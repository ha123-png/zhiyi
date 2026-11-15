"""Task list/recovery contracts, using isolated records and no model transport."""

from datetime import datetime, timezone
from dataclasses import replace
import json

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import ExtractionRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.schemas.extraction import DocumentExtraction
from document_pipeline_api.schemas.input_scope import InputPolicy, InputScope, OmittedRange, SourceRange
from document_pipeline_api.schemas.tasks import TaskRead
from document_pipeline_api.services.data_tables import materialize_task_result
from document_pipeline_api.services.task_presentation import pending_details_for_tasks
from document_pipeline_api.services import tasks as task_service


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'task-contract.db'}",
                        storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as value:
        yield value


def add_task(session, task_id, *, status="waiting_for_template", reason=None, export=None):
    task = TaskRecord(
        id=task_id, filename=f"{task_id}.txt", content_type="text/plain", size_bytes=1,
        sha256=task_id, storage_path=f"{task_id}.txt", template_mode="manual",
        template_id="builtin-invoice", template_version=1, status=status,
        pending_reason=reason, export_state_json=json.dumps(export) if export else None,
        created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    session.add(task)
    session.flush()
    return task


def add_result(session, task):
    result = DocumentExtraction(
        document_type="invoice", seller_name="Synthetic seller", buyer_name="Synthetic buyer",
        document_number="SYNTHETIC-1", document_date="2026-11-02",
        amount_before_tax=100, tax_amount=6, total_amount=106,
        items=[{"name": "Synthetic item", "specification": "Synthetic", "unit": "item", "quantity": 1, "unit_price": 100,
                "amount": 100, "tax_amount": 6, "tax_rate": "6%"}],
    )
    session.add(ExtractionRecord(
        task_id=task.id, document_kind="invoice", model_name="synthetic-record",
        prompt_version="synthetic", elapsed_seconds=0,
        result_json=result.model_dump_json(), validation_json="[]",
    ))
    session.flush()
    materialize_task_result(session, task.id)
    return session.get(FileCurrentDataRecord, task.id)


def set_limited_input(task):
    task.input_policy_json = InputPolicy(allow_limited_input=False).model_dump_json()
    task.input_plan_json = InputScope(
        rule="prefix_v1", coverage="partial", selected=[SourceRange(kind="line", start=1, end=2)],
        omitted=[OmittedRange(location=SourceRange(kind="line", start=3, end=8), reason="input_budget")],
        selected_units=2, total_units=8, text_characters=20, text_budget=20, image_budget=0,
    ).model_dump_json()


def test_group_pages_and_sql_counts_are_disjoint_with_all_export_states(client):
    expected = {key: set() for key in ("active", "pending", "completed", "failed", "cancelled")}
    with client.app.state.session_factory() as session:
        for status in ("created", "queued", "processing", "validating", "paused"):
            add_task(session, f"active-{status}", status=status)
            expected["active"].add(f"active-{status}")
        for status in ("waiting_for_template", "needs_review", "failed", "cancelled"):
            task_id = f"other-{status}"
            add_task(session, task_id, status=status)
            expected["pending" if status in ("waiting_for_template", "needs_review") else status].add(task_id)
        for mode in ("copy", "move"):
            for export_status in ("disabled", "awaiting_confirmation", "pending", "exporting", "completed", "failed", "skipped", "needs_rebind"):
                task_id = f"{mode}-{export_status}"
                add_task(session, task_id, status="completed", export={"mode": mode, "status": export_status})
                group = ("pending" if export_status in ("failed", "needs_rebind") else
                         "active" if export_status in ("awaiting_confirmation", "pending", "exporting") else "completed")
                expected[group].add(task_id)
        session.commit()
    summary = client.get("/api/v1/tasks/summary").json()
    groups = summary["group_counts"]
    assert groups == {"all": sum(map(len, expected.values())), **{key: len(ids) for key, ids in expected.items()}}
    seen = set()
    for group, ids in expected.items():
        actual = []
        for offset in range(0, len(ids), 2):
            response = client.get("/api/v1/tasks", params={"group": group, "limit": 2, "offset": offset})
            assert response.status_code == 200, response.text
            actual.extend(task["id"] for task in response.json())
        assert actual == sorted(ids, reverse=True)
        assert not seen.intersection(actual)
        seen.update(actual)
    assert len(seen) == groups["all"]
    assert {task["id"] for task in client.get("/api/v1/tasks", params={"group": "all"}).json()} == seen
    assert client.get("/api/v1/tasks", params={"group": "unknown"}).status_code == 422
    # Existing flags retain their earlier scope (including waiting/review).
    old_active = {task["id"] for task in client.get("/api/v1/tasks", params={"active_only": True}).json()}
    assert {f"active-{status}" for status in ("created", "queued", "processing", "validating", "paused")} < old_active
    assert "copy-exporting" not in old_active
    assert "other-needs_review" in old_active
    old_completed = {task["id"] for task in client.get("/api/v1/tasks", params={"status": "completed"}).json()}
    assert "copy-failed" in old_completed and "move-failed" not in old_completed
    assert client.get("/api/v1/tasks/summary", params={"search": "copy-failed"}).json()["group_counts"] == {
        "all": 1, "active": 0, "pending": 1, "completed": 0, "failed": 0, "cancelled": 0,
    }
    assert [task["id"] for task in client.get("/api/v1/tasks", params={"group": "pending", "search": "copy-failed"}).json()] == ["copy-failed"]


def test_legacy_pending_actions_use_stored_facts_not_descriptions_or_target_id(client):
    with client.app.state.session_factory() as session:
        template = add_task(session, "legacy-template")
        template.target_table_id = "unrelated-stale-destination"
        template.candidate_templates_json = json.dumps([{"id": "builtin-invoice", "version": 1, "name": "发票", "description": "指定表不符"}])
        target = add_task(session, "legacy-target", reason="template")
        target.candidate_templates_json = json.dumps([{"id": "builtin-invoice", "version": 1, "name": "Invoice", "description": "Any changed or translated text"}])
        current = add_result(session, target)
        table_id = current.table_id
        initial = session.get(ExtractionRecord, target.id).result_json
        scope = add_task(session, "legacy-scope")
        set_limited_input(scope)
        add_task(session, "future-kind", reason="future-pending-kind")
        add_task(session, "stale-terminal", status="completed", reason="input_scope")
        session.commit()
    rows = {task["id"]: task for task in client.get("/api/v1/tasks").json()}
    assert (rows["legacy-template"]["pending_reason"], rows["legacy-template"]["pending_action"]) == ("template", "select_template")
    assert (rows["legacy-target"]["pending_reason"], rows["legacy-target"]["pending_action"], rows["legacy-target"]["pending_review_version"]) == ("target_table", "confirm_target_table", 0)
    assert rows["legacy-scope"]["pending_action"] == "confirm_input_scope"
    assert rows["future-kind"]["pending_reason"] == "unknown" and rows["future-kind"]["pending_action"] is None
    assert rows["stale-terminal"]["pending_reason"] is None and rows["stale-terminal"]["pending_action"] is None
    assert client.post("/api/v1/tasks/legacy-template/template", json={"target_table_id": table_id}).status_code == 409
    assert client.post("/api/v1/tasks/legacy-target/template", json={"template_id": "builtin-invoice"}).status_code == 409
    assert client.post("/api/v1/tasks/legacy-target/input-scope").status_code == 409
    assert client.post("/api/v1/tasks/legacy-scope/template", json={"template_id": "builtin-invoice"}).status_code == 409
    assert client.post("/api/v1/tasks/future-kind/template", json={"template_id": "builtin-invoice"}).status_code == 409
    assert client.post("/api/v1/tasks/legacy-template/retry").status_code == 409
    with client.app.state.session_factory() as session:
        assert session.get(ExtractionRecord, "legacy-target").result_json == initial
        assert session.get(TaskRecord, "legacy-target").status == "waiting_for_template"
    confirmed = client.post("/api/v1/tasks/legacy-target/template", json={"target_table_id": table_id, "expected_review_version": 0})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed" and confirmed.json()["pending_action"] is None
    assert client.post("/api/v1/queue/pause").status_code == 200
    scoped = client.post("/api/v1/tasks/legacy-scope/input-scope")
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["status"] == "paused"
    with client.app.state.session_factory() as session:
        policy = json.loads(session.get(TaskRecord, "legacy-scope").input_policy_json)
        assert policy["accept_partial"] and policy["allow_limited_input"]


def test_target_confirmation_rejects_wrong_version_and_wrong_table(client):
    with client.app.state.session_factory() as session:
        task = add_task(session, "versioned-target", reason="target_table")
        current = add_result(session, task)
        current.current_version = 3
        table_id = current.table_id
        session.commit()
    rows = client.get("/api/v1/tasks").json()
    assert rows[0]["pending_review_version"] == 3
    for payload in ({"target_table_id": table_id}, {"target_table_id": table_id, "expected_review_version": 2}, {"target_table_id": "other-table", "expected_review_version": 3}):
        assert client.post("/api/v1/tasks/versioned-target/template", json=payload).status_code == 409
    valid = client.post("/api/v1/tasks/versioned-target/template", json={"target_table_id": table_id, "expected_review_version": 3})
    assert valid.status_code == 200, valid.text
    assert valid.json()["status"] == "completed"


def test_pending_page_resolution_is_bounded_and_detached_records_are_not_guessed(client):
    with client.app.state.session_factory() as session:
        tasks = [add_task(session, f"pending-{index}") for index in range(30)]
        session.commit()
        # Warm task scalar fields before measuring the metadata resolver.
        tasks = list(session.scalars(select(TaskRecord)))
        statements = []
        engine = session.get_bind()
        def count(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(engine, "before_cursor_execute", count)
        try:
            details = pending_details_for_tasks(session, tasks)
            reads = [TaskRead.model_validate(task, context={"pending_details": details}) for task in tasks]
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert len(statements) == 2
        assert all(task.pending_action == "select_template" for task in reads)
        detached = tasks[0]
        session.expunge(detached)
        assert TaskRead.model_validate(detached).pending_action is None


def test_batch_retry_reports_each_skip_and_preserves_partial_success(client, monkeypatch):
    with client.app.state.session_factory() as session:
        for task_id in ("first-ok", "rejected", "racing", "last-ok"):
            add_task(session, task_id, status="failed")
        add_task(session, "cancelled", status="cancelled")
        add_task(session, "completed", status="completed")
        session.commit()
    original_retry = task_service.retry_task
    def reject_or_race(session, settings, task_id, **kwargs):
        if task_id == "rejected":
            raise HTTPException(422, "secret synthetic provider detail must not be copied")
        if task_id == "racing":
            with client.app.state.session_factory() as writer:
                writer.get(TaskRecord, task_id).status = "cancelled"
                writer.commit()
        return original_retry(session, settings, task_id, **kwargs)
    monkeypatch.setattr(task_service, "retry_task", reject_or_race)
    response = client.post("/api/v1/tasks/batch-retry", json={"task_ids": ["first-ok", "missing", "cancelled", "completed", "rejected", "racing", "first-ok", "last-ok"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["retried"] == ["first-ok", "cancelled", "last-ok"]
    assert result["skipped"] == 5
    assert [(item["task_id"], item["reason"]) for item in result["skipped_tasks"]] == [
        ("missing", "not_found"), ("completed", "not_failed"), ("rejected", "rejected"), ("racing", "state_changed"), ("first-ok", "duplicate"),
    ]
    assert "secret synthetic" not in response.text
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, "first-ok").status == "queued"
        assert session.get(TaskRecord, "last-ok").status == "queued"
        assert session.get(TaskRecord, "racing").status == "cancelled"
        assert session.get(TaskRecord, "rejected").status == "failed"


@pytest.mark.parametrize(("original_status", "reason"), [("needs_review", None), ("waiting_for_template", "target_table")])
def test_retry_cancelled_task_with_result_restores_the_pending_step(client, original_status, reason, monkeypatch):
    from document_pipeline_api.services import queueing

    with client.app.state.session_factory() as session:
        task = add_task(session, "keep-result", status=original_status, reason=reason)
        add_result(session, task).current_version = 3
        session.commit()
    enqueued = []
    monkeypatch.setattr(queueing, "enqueue_task", enqueued.append)
    monkeypatch.setattr(queueing, "revoke_task", lambda _task_id: None)
    client.app.state.settings = replace(client.app.state.settings, queue_enabled=True)
    cancelled = client.post("/api/v1/tasks/keep-result/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["has_result"] is True
    assert cancelled["pending_action"] is None
    assert client.get("/api/v1/tasks", params={"group": "pending"}).json() == []
    original = client.get("/api/v1/tasks/keep-result/result").json()

    response = client.post("/api/v1/tasks/keep-result/retry")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == original_status
    assert response.json()["has_result"] is True
    assert not enqueued
    assert client.get("/api/v1/tasks/keep-result/result").json() == original
    if reason == "target_table":
        assert response.json()["pending_review_version"] == 3


def test_batch_retry_capacity_is_distinct_and_paused_queue_stays_frozen(client):
    client.app.state.settings = replace(client.app.state.settings, max_active_tasks=1)
    with client.app.state.session_factory() as session:
        add_task(session, "capacity-first", status="failed")
        add_task(session, "capacity-second", status="failed")
        session.commit()
    response = client.post("/api/v1/tasks/batch-retry", json={"task_ids": ["capacity-first", "capacity-second"]})
    assert response.status_code == 200, response.text
    assert response.json()["retried"] == ["capacity-first"]
    assert response.json()["skipped_tasks"][0]["reason"] == "capacity"
    assert client.post("/api/v1/queue/pause").status_code == 200
    response = client.post("/api/v1/tasks/batch-retry", json={"task_ids": ["capacity-second"]})
    assert response.json()["retried"] == ["capacity-second"]
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, "capacity-first").status == "paused"
        assert session.get(TaskRecord, "capacity-second").status == "paused"


def test_both_retry_modes_keep_model_and_template_snapshots(client):
    with client.app.state.session_factory() as session:
        task = add_task(session, "bound-model", status="failed")
        task.model_config_version = "environment-v1"
        task.model_provider = "openai_compatible"
        task.model_name = "synthetic-bound-model"
        task.model_base_url = "http://127.0.0.1:1/v1"
        task.model_secret_ref = "synthetic-reference"
        task.input_policy_json = InputPolicy(text_budget=17).model_dump_json()
        session.commit()
        bound = {column.name: getattr(task, column.name) for column in TaskRecord.__table__.columns if column.name.startswith("model_")}
        original_policy = task.input_policy_json
    source = client.app.state.settings.storage_dir / "bound-model.txt"
    source.write_text("synthetic local text only", encoding="utf-8")
    assert client.post("/api/v1/tasks/bound-model/retry").status_code == 200
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, "bound-model")
        assert task.input_policy_json == original_policy
        task.status = "failed"
        session.commit()
    response = client.post("/api/v1/tasks/bound-model/retry", params={"use_current_settings": True})
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, "bound-model")
        assert {key: getattr(task, key) for key in bound} == bound
        assert task.template_id == "builtin-invoice" and task.template_version == 1
        assert task.input_policy_json != original_policy


def test_selection_payload_rejects_two_actions(client):
    response = client.post("/api/v1/tasks/unused/template", json={"template_id": "builtin-invoice", "target_table_id": "table"})
    assert response.status_code == 422
