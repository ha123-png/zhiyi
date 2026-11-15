"""Exercise real batch TaskRead consumers without model calls or live services."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from document_pipeline_api.business_backup import BusinessBackupError, _prepare_staged_database
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.mcp_server import create_mcp_server
from document_pipeline_api.models import FileCurrentDataRecord, TaskRecord
from document_pipeline_api.schemas.input_scope import InputPolicy, InputScope, OmittedRange, SourceRange
from document_pipeline_api.storage_paths import task_storage_name


READ_TOKEN = "synthetic-read-" + "a" * 40


@contextmanager
def pending_read_queries():
    queries = []
    def collect(_connection, _cursor, statement, _parameters, _context, _many):
        sql = " ".join(statement.lower().split())
        if sql.startswith("select") and (" from extractions " in sql or " from file_current_data " in sql):
            queries.append(sql)
    event.listen(Engine, "before_cursor_execute", collect)
    try:
        yield queries
    finally:
        event.remove(Engine, "before_cursor_execute", collect)


def seed_waiting(client, count, *, write_originals=False):
    policy = InputPolicy(allow_limited_input=False).model_dump_json()
    plan = InputScope(
        rule="prefix_v1", coverage="partial", selected=[SourceRange(kind="line", start=1, end=1)],
        omitted=[OmittedRange(location=SourceRange(kind="line", start=2, end=5))],
        selected_units=1, total_units=5, text_characters=1, text_budget=1, image_budget=0,
    ).model_dump_json()
    manifest = {"files": []}
    with client.app.state.session_factory() as session:
        for index in range(count):
            task_id = f"consumer-{index:04d}"
            content = f"synthetic-{index}".encode()
            digest = hashlib.sha256(content).hexdigest()
            task = TaskRecord(
                id=task_id, filename=f"{task_id}.txt", content_type="text/plain",
                size_bytes=len(content), sha256=digest,
                storage_path=task_storage_name(task_id, "text/plain"), status="waiting_for_template",
                template_mode="smart", pending_reason=None,
                # Text deliberately resembles the old classifier for every row.
                candidate_templates_json=json.dumps([{"id": "builtin-invoice", "version": 1,
                                                     "name": "Synthetic", "description": "指定表不符"}]),
                created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
                updated_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
            )
            if index % 3 == 1:
                task.input_policy_json, task.input_plan_json = policy, plan
            session.add(task)
            session.flush()
            if index % 3 == 2:
                session.add(FileCurrentDataRecord(task_id=task_id, current_version=7))
            if write_originals:
                path = client.app.state.settings.storage_dir / task.storage_path
                path.write_bytes(content)
            manifest["files"].append({"task_id": task_id, "content_type": "text/plain",
                                      "size_bytes": len(content), "sha256": digest})
        session.commit()
    return manifest


def tool_payload(raw):
    if isinstance(raw, tuple):
        raw = raw[1] or raw[0]
    if isinstance(raw, dict):
        return raw.get("result", raw)
    if raw and isinstance(raw[0], dict) and "id" in raw[0]:
        return raw
    data = json.loads(raw[0].text)
    return data.get("result", data) if isinstance(data, dict) else data


def test_integration_list_batches_metadata_and_keeps_read_permissions(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'integration.db'}",
                        storage_dir=tmp_path / "uploads", integration_read_token=READ_TOKEN)
    headers = {"Authorization": f"Bearer {READ_TOKEN}"}
    with TestClient(create_app(settings)) as client:
        seed_waiting(client, 40)
        expected = client.get("/api/v1/tasks").json()
        with pending_read_queries() as queries:
            result = client.get("/api/integration/v1/tasks", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json() == expected
        assert len(queries) == 2
        assert {row["pending_action"] for row in result.json()} == {
            "select_template", "confirm_input_scope", "confirm_target_table",
        }
        assert client.get("/api/integration/v1/tasks").status_code == 401
        assert client.post("/api/integration/v1/tasks", headers=headers,
                           files={"file": ("unused.txt", b"synthetic", "text/plain")}).status_code == 403


def test_mcp_list_batches_metadata_without_changing_filters_or_limits(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'mcp.db'}", storage_dir=tmp_path / "uploads")
    with TestClient(create_app(settings)) as client:
        seed_waiting(client, 40)
        expected = client.get("/api/v1/tasks", params={"limit": 7, "offset": 9, "status": "waiting_for_template"}).json()
    server = create_mcp_server(settings, task_read_enabled=True)
    with pending_read_queries() as queries:
        result = tool_payload(asyncio.run(server.call_tool("list_tasks", {"limit": 7, "offset": 9, "status": "waiting_for_template"})))
    assert result == expected
    assert len(queries) == 2
    with pending_read_queries() as full_queries:
        full = tool_payload(asyncio.run(server.call_tool("list_tasks", {"limit": 40})))
    assert len(full) == 40 and len(full_queries) == 2
    with pytest.raises(Exception, match="limit"):
        asyncio.run(server.call_tool("list_tasks", {"limit": 501}))
    read_disabled = create_mcp_server(settings)
    assert "list_tasks" not in {tool.name for tool in asyncio.run(read_disabled.list_tools())}
    with pytest.raises(Exception):
        asyncio.run(read_disabled.call_tool("list_tasks", {}))


@pytest.mark.parametrize("count, expected_queries", [(1, 2), (201, 6)])
def test_staged_backup_validation_batches_every_hundred_records(tmp_path, count, expected_queries):
    database = tmp_path / "stage.db"
    settings = Settings(database_url=f"sqlite:///{database}", storage_dir=tmp_path / "uploads")
    with TestClient(create_app(settings)) as client:
        manifest = seed_waiting(client, count, write_originals=True)
    with pending_read_queries() as queries:
        _prepare_staged_database(database, settings.storage_dir, manifest)
    assert len(queries) == expected_queries
    assert len(list(settings.storage_dir.glob("*.txt"))) == count


@pytest.mark.parametrize("malformed", ["candidate", "policy"])
def test_staged_backup_still_rejects_invalid_record_in_later_batch(tmp_path, malformed):
    database = tmp_path / "stage.db"
    settings = Settings(database_url=f"sqlite:///{database}", storage_dir=tmp_path / "uploads")
    with TestClient(create_app(settings)) as client:
        manifest = seed_waiting(client, 151, write_originals=True)
        with client.app.state.session_factory() as session:
            task = session.get(TaskRecord, "consumer-0150")
            if malformed == "candidate":
                task.candidate_templates_json = '[{"id":"missing-required-fields"}]'
            else:
                task.input_policy_json = '{"row_limit":0}'
            session.commit()
    with pytest.raises(BusinessBackupError, match="任务业务结构无效"):
        _prepare_staged_database(database, settings.storage_dir, manifest)
    assert len(list(settings.storage_dir.glob("*.txt"))) == 151
