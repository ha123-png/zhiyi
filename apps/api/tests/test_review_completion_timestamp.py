"""A first successful revision completes the task in history and statistics."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_secrets import ModelSecretStore, SecretNotFoundError
from document_pipeline_api.models import ExtractionRecord
from document_pipeline_api.services.extraction import process_task


class NoCredentials:
    def read(self, target):
        raise SecretNotFoundError("No credentials in this synthetic test")

    def write(self, target, secret):
        raise AssertionError("This test must not create model credentials")

    def delete(self, target):
        pass


class SyntheticModel:
    model_name = "synthetic-review-completion"

    def __init__(self):
        self.calls = 0

    def complete_text(self, prompt, result_type):
        self.calls += 1
        return result_type.model_validate({"header": {"customer": "合成客户"}, "items": [
            {"item": "合成项目", "qty": 12, "price": 28, "amount": 999},
        ]})


def api(client, method, route, **kwargs):
    response = client.request(method, "/api/v1" + route, **kwargs)
    assert response.is_success, response.text
    return response.json()


def task_state(client, task_id):
    tasks = api(client, "GET", "/tasks", params={"task_ids": task_id})
    assert len(tasks) == 1 and tasks[0]["id"] == task_id
    return tasks[0]


def revise(client, task_id, entry, changes):
    if entry == "review":
        extraction = api(client, "GET", f"/tasks/{task_id}/result")
        extraction["result"]["items"][0].update(changes)
        return api(client, "PUT", f"/tasks/{task_id}/review", json={
            "expected_version": extraction["review_version"],
            "result": extraction["result"],
            "detail_ids": extraction["detail_ids"],
        })
    if entry == "file":
        current = api(client, "GET", f"/tasks/{task_id}/current-details")
        return api(client, "PATCH", f"/tasks/{task_id}/current", json={
            "expected_version": current["version"],
            "detail_changes": {str(current["items"][0]["id"]): changes},
        })
    tables = api(client, "GET", "/tables")
    assert len(tables) == 1
    table = api(client, "GET", f"/tables/{tables[0]['id']}")
    row = next(row for row in table["rows"] if row["task_id"] == task_id)
    return api(client, "PATCH", f"/tables/{table['id']}/rows/{row['id']}", json={
        "expected_version": row["version"], "changes": changes,
    })


def completion_counts(client):
    summary = api(client, "GET", "/stats/summary", params={"days": "all"})
    trend = api(client, "GET", "/stats/trend", params={"days": "all"})
    return summary["completed_count"], sum(point["completed_on_date"] for point in trend)


@pytest.mark.parametrize("entry", ["review", "file", "warehouse"])
def test_revision_sets_first_completion_time_and_preserves_it_after_later_edits(tmp_path, monkeypatch, entry):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'synthetic.db'}", storage_dir=tmp_path / "uploads",
                        queue_enabled=False, recover_local_model_on_startup=False)
    store = ModelSecretStore(NoCredentials())
    model = SyntheticModel()
    original = "合成原件：数量 12，单价 28，待核对金额 999。".encode()
    with TestClient(create_app(settings, model_secret_store=store)) as client:
        template = api(client, "POST", "/templates", json={
            "name": "合成核对完成时间", "fields": [
                {"key": "customer", "label": "客户", "section": "header", "value_type": "text"},
                {"key": "item", "label": "项目", "section": "item", "value_type": "text"},
                *[{"key": key, "label": label, "section": "item", "value_type": "number"}
                  for key, label in (("qty", "数量"), ("price", "单价"), ("amount", "金额"))],
            ],
            "deterministic_rules": [{"kind": "equation", "field": "items[].amount",
                "left": {"op": "multiply", "left": {"op": "field", "path": "items[].qty"},
                         "right": {"op": "field", "path": "items[].price"}},
                "right": {"op": "field", "path": "items[].amount"}}],
        })
        task = api(client, "POST", "/tasks", files={"file": ("synthetic.txt", original, "text/plain")},
                   data={"template_id": template["id"]})
        task_id = task["id"]
        with client.app.state.session_factory() as session:
            process_task(session, settings, task_id, client=model, secret_store=store)
            original_result = session.get(ExtractionRecord, task_id).result_json
        before = task_state(client, task_id)
        assert before["status"] == "needs_review" and before["completed_at"] is None
        assert completion_counts(client) == (0, 0)

        first_time = datetime.now(timezone.utc)
        monkeypatch.setattr("document_pipeline_api.services.current_data.utc_now", lambda: first_time)
        revise(client, task_id, entry, {"amount": 336})
        completed = task_state(client, task_id)
        assert completed["status"] == "completed"
        assert (completed["completed_at"] is not None, *completion_counts(client)) == (True, 1, 1)
        first_completion = completed["completed_at"]
        assert datetime.fromisoformat(first_completion).replace(tzinfo=timezone.utc) == first_time

        monkeypatch.setattr("document_pipeline_api.services.current_data.utc_now", lambda: first_time + timedelta(minutes=1))
        revise(client, task_id, entry, {"item": "修订后的合成项目"})
        assert task_state(client, task_id)["completed_at"] == first_completion
        assert completion_counts(client) == (1, 1)

        # Later user revisions keep the completed state while recording fresh rule findings.
        revise(client, task_id, entry, {"amount": 999})
        edited = task_state(client, task_id)
        assert edited["status"] == "completed" and edited["completed_at"] == first_completion
        assert completion_counts(client) == (1, 1)
        issues = api(client, "GET", f"/tasks/{task_id}/result")["validation_issues"]
        assert any("计算不一致" in issue["message"] and not issue["not_checked"] for issue in issues)
        revise(client, task_id, entry, {"amount": "待核"})
        exception = api(client, "GET", f"/tasks/{task_id}/result")
        assert exception["result"]["items"][0]["amount"] == "待核"
        assert any(issue["not_checked"] for issue in exception["validation_issues"])
        assert completion_counts(client) == (1, 1)
        revise(client, task_id, entry, {"amount": 336})
        repaired = task_state(client, task_id)
        assert repaired["status"] == "completed" and repaired["completed_at"] == first_completion
        assert completion_counts(client) == (1, 1)

        current = api(client, "GET", f"/tasks/{task_id}/current-details")
        assert current["items"][0]["values"]["amount"] == 336
        assert current["items"][0]["values"]["item"] == "修订后的合成项目"
        assert api(client, "GET", f"/tasks/{task_id}/revisions")["total"] == 6
        with client.app.state.session_factory() as session:
            assert session.get(ExtractionRecord, task_id).result_json == original_result
        assert client.get(f"/api/v1/tasks/{task_id}/file").content == original
        assert model.calls == 1
