from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.services.templates import get_template_version


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'behavior.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def template_body():
    return {
        "name": "数学错题",
        "fields": [
            {"key": "subject", "label": "科目", "section": "header"},
            {"key": "question", "label": "题目", "section": "item"},
            {"key": "answer", "label": "答案", "section": "item"},
        ],
    }


def test_legacy_template_gets_conservative_defaults(client):
    response = client.post("/api/v1/templates", json=template_body())
    assert response.status_code == 201
    behavior = response.json()["behavior"]
    assert behavior["presentation"]["mode"] == "table"
    assert behavior["requires_complete_input"] is True
    assert behavior["suggest_filename"] is False


def test_legacy_hints_merge_once_and_builtin_snapshot_stays_unchanged(client):
    builtin = client.get("/api/v1/templates/builtin-invoice").json()
    body = {**template_body(), "extra_instructions": "保留原文", "validation_rules": ["保留原文", "手写优先", "手写优先", "特殊情况：\n缺失时留空"]}
    created = client.post("/api/v1/templates", json=body).json()
    assert created["extra_instructions"] == "保留原文\n手写优先\n特殊情况：\n缺失时留空"
    assert created["validation_rules"] == []
    saved = client.put(f"/api/v1/templates/{created['id']}", json={**body, "extra_instructions": created["extra_instructions"], "expected_version": 1}).json()
    assert saved["extra_instructions"] == created["extra_instructions"]
    copied = client.post("/api/v1/templates/builtin-invoice/copy").json()
    for hint in builtin["validation_rules"]:
        assert hint in copied["extra_instructions"]
    assert client.get("/api/v1/templates/builtin-invoice").json() == builtin


def test_reordering_updates_existing_table_without_rewriting_schema_or_values(client):
    from document_pipeline_api.models import DataTableRecord

    body = template_body()
    template = client.post("/api/v1/templates", json=body).json()
    manual = client.post("/api/v1/tables", json={"name": "独立手工表", "template_key": template["id"]}).json()
    manual_url = f"/api/v1/tables/{manual['id']}"
    manual_before = client.get(manual_url).json()["columns"]
    from document_pipeline_api.models import ExtractionRecord, TaskRecord
    task = client.post("/api/v1/tasks", files={"file": ("记录.txt", b"synthetic", "text/plain")}, data={"template_id": template["id"]}).json()
    with client.app.state.session_factory() as session:
        session.get(TaskRecord, task["id"]).status = "needs_review"
        session.add(ExtractionRecord(task_id=task["id"], document_kind="custom", template_id=template["id"], template_version=1, model_name="deterministic fixture", prompt_version="test", elapsed_seconds=0, result_json='{"header":{"subject":"记录"},"items":[{"question":"内容","answer":"结果"}]}', validation_json="[]"))
        session.commit()
    confirmed = client.post(f"/api/v1/tasks/{task['id']}/confirm", json={"expected_review_version": 0})
    assert confirmed.status_code == 200, confirmed.text
    table = {"id": confirmed.json()["table_id"]}
    table_url = f"/api/v1/tables/{table['id']}"
    added = client.post(f"{table_url}/columns", json={"label": "人工补充", "section": "item"})
    assert added.status_code == 201, added.text
    before = client.get(table_url).json()
    with client.app.state.session_factory() as session:
        stored = session.get(DataTableRecord, table["id"])
        schema_before = stored.columns_json
        version_before = stored.template_version
    reordered = {**body, "fields": [body["fields"][1], body["fields"][2], body["fields"][0]], "expected_version": 1}
    assert client.put(f"/api/v1/templates/{template['id']}", json=reordered).status_code == 200
    after = client.get(table_url).json()
    assert [column["key"] for column in after["columns"]] == ["question", "answer", "subject", added.json()["key"]]
    assert client.get(manual_url).json()["columns"] == manual_before
    assert after["rows"] == before["rows"]
    assert {column["key"]: column for column in after["columns"]} == {column["key"]: column for column in before["columns"]}
    with client.app.state.session_factory() as session:
        stored = session.get(DataTableRecord, table["id"])
        assert stored.columns_json == schema_before
        assert stored.template_version == version_before
        assert [field.key for field in get_template_version(session, template["id"], 1).fields] == ["subject", "question", "answer"]


def test_behavior_is_versioned_and_copied(client):
    body = template_body()
    created = client.post("/api/v1/templates", json=body).json()
    body["behavior"] = {
        "presentation": {
            "mode": "card",
            "title_field": "item.question",
            "primary_fields": ["header.subject"],
            "collapsed_fields": ["item.answer"],
        },
        "suggest_filename": True,
        "filename_mode": "fixed",
        "requires_complete_input": False,
    }
    response = client.put(
        f"/api/v1/templates/{created['id']}",
        json={**body, "expected_version": 1},
    )
    assert response.status_code == 200
    assert response.json()["behavior"] == body["behavior"]
    with client.app.state.session_factory() as session:
        old = get_template_version(session, created["id"], 1)
        assert old.behavior.presentation.mode == "table"
        assert old.behavior.suggest_filename is False
        assert old.behavior.filename_mode == "ai"
    copied = client.post(f"/api/v1/templates/{created['id']}/copy")
    assert copied.status_code == 201
    assert copied.json()["behavior"] == body["behavior"]


def test_unknown_field_cannot_be_used_for_card_title(client):
    response = client.post(
        "/api/v1/templates",
        json={**template_body(), "behavior": {
            "presentation": {"mode": "card", "title_field": "item.missing"},
        }},
    )
    assert response.status_code == 422
    assert "不存在" in response.json()["detail"]


def test_shareable_behavior_rejects_filesystem_binding(client):
    response = client.post(
        "/api/v1/templates",
        json={**template_body(), "behavior": {"export_directory": "D:/private"}},
    )
    assert response.status_code == 422


def test_table_and_split_views_keep_versioned_presentation_and_same_facts(client):
    body = template_body()
    body["behavior"] = {"presentation": {"mode": "card", "title_field": "item.question"}}
    template = client.post("/api/v1/templates", json=body).json()
    table_response = client.post("/api/v1/tables", json={"name": "错题集合", "template_key": template["id"]})
    assert table_response.status_code == 201, table_response.text
    table_id = table_response.json()["id"]
    for subject in ["数学", "数学", "物理"]:
        response = client.post(f"/api/v1/tables/{table_id}/rows", json={"values": {"subject": subject, "question": "练习题"}})
        assert response.status_code == 201
    details = client.get(f"/api/v1/tables/{table_id}").json()
    assert details["presentation"]["mode"] == "card"
    assert details["row_count"] == 3
    views = client.post(f"/api/v1/tables/{table_id}/split", json={"field_key": "subject"}).json()
    math = next(v for v in views if v["name"] == "数学")
    view = client.get(f"/api/v1/tables/{table_id}/views/{math['id']}").json()
    assert view["presentation"] == details["presentation"]
    assert view["row_count"] == 2
    row = view["rows"][0]
    edited = client.patch(f"/api/v1/tables/{table_id}/rows/{row['id']}", json={"expected_version": row["version"], "changes": {"question": "已修正"}})
    assert edited.status_code == 200
    assert client.get(f"/api/v1/tables/{table_id}").json()["rows"][0]["values"]["question"] == "已修正"
    filtered = client.get(f"/api/v1/tables/{table_id}/views/{math['id']}", params={"search": "已修正", "page_size": 1}).json()
    assert filtered["row_count"] == 1
    assert [r["id"] for r in filtered["rows"]] == [row["id"]]
    assert client.get(f"/api/v1/tables/{table_id}/views/{math['id']}", params={"search": "物理"}).json()["row_count"] == 0
    # Updating template defaults must not silently rewrite an old table version.
    body["behavior"]["presentation"]["mode"] = "table"
    assert client.put(f"/api/v1/templates/{template['id']}", json={**body, "expected_version": 1}).status_code == 200
    assert client.get(f"/api/v1/tables/{table_id}").json()["presentation"]["mode"] == "card"
