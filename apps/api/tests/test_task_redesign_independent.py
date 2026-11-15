"""Independent task-list acceptance: real API/SQLite, bounded synthetic data."""

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from document_pipeline_api.api import tasks as task_api
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import ExtractionRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.schemas.extraction import DocumentExtraction
from document_pipeline_api.services.current_data import revise_current
from document_pipeline_api.services.data_tables import materialize_task_result


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'independent-task-review.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as value:
        yield value


def _task(task_id, status, export=None):
    return TaskRecord(
        id=task_id,
        filename=f"{task_id}.txt",
        content_type="text/plain",
        size_bytes=1,
        sha256=task_id,
        storage_path=f"{task_id}.txt",
        template_mode="smart",
        status=status,
        export_state_json=json.dumps(export) if export else None,
        created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )


def test_group_counts_and_pages_cover_more_than_a_thousand_real_tasks(client):
    # Expected categories encode the user-facing outcome, not the SQL predicate.
    cases = [
        ("queued", None, "active"),
        ("waiting_for_template", None, "pending"),
        ("needs_review", None, "pending"),
        ("failed", None, "failed"),
        ("cancelled", None, "cancelled"),
        ("completed", None, "completed"),
        ("completed", {"mode": "copy", "status": "failed"}, "pending"),
        ("completed", {"mode": "move", "status": "needs_rebind"}, "pending"),
        ("completed", {"mode": "copy", "status": "pending"}, "active"),
        ("completed", {"mode": "move", "status": "exporting"}, "active"),
        ("completed", {"mode": "copy", "status": "completed"}, "completed"),
        ("completed", {"mode": "move", "status": "skipped"}, "completed"),
    ]
    expected = {group: set() for group in ("active", "pending", "completed", "failed", "cancelled")}
    with client.app.state.session_factory() as session:
        for index in range(1007):
            status, export, group = cases[index % len(cases)]
            task_id = f"independent-{index:04}"
            session.add(_task(task_id, status, export))
            expected[group].add(task_id)
        session.commit()

    response = client.get("/api/v1/tasks/summary")
    assert response.status_code == 200
    counts = response.json()["group_counts"]
    assert counts == {"all": 1007, **{group: len(ids) for group, ids in expected.items()}}

    seen = set()
    for group, expected_ids in expected.items():
        returned = []
        for offset in range(0, len(expected_ids), 37):
            page = client.get("/api/v1/tasks", params={"group": group, "limit": 37, "offset": offset})
            assert page.status_code == 200, page.text
            returned.extend(row["id"] for row in page.json())
        assert len(returned) == len(expected_ids)
        assert set(returned) == expected_ids
        assert returned == sorted(expected_ids, reverse=True)
        assert seen.isdisjoint(returned)
        seen.update(returned)
    assert len(seen) == 1007

    # The unfiltered route also spans the hard 1000-row request ceiling.
    first = client.get("/api/v1/tasks", params={"group": "all", "limit": 1000, "offset": 0})
    last = client.get("/api/v1/tasks", params={"group": "all", "limit": 1000, "offset": 1000})
    assert first.status_code == last.status_code == 200
    all_ids = [row["id"] for row in first.json() + last.json()]
    assert len(all_ids) == 1007
    assert set(all_ids) == seen
    assert len(set(all_ids)) == 1007


def test_summary_remains_internally_consistent_when_a_task_changes_between_queries(client):
    with client.app.state.session_factory() as session:
        session.add(_task("changing-status", "queued"))
        session.commit()
        engine = session.get_bind()

    observed = {"writer_committed": False}

    def after_first_status_read(_connection, _cursor, statement, _parameters, _context, _many):
        if observed["writer_committed"] or "group by tasks.status" not in statement.lower():
            return
        observed["writer_committed"] = True
        # WAL allows an independent writer to commit while this API request is reading.
        with Session(engine) as writer:
            writer.execute(update(TaskRecord).where(TaskRecord.id == "changing-status").values(status="failed"))
            writer.commit()

    event.listen(engine, "after_cursor_execute", after_first_status_read)
    try:
        first = client.get("/api/v1/tasks/summary")
    finally:
        event.remove(engine, "after_cursor_execute", after_first_status_read)
    assert first.status_code == 200, first.text
    assert observed["writer_committed"]
    old = first.json()
    assert (old["active"], old["failed"]) == (1, 0)
    assert old["group_counts"] == {"all": 1, "active": 1, "pending": 0, "completed": 0, "failed": 0, "cancelled": 0}

    new = client.get("/api/v1/tasks/summary").json()
    assert (new["active"], new["failed"]) == (0, 1)
    assert new["group_counts"] == {"all": 1, "active": 0, "pending": 0, "completed": 0, "failed": 1, "cancelled": 0}


def _extraction():
    return DocumentExtraction(
        document_type="invoice", seller_name="Synthetic seller", buyer_name="Synthetic buyer",
        document_number="SYNTHETIC-1", document_date="2026-11-02",
        amount_before_tax=100, tax_amount=6, total_amount=106,
        items=[{"name": "Synthetic item", "specification": "Synthetic", "unit": "item",
                "quantity": 1, "unit_price": 100, "amount": 100, "tax_amount": 6, "tax_rate": "6%"}],
    )


def test_legacy_target_confirmation_serializes_against_a_new_review(client, monkeypatch):
    task_id = "independent-legacy-target"
    with client.app.state.session_factory() as session:
        # Old records can lack a structured pending_reason even though a
        # materialized result has to be confirmed, not extracted a second time.
        task = _task(task_id, "waiting_for_template")
        session.add(task)
        session.flush()
        extraction = ExtractionRecord(
            task_id=task_id, document_kind="invoice", model_name="synthetic",
            prompt_version="synthetic", elapsed_seconds=0,
            result_json=_extraction().model_dump_json(), validation_json="[]",
        )
        session.add(extraction)
        session.flush()
        materialize_task_result(session, task_id)
        current = session.get(FileCurrentDataRecord, task_id)
        table_id = current.table_id
        original_result = extraction.result_json
        session.commit()
        engine = session.get_bind()

    row = client.get("/api/v1/tasks", params={"group": "pending"}).json()[0]
    assert (row["pending_action"], row["pending_review_version"]) == ("confirm_target_table", 0)
    expected_payload = {"target_table_id": table_id, "expected_review_version": 0}
    real_confirm = task_api.confirm_task
    observed = {"attempted": False, "serialized": False}

    def confirm_with_competing_edit(session, *args, **kwargs):
        observed["attempted"] = True
        try:
            with Session(engine) as writer:
                writer.connection().exec_driver_sql("PRAGMA busy_timeout=0")
                revise_current(writer, task_id, expected_version=0,
                               header_changes={"seller_name": "Concurrent edit"})
                writer.commit()
        except OperationalError as error:
            assert getattr(error.orig, "sqlite_errorcode", 0) & 0xFF in {5, 6}
            observed["serialized"] = True
        return real_confirm(session, *args, **kwargs)

    monkeypatch.setattr(task_api, "confirm_task", confirm_with_competing_edit)
    response = client.post(f"/api/v1/tasks/{task_id}/template", json=expected_payload)
    assert response.status_code == 200, response.text
    assert observed == {"attempted": True, "serialized": True}
    edit = client.patch(f"/api/v1/tasks/{task_id}/current", json={
        "expected_version": 0, "header_changes": {"seller_name": "Concurrent edit"},
    })
    assert edit.status_code == 200, edit.text
    stale = client.post(f"/api/v1/tasks/{task_id}/template", json=expected_payload)
    assert stale.status_code == 409
    with Session(engine) as session:
        assert session.get(FileCurrentDataRecord, task_id).current_version == 1
        assert session.get(TaskRecord, task_id).status == "completed"
        assert json.loads(session.get(FileCurrentDataRecord, task_id).header_json)["seller_name"] == "Concurrent edit"
        assert session.get(ExtractionRecord, task_id).result_json == original_result
