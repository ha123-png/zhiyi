"""No cloud calls: duplicate rejection must happen before queue publication."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from threading import Barrier

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.main import create_app
from document_pipeline_api.migrations import _alembic_config, upgrade_database
from document_pipeline_api.models import DataRowRecord, TaskRecord
from document_pipeline_api.services.native_files import consume_import_ticket, issue_import_ticket
from document_pipeline_api.storage_paths import resolve_task_storage_path


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}",
                        storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as client:
        yield client


def upload(client, **data):
    return client.post("/api/v1/tasks", files={"file": ("sample.txt", b"same original", "text/plain")}, data=data)


def test_duplicate_confirmation_checks_content_when_a_file_is_selected_again(client):
    original = upload(client).json()
    conflict = upload(client).json()["detail"]
    assert conflict["duplicate_sha256"] == original["sha256"]
    wrong = client.post("/api/v1/tasks", files={"file": ("sample.txt", b"diff original", "text/plain")},
                        data={"duplicate_policy": "allow", "expected_sha256": conflict["duplicate_sha256"]})
    assert wrong.status_code == 409
    assert len(client.get("/api/v1/tasks").json()) == 1
    assert upload(client, duplicate_policy="allow", expected_sha256=conflict["duplicate_sha256"]).status_code == 201


def test_pending_duplicate_reuses_verified_original_and_only_queues_once(client):
    original = upload(client).json()
    pending = "/api/v1/user-state/pending-duplicates"
    assert client.put(pending, json={
        "id": "reuse-original", "name": original["filename"], "size": original["size_bytes"], "selection": "smart",
        "existingTaskId": original["id"], "sha256": original["sha256"], "createdAt": original["created_at"],
    }).is_success
    action = "/api/v1/tasks/pending-duplicates/reuse-original/extract"
    first = client.post(action)
    assert first.status_code == 201, first.text
    assert first.json()["sha256"] == original["sha256"]
    assert first.json()["id"] != original["id"]
    assert client.get(pending).json() == []
    assert client.post(action).status_code == 404
    assert len(client.get("/api/v1/tasks").json()) == 2


def test_missing_original_keeps_pending_and_batch_delete_keeps_existing_record(client):
    original = upload(client).json()
    pending = "/api/v1/user-state/pending-duplicates"
    assert client.put(pending, json={
        "id": "missing-original", "name": original["filename"], "size": original["size_bytes"], "selection": "smart",
        "existingTaskId": original["id"], "sha256": original["sha256"], "createdAt": original["created_at"],
    }).is_success
    basename = f"{original['id']}.txt"
    (client.app.state.settings.storage_dir / basename).unlink()
    response = client.post("/api/v1/tasks/pending-duplicates/missing-original/extract")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "original_unavailable"
    assert len(client.get(pending).json()) == 1
    assert client.post("/api/v1/tasks/batch-delete", json={"pending_ids": ["missing-original"]}).is_success
    assert client.get(pending).json() == []
    assert [task["id"] for task in client.get("/api/v1/tasks").json()] == [original["id"]]


@pytest.mark.parametrize("invalid", [{"template_mode": "manual"}, {"template_mode": "bogus"},
                                      {"template_mode": "bogus", "template_id": "builtin-invoice"}])
def test_invalid_modes_cannot_create_semantically_identical_smart_task(client, tmp_path, invalid):
    assert upload(client).status_code == 201
    rejected = upload(client, **invalid)
    assert rejected.status_code == 422, rejected.text
    assert len(client.get("/api/v1/tasks").json()) == 1
    assert len(list((tmp_path / "uploads").rglob("*.txt"))) == 1
    assert not list((tmp_path / "uploads").glob("*.uploading"))


def test_legacy_builtin_mode_and_explicit_template_share_canonical_intent(client):
    assert upload(client, template_mode="invoice").status_code == 201
    assert upload(client, template_mode="manual", template_id="builtin-invoice").status_code == 409
    assert upload(client, template_mode="delivery").status_code == 201
    assert upload(client, template_id="builtin-delivery").status_code == 409


@pytest.mark.parametrize("status", ["queued", "processing", "paused", "waiting_for_template", "needs_review", "completed"])
def test_duplicate_blocked_before_queue_no_files_or_tasks_leak(client, tmp_path, monkeypatch, status):
    first = upload(client).json()
    with client.app.state.session_factory() as session:
        session.get(TaskRecord, first["id"]).status = status
        session.commit()
    calls = []
    client.app.state.settings = replace(client.app.state.settings, queue_enabled=True)
    monkeypatch.setattr("document_pipeline_api.services.queueing.enqueue_task", lambda *a: calls.append(a))
    result = upload(client)
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["duplicate_status"] == status
    assert calls == []
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TaskRecord)) == 1
    assert len(list((tmp_path / "uploads").rglob("*.txt"))) == 1
    assert list((tmp_path / "uploads").glob("*.uploading")) == []


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_and_cancelled_reupload_allowed(client, status):
    first = upload(client).json()
    with client.app.state.session_factory() as session:
        session.get(TaskRecord, first["id"]).status = status
        session.commit()
    assert upload(client).status_code == 201


def test_cancelled_review_with_retained_facts_still_requires_duplicate_confirmation(client):
    from test_extraction_service import StubModelClient
    from document_pipeline_api.services.extraction import process_task
    first = upload(client, template_mode="invoice").json()
    with client.app.state.session_factory() as session:
        process_task(session, client.app.state.settings, first["id"],
                     client=StubModelClient(total_amount=999))
        assert session.get(TaskRecord, first["id"]).status == "needs_review"
        rows = session.scalar(select(func.count()).select_from(DataRowRecord))
        assert rows > 0
    assert client.post(f"/api/v1/tasks/{first['id']}/cancel").status_code == 200
    duplicate = upload(client, template_mode="invoice")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["duplicate_status"] == "cancelled"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DataRowRecord)) == rows
        assert session.scalar(select(func.count()).select_from(TaskRecord)) == 1
    assert upload(client, template_mode="invoice", duplicate_policy="allow").status_code == 201


def test_different_template_version_target_and_explicit_repeat(client):
    from test_templates_api import _editable_body
    first = upload(client, template_mode="invoice").json()
    assert upload(client, template_mode="delivery").status_code == 201
    assert upload(client, template_mode="invoice", target_table_id="separate-target").status_code == 201
    template = client.get("/api/v1/templates/builtin-invoice").json()
    updated = client.put("/api/v1/templates/builtin-invoice", json={
        **_editable_body(template), "expected_version": template["version"],
        "extra_instructions": "A deliberate new extraction requirement",
    })
    assert updated.status_code == 200, updated.text
    new_version = upload(client, template_mode="invoice")
    assert new_version.status_code == 201
    assert new_version.json()["template_version"] != first["template_version"]
    repeated = upload(client, template_mode="invoice", duplicate_policy="allow")
    assert repeated.status_code == 201
    assert repeated.json()["duplicate_of_task_id"]
    assert upload(client, duplicate_policy="typo").status_code == 422


def test_manual_original_intent_survives_reselection_and_retry(client):
    first = upload(client, template_mode="invoice").json()
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, first["id"])
        original_intent = task.upload_intent_json
        task.status = "waiting_for_template"
        session.commit()
    selected = client.post(f"/api/v1/tasks/{first['id']}/template", json={"template_id": "builtin-delivery"})
    assert selected.status_code == 200
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, first["id"])
        assert task.upload_intent_json == original_intent
        task.status = "failed"
        session.commit()
    assert client.post(f"/api/v1/tasks/{first['id']}/retry?use_current_settings=true").status_code == 200
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, first["id"]).upload_intent_json == original_intent
    assert upload(client, template_mode="invoice").status_code == 409
    assert upload(client, template_mode="delivery").status_code == 201


@pytest.mark.parametrize("legacy", [False, True])
def test_smart_intent_survives_matching_and_target_change(client, legacy):
    first = upload(client).json()
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, first["id"])
        task.template_id = "builtin-invoice"
        task.template_version = 1
        task.status = "completed"
        if legacy:
            task.upload_intent_json = None
        else:
            task.target_table_id = "later-selected-target"
        session.commit()
    assert upload(client).status_code == 409
    assert upload(client, template_mode="invoice").status_code == 201


def test_concurrent_guard_is_atomic_at_insert(client, tmp_path, monkeypatch):
    from document_pipeline_api.services import tasks
    original = tasks._insert_task_if_capacity_available
    barrier = Barrier(2)
    def synchronized(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(tasks, "_insert_task_if_capacity_available", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        # Independent request event loops so the synchronous insertion barrier
        # forces an actual competing DB write, rather than blocking one loop.
        results = list(pool.map(lambda _: upload(TestClient(client.app)), range(2)))
    assert sorted(result.status_code for result in results) == [201, 409]
    assert len(client.get("/api/v1/tasks").json()) == 1
    assert len(list((tmp_path / "uploads").rglob("*.txt"))) == 1
    assert list((tmp_path / "uploads").glob("*.uploading")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows native capabilities")
@pytest.mark.parametrize("change_source", [False, True])
def test_native_duplicate_rotates_ticket_and_preserves_authority(client, tmp_path, change_source):
    source = tmp_path / "sample.txt"
    source.write_bytes(b"same original")
    upload(client)
    old = issue_import_ticket(tmp_path, source)["token"]
    old_authority = json.loads((tmp_path / "native-imports" / f"{old}.json").read_text(encoding="utf-8"))
    result = client.post("/api/v1/tasks/native-import", json={"token": old})
    assert result.status_code == 409, result.text
    new = result.json()["detail"]["retry_token"]
    assert new != old and str(source) not in result.text
    assert json.loads((tmp_path / "native-imports" / f"{new}.json").read_text(encoding="utf-8")) == old_authority
    assert client.post("/api/v1/tasks/native-import", json={"token": old, "duplicate_policy": "allow"}).status_code == 422
    if change_source:
        source.write_bytes(b"changed file after confirmation")
    confirmed = client.post("/api/v1/tasks/native-import", json={"token": new, "duplicate_policy": "allow"})
    assert confirmed.status_code == (422 if change_source else 201), confirmed.text
    assert client.post("/api/v1/tasks/native-import", json={"token": new, "duplicate_policy": "allow"}).status_code == 422
    assert not list((tmp_path / "native-imports").glob("*.claimed"))


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic rename")
def test_losing_ticket_consumer_cannot_delete_winner_claim(tmp_path):
    from document_pipeline_api.services.file_copies import CopyExportError
    source = tmp_path / "sample.txt"
    source.write_bytes(b"same original")
    token = issue_import_ticket(tmp_path, source)["token"]
    claimed = tmp_path / "native-imports" / f"{token}.claimed"
    with consume_import_ticket(tmp_path, token) as (handle, _):
        assert claimed.exists()
        with pytest.raises(CopyExportError):
            with consume_import_ticket(tmp_path, token):
                pytest.fail("ticket consumed twice")
        assert claimed.exists()
        assert handle.read() == b"same original"
    assert not claimed.exists()


def test_upgrade_from_0045_preserves_all_existing_facts_and_original(client):
    from test_integration_api import _seed_confirmed_task
    _seed_confirmed_task(client)
    first = upload(client).json()
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, first["id"])
        original_path = resolve_task_storage_path(settings.storage_dir, task)
        original_bytes = original_path.read_bytes()
    engine = build_engine(settings.database_url)
    with engine.begin() as connection:
        config = _alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "0045_assistant_authorization")
    def dump():
        with engine.connect() as connection:
            tables = [r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version' ORDER BY name")]
            data = {}
            for name in tables:
                rows = connection.exec_driver_sql(f'SELECT * FROM "{name}"').mappings()
                data[name] = [
                    {k: v for k, v in row.items() if k not in {"upload_intent_json", "merge_sources_json"}}
                    for row in rows
                ]
            return data
    before = dump()
    assert before["extractions"] and before["data_rows"] and before["file_current_data"]
    upgrade_database(engine)
    assert dump() == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM data_tables WHERE merge_sources_json IS NOT NULL"
        ).scalar_one() == 0
    assert original_path.read_bytes() == original_bytes
    assert upload(client).status_code == 409  # Legacy smart fallback.
    engine.dispose()


def test_mcp_same_default_and_explicit_confirmation(client, tmp_path):
    from document_pipeline_api.mcp_server import create_mcp_server
    source = tmp_path / "sample.txt"
    source.write_bytes(b"same original")
    server = create_mcp_server(client.app.state.settings, file_access_enabled=True, allowed_file_roots=(tmp_path,))
    args = {"file_path": str(source)}
    asyncio.run(server.call_tool("create_task_from_file", args))
    with pytest.raises(Exception, match="duplicate_upload"):
        asyncio.run(server.call_tool("create_task_from_file", args))
    asyncio.run(server.call_tool("create_task_from_file", {**args, "duplicate_policy": "allow"}))
    assert len(client.get("/api/v1/tasks").json()) == 2


def test_integration_http_shares_duplicate_contract(tmp_path):
    from test_integration_api import _settings, _headers, WRITE_TOKEN
    with TestClient(create_app(_settings(tmp_path))) as client:
        def send(**data):
            return client.post("/api/integration/v1/tasks", headers=_headers(WRITE_TOKEN),
                               files={"file": ("sample.txt", b"same original", "text/plain")}, data=data)
        first = send()
        assert first.status_code == 201
        duplicate = send()
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["duplicate_task_id"] == first.json()["id"]
        assert send(duplicate_policy="allow").status_code == 201
