import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import DataRowRecord, ExtractionRecord, FileCurrentDataRecord, FileRevisionRecord, TaskRecord
from document_pipeline_api.schemas.extraction import DocumentExtraction
from document_pipeline_api.services.data_tables import materialize_task_result


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as value:
        for task_id in ("A", "B"):
            with value.app.state.session_factory() as session:
                task = TaskRecord(id=task_id, filename=f"{task_id}.png", content_type="image/png", size_bytes=1,
                                  sha256=task_id, storage_path=f"{task_id}.png", status="completed", template_mode="invoice")
                session.add(task)
                session.flush()
                item_defaults = {"specification": None, "unit": None, "unit_price": None, "tax_rate": None, "tax_amount": None}
                result = DocumentExtraction(document_type="发票", document_number=task_id, seller_name="甲", buyer_name="乙", document_date="2026-09-16",
                                            amount_before_tax=30, tax_amount=0, total_amount=30,
                                            items=[{**item_defaults, "name": "商品一", "quantity": 1, "amount": 10},
                                                   {**item_defaults, "name": "商品二", "quantity": 2, "amount": 20}])
                session.add(ExtractionRecord(task_id=task_id, document_kind="invoice", model_name="test", prompt_version="test",
                                             elapsed_seconds=0, result_json=result.model_dump_json(), validation_json="[]"))
                session.flush()
                materialize_task_result(session, task_id)
                session.commit()
                (settings.storage_dir / f"{task_id}.png").write_bytes(b"evidence")
        yield value


def get(client, url):
    response = client.get('/api/v1' + url)
    assert response.status_code == 200, response.text
    return response.json()


def table(client):
    return get(client, "/tables/" + get(client, "/tables")[0]["id"])


def test_manual_evidence_survives_later_unrelated_edit_and_detail_deletion(client):
    for key, value in (("seller_name", "人工甲"), ("buyer_name", "人工乙")):
        extraction = get(client, "/tasks/A/result")
        extraction["result"]["header"][key] = value
        response = client.put('/api/v1/tasks/A/review', json={"expected_version": extraction["review_version"],
            "result": extraction["result"], "detail_ids": extraction["detail_ids"]})
        assert response.status_code == 200, response.text
    extraction = get(client, "/tasks/A/result")
    evidence = {item["field_path"]: item for item in extraction["evidence"]}
    assert evidence["header.seller_name"]["status"] == "user_edited"
    assert evidence["header.buyer_name"]["status"] == "user_edited"
    extraction["result"]["items"].pop(0)
    assert client.put('/api/v1/tasks/A/review', json={"expected_version": extraction["review_version"],
        "result": extraction["result"], "detail_ids": extraction["detail_ids"][1:]}).status_code == 200
    evidence = {item["field_path"]: item for item in get(client, "/tasks/A/result")["evidence"]}
    assert evidence["header.seller_name"]["status"] == "user_edited"
    assert evidence["items[0].name"]["location_verified"] is False


def test_file_edit_and_warehouse_are_the_same_fact_and_one_revision(client):
    extraction = get(client, "/tasks/A/result")
    extraction["result"]["header"]["seller_name"] = "新客户"
    extraction["result"]["items"][0]["quantity"] = 5
    extraction["result"]["items"].pop()
    response = client.put('/api/v1/tasks/A/review', json={
        "expected_version": extraction["review_version"], "result": extraction["result"],
        "detail_ids": extraction["detail_ids"][:1],
    })
    assert response.status_code == 200, response.text
    rows = [row for row in table(client)["rows"] if row["task_id"] == "A"]
    assert len(rows) == 1
    assert rows[0]["values"]["seller_name"] == "新客户"
    assert rows[0]["values"]["quantity"] == 5
    history = get(client, "/tasks/A/revisions")
    assert history["total"] == 2
    revision = get(client, "/tasks/A/revisions/" + history["revisions"][0]["id"])
    assert revision["header"]["seller_name"] == {"before": "甲", "after": "新客户"}
    assert revision["deleted_details"] == 1
    deleted = next(item for item in revision["items"] if item["after"] is None)
    assert deleted["before"]["values"]["name"] == "商品二"
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, rows[0]["id"])
        assert "seller_name" not in json.loads(row.row_json)


def test_table_session_writes_immediately_groups_per_file_and_seals(client):
    begin = client.post('/api/v1/tables/edit-sessions')
    assert begin.status_code == 201, begin.text
    session_id = begin.json()["id"]
    for file_id, count in (("A", 5), ("B", 3)):
        for index in range(count):
            data = table(client)
            row = next(row for row in data["rows"] if row["task_id"] == file_id)
            response = client.patch(f'/api/v1/tables/{data["id"]}/rows/{row["id"]}', json={
                "expected_version": row["version"], "changes": {"seller_name": f"客户{index}"}, "edit_session_id": session_id,
            })
            assert response.status_code == 200, response.text
            assert get(client, f"/tasks/{file_id}/result")["result"]["header"]["seller_name"] == f"客户{index}"
    assert client.post(f'/api/v1/tables/edit-sessions/{session_id}/seal').status_code == 204
    for file_id in ("A", "B"):
        history = get(client, f"/tasks/{file_id}/revisions")
        assert history["total"] == 2
        assert history["revisions"][0]["sealed"]
        assert history["revisions"][0]["modified_fields"] == 1


def test_deleting_rows_clears_current_but_keeps_evidence_and_history(client):
    data = table(client)
    ids = [row["id"] for row in data["rows"] if row["task_id"] == "A"]
    response = client.request('DELETE', f'/api/v1/tables/{data["id"]}/rows', json={"row_ids": ids})
    assert response.status_code == 204, response.text
    extraction = get(client, '/tasks/A/result')
    assert extraction["has_current_data"] is False
    assert extraction["result"]["items"] == []
    assert extraction["result"]["header"] == {}
    assert get(client, '/tasks/A/revisions')["total"] == 2
    assert (client.app.state.settings.storage_dir / 'A.png').exists()
    assert client.post('/api/v1/tasks/A/confirm', json={"expected_review_version": extraction["review_version"]}).status_code == 409
    assert all(row["task_id"] != 'A' for row in table(client)["rows"])


def test_deleting_table_keeps_file_history_empty_current_and_no_resurrection(client):
    data = table(client)
    assert client.delete(f'/api/v1/tables/{data["id"]}').status_code == 204
    assert get(client, '/tables') == []
    assert get(client, '/tasks/A/result')["has_current_data"] is False
    assert get(client, '/tasks/A/revisions')["total"] == 2
    assert (client.app.state.settings.storage_dir / 'A.png').exists()


def test_deleting_whole_file_removes_only_its_evidence_current_and_history(client):
    response = client.delete('/api/v1/tasks/A')
    assert response.status_code == 200, response.text
    assert not (client.app.state.settings.storage_dir / 'A.png').exists()
    assert (client.app.state.settings.storage_dir / 'B.png').exists()
    assert get(client, '/tasks/B/result')["has_current_data"] is True
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, 'A') is None
        assert session.get(FileCurrentDataRecord, 'A') is None
        assert session.scalar(select(FileRevisionRecord).where(FileRevisionRecord.task_id == 'A')) is None
        assert session.scalar(select(DataRowRecord).where(DataRowRecord.task_id == 'A')) is None
