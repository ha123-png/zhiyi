"""A restored batch is an explicit ID set, not the newest history page."""

from datetime import datetime, timedelta, timezone

import pytest

from test_tasks_api import _add_task, client as tasks_client_fixture
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.schemas.file_export import TaskExportState
from document_pipeline_api.services.tasks import list_tasks


client = tasks_client_fixture
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_id_query_finds_old_processing_task_and_its_later_completion(client):
    with client.app.state.session_factory() as session:
        _add_task(session, "old-batch-file", status="processing", created_at=BASE)
        for index in range(205):
            _add_task(session, f"unrelated-{index:03}", status="completed",
                      created_at=BASE + timedelta(minutes=index + 1))
        session.commit()

    recent = client.get("/api/v1/tasks", params={"limit": 200}).json()
    assert len(recent) == 200
    assert "old-batch-file" not in {task["id"] for task in recent}
    selected = client.get("/api/v1/tasks", params={"task_ids": "old-batch-file"})
    assert selected.status_code == 200
    assert [(task["id"], task["status"]) for task in selected.json()] == [
        ("old-batch-file", "processing"),
    ]
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, "old-batch-file")
        task.status = "completed"
        task.updated_at = BASE + timedelta(days=1)
        session.commit()
    selected = client.get("/api/v1/tasks", params={"task_ids": "old-batch-file"})
    assert [(task["id"], task["status"]) for task in selected.json()] == [
        ("old-batch-file", "completed"),
    ]


def test_id_query_deduplicates_and_keeps_existing_sort_and_pagination(client):
    with client.app.state.session_factory() as session:
        for identifier in ["selected-a", "selected-b", "unrelated-z"]:
            _add_task(session, identifier, status="completed", created_at=BASE)
        session.commit()
        assert list_tasks(session, task_ids=[]) == []
    params = [("task_ids", identifier) for identifier in [
        "selected-a", "missing", "selected-a", "selected-b",
    ]]
    assert [task["id"] for task in client.get("/api/v1/tasks", params=params).json()] == [
        "selected-b", "selected-a",
    ]
    page = client.get("/api/v1/tasks", params=[*params, ("limit", "1"), ("offset", "1")])
    assert [task["id"] for task in page.json()] == ["selected-a"]
    assert client.get("/api/v1/tasks", params={"task_ids": "missing"}).json() == []


@pytest.mark.parametrize(("filters", "expected"), [
    ({"status": "queued"}, ["selected-a"]),
    ({"active_only": "true"}, ["selected-a"]),
    ({"export_pending": "true"}, ["selected-d"]),
    ({"search": "target%"}, ["selected-a"]),
    ({"template_id": "builtin-delivery"}, ["selected-c"]),
    ({"since": (BASE + timedelta(days=2)).isoformat()}, ["selected-d", "selected-c", "selected-b"]),
    ({"status": "completed", "search": "target", "template_id": "builtin-invoice",
      "since": (BASE + timedelta(days=1)).isoformat()}, ["selected-d", "selected-b"]),
])
def test_id_query_intersects_existing_filters(client, filters, expected):
    with client.app.state.session_factory() as session:
        for index, (identifier, status, name, template) in enumerate([
            ("selected-a", "queued", "target%.png", "builtin-invoice"),
            ("selected-b", "completed", "target.png", "builtin-invoice"),
            ("selected-c", "failed", "other.png", "builtin-delivery"),
            ("selected-d", "completed", "target.png", "builtin-invoice"),
            ("unrelated-e", "queued", "target%.png", "builtin-invoice"),
        ]):
            _add_task(session, identifier, status=status, created_at=BASE + timedelta(minutes=index))
            task = session.get(TaskRecord, identifier)
            task.filename = name
            task.template_id = template
            task.updated_at = BASE + timedelta(days=index + 1)
            if identifier == "selected-d":
                task.export_state_json = TaskExportState(status="failed").model_dump_json()
        session.commit()
    params = [("task_ids", f"selected-{suffix}") for suffix in "abcd"]
    params.extend(filters.items())
    response = client.get("/api/v1/tasks", params=params)
    assert response.status_code == 200
    assert [task["id"] for task in response.json()] == expected


def test_id_query_accepts_full_bound_without_truncating_identifiers(client):
    identifiers = [f"{index:03}" + "x" * 61 for index in range(100)]
    with client.app.state.session_factory() as session:
        for identifier in identifiers:
            _add_task(session, identifier, status="paused", created_at=BASE)
        session.commit()
    response = client.get("/api/v1/tasks", params=[("task_ids", value) for value in identifiers])
    assert len(str(response.request.url).encode("ascii")) < 8192
    assert response.status_code == 200
    assert {task["id"] for task in response.json()} == set(identifiers)


@pytest.mark.parametrize("identifiers", [
    [""], ["x" * 65], ["duplicate"] * 101, [f"task-{index}" for index in range(101)],
])
def test_id_query_rejects_invalid_or_oversized_scope(client, identifiers):
    response = client.get("/api/v1/tasks", params=[("task_ids", value) for value in identifiers])
    assert response.status_code == 422
