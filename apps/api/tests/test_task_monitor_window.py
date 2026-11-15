"""Display-only terminal window: real API/SQLite, no worker or model transport."""

from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord


CUTOFF = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'monitor.db'}",
                        storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as value:
        yield value


def add_task(session, task_id, status, *, updated=None, export=None, completed=None):
    session.add(TaskRecord(
        id=task_id, filename=f"{task_id}.txt", content_type="text/plain", size_bytes=1,
        sha256=task_id, storage_path=f"{task_id}.txt", template_mode="smart", status=status,
        export_state_json=json.dumps(export) if export else None,
        created_at=CUTOFF - timedelta(days=30),
        updated_at=updated or CUTOFF - timedelta(microseconds=1), completed_at=completed,
    ))


def ids(client, **params):
    response = client.get("/api/v1/tasks", params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()]


def counts(client, **params):
    response = client.get("/api/v1/tasks/summary", params=params)
    assert response.status_code == 200, response.text
    return response.json()["group_counts"]


def test_old_unfinished_failed_and_actionable_exports_survive_terminal_window(client):
    expected = {
        "active": {"created", "queued", "processing", "validating", "paused"},
        "pending": {"waiting_for_template", "needs_review"},
        "completed": set(), "failed": {"failed"}, "cancelled": set(),
    }
    with client.app.state.session_factory() as session:
        for status in ["created", "queued", "processing", "validating", "paused",
                       "waiting_for_template", "needs_review", "failed", "completed", "cancelled"]:
            add_task(session, status, status)
        for mode in ("copy", "move"):
            for export_status in ("disabled", "awaiting_confirmation", "pending", "exporting",
                                  "failed", "needs_rebind", "completed", "skipped"):
                task_id = f"{mode}-{export_status}"
                add_task(session, task_id, "completed", export={"mode": mode, "status": export_status})
                if export_status in ("awaiting_confirmation", "pending", "exporting"):
                    expected["active"].add(task_id)
                elif export_status in ("failed", "needs_rebind"):
                    expected["pending"].add(task_id)
        session.commit()
        before = session.scalar(select(func.count()).select_from(TaskRecord))

    query = {"terminal_since": CUTOFF.isoformat()}
    assert counts(client, **query) == {"all": 18, **{key: len(value) for key, value in expected.items()}}
    for group, expected_ids in expected.items():
        assert set(ids(client, group=group, **query)) == expected_ids
    assert set(ids(client, **query)) == set().union(*expected.values())
    assert len(ids(client)) == before == 26  # No caller opt-in means no display window.
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TaskRecord)) == before


@pytest.mark.parametrize("cutoff", ["2026-11-01T08:00:00Z", "2026-11-01T16:00:00+08:00", "2026-11-01T08:00:00"])
def test_cutoff_is_inclusive_utc_and_uses_latest_update_not_creation_or_extraction(client, cutoff):
    with client.app.state.session_factory() as session:
        add_task(session, "before", "completed")
        add_task(session, "boundary", "completed", updated=CUTOFF,
                 completed=CUTOFF - timedelta(days=20))
        add_task(session, "legacy-recent", "completed", updated=CUTOFF + timedelta(hours=1))
        add_task(session, "cancelled-boundary", "cancelled", updated=CUTOFF)
        add_task(session, "failed-old", "failed")
        session.commit()
    query = {"terminal_since": cutoff}
    assert set(ids(client, **query)) == {"boundary", "legacy-recent", "cancelled-boundary", "failed-old"}
    assert counts(client, **query) == {"all": 4, "active": 0, "pending": 0, "completed": 2,
                                      "failed": 1, "cancelled": 1}
    # Existing historical `since` filters every state, including old failures.
    assert set(ids(client, since=cutoff)) == {"boundary", "legacy-recent", "cancelled-boundary"}


def test_window_search_group_and_pagination_share_database_scope(client):
    with client.app.state.session_factory() as session:
        for index in range(63):
            add_task(session, f"match-{index:03}", "completed", updated=CUTOFF)
        add_task(session, "match-old", "completed")
        add_task(session, "match-failed", "failed")
        add_task(session, "other", "completed", updated=CUTOFF)
        session.commit()
    query = {"terminal_since": CUTOFF.isoformat(), "search": "match"}
    assert counts(client, **query) == {"all": 64, "active": 0, "pending": 0, "completed": 63,
                                      "failed": 1, "cancelled": 0}
    first = ids(client, group="completed", limit=50, **query)
    last = ids(client, group="completed", limit=50, offset=50, **query)
    assert len(first) == 50 and len(last) == 13 and set(first).isdisjoint(last)
    assert first + last == [f"match-{index:03}" for index in reversed(range(63))]
    assert ids(client, task_ids=["match-old", "match-failed"], **query) == ["match-failed"]


def test_export_reactivation_remains_visible_then_uses_its_new_update(client):
    with client.app.state.session_factory() as session:
        add_task(session, "reactivated", "completed")
        session.commit()
    query = {"terminal_since": CUTOFF.isoformat()}
    assert ids(client, **query) == []
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, "reactivated")
        task.export_state_json = json.dumps({"mode": "copy", "status": "failed"})
        task.updated_at = CUTOFF - timedelta(days=2)
        session.commit()
    assert ids(client, group="pending", **query) == ["reactivated"]
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, "reactivated")
        task.export_state_json = json.dumps({"mode": "copy", "status": "completed"})
        task.updated_at = CUTOFF + timedelta(hours=1)
        session.commit()
    assert ids(client, group="completed", **query) == ["reactivated"]
    assert counts(client, **query)["completed"] == 1


@pytest.mark.parametrize("path", ["/api/v1/tasks", "/api/v1/tasks/summary"])
def test_invalid_window_cutoff_is_rejected_without_changing_records(client, path):
    response = client.get(path, params={"terminal_since": "not-a-date"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "terminal_since"
