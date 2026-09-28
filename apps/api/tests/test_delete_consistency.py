"""File lifecycle and current-data deletion follow the v0.5 data baseline."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import (
    FileCurrentDataRecord,
    FileRevisionRecord,
    DataRowRecord,
    DataTableRecord,
    ExtractionRecord,
    TaskRecord,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "uploads",
        max_upload_bytes=32,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _seed_completed_task(session, task_id: str, *, target_table_id: str | None = None) -> None:
    session.add(
        TaskRecord(
            id=task_id,
            filename=f"{task_id}.png",
            content_type="image/png",
            size_bytes=1,
            sha256=f"digest-{task_id}",
            storage_path=f"{task_id}.png",
            template_mode="invoice",
            status="completed",
            target_table_id=target_table_id,
        )
    )
    session.flush()
    session.add(
        ExtractionRecord(
            task_id=task_id,
            document_kind="invoice",
            model_name="test-model",
            prompt_version="test-v1",
            elapsed_seconds=1.0,
            result_json=json.dumps(
                {
                    "document_type": "发票",
                    "seller_name": "销售方",
                    "buyer_name": "购买方",
                    "document_number": "NO-1",
                    "document_date": "2026-08-01",
                    "amount_before_tax": 100.0,
                    "tax_amount": 13.0,
                    "total_amount": 113.0,
                    "items": [
                        {
                            "name": "商品A",
                            "specification": None,
                            "unit": "件",
                            "quantity": 1.0,
                            "unit_price": 100.0,
                            "amount": 100.0,
                            "tax_rate": None,
                            "tax_amount": None,
                        },
                        {
                            "name": "商品B",
                            "specification": None,
                            "unit": "件",
                            "quantity": 2.0,
                            "unit_price": 50.0,
                            "amount": 100.0,
                            "tax_rate": None,
                            "tax_amount": None,
                        },
                    ],
                }
            ),
            validation_json="[]",
            evidence_json="[]",
        )
    )
    session.flush()
    from document_pipeline_api.services.data_tables import materialize_task_result
    materialize_task_result(session, task_id)
    session.commit()


def _seed_manual_table(session, table_id: str, name: str) -> None:
    session.add(
        DataTableRecord(
            id=table_id,
            name=name,
            template_key=f"manual-{table_id}",
            template_version="1",
            document_kind="custom",
            columns_json=json.dumps(
                [
                    {"key": "name", "label": "商品名称", "value_type": "text"},
                    {"key": "seller_name", "label": "销售方", "value_type": "text"},
                ]
            ),
        )
    )
    session.commit()


def _data(client: TestClient) -> dict:
    tables = client.get("/api/v1/tables").json()
    source = next(table for table in tables if table["document_kind"] == "invoice")
    return client.get(f"/api/v1/tables/{source['id']}").json()


def _seed(client: TestClient, task_id: str = "file") -> dict:
    with client.app.state.session_factory() as session:
        _seed_completed_task(session, task_id)
    (client.app.state.settings.storage_dir / f"{task_id}.png").write_bytes(b"evidence")
    return _data(client)


def test_delete_whole_file_removes_bound_data_but_not_independent_copy(client):
    data = _seed(client)
    with client.app.state.session_factory() as session:
        _seed_manual_table(session, "copy", "独立副本")
        session.add(DataRowRecord(table_id="copy", item_index=1, row_json='{"name":"商品A"}'))
        session.commit()
    response = client.delete("/api/v1/tasks/file")
    assert response.status_code == 200, response.text
    assert response.json()["kept_rows"] == 0
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, "file") is None
        assert session.get(ExtractionRecord, "file") is None
        assert session.get(FileCurrentDataRecord, "file") is None
        assert session.scalar(select(FileRevisionRecord).where(FileRevisionRecord.task_id == "file")) is None
        rows = list(session.scalars(select(DataRowRecord)))
        assert len(rows) == 1 and rows[0].table_id == "copy" and rows[0].task_id is None
        assert session.get(DataTableRecord, data["id"]) is not None
    assert not (client.app.state.settings.storage_dir / "file.png").exists()


def test_delete_detail_updates_both_views_and_current_count_preserving_evidence(client):
    data = _seed(client)
    row = data["rows"][0]
    response = client.request("DELETE", f"/api/v1/tables/{data['id']}/rows", json={"row_ids": [row["id"]]})
    assert response.status_code == 204, response.text
    assert client.get("/api/v1/tasks").json()[0]["record_count"] == 1
    result = client.get("/api/v1/tasks/file/result").json()
    assert [item["name"] for item in result["result"]["items"]] == ["商品B"]
    assert result["result"]["header"]["seller_name"] == "销售方"
    assert (client.app.state.settings.storage_dir / "file.png").read_bytes() == b"evidence"
    history = client.get("/api/v1/tasks/file/revisions").json()
    detail = client.get(f"/api/v1/tasks/file/revisions/{history['revisions'][0]['id']}").json()
    assert detail["items"][0]["before"]["values"]["name"] == "商品A"
    assert detail["items"][0]["after"] is None


def test_repeat_confirmation_never_restores_a_deleted_detail(client):
    data = _seed(client)
    deleted_id = data["rows"][1]["id"]
    assert client.request("DELETE", f"/api/v1/tables/{data['id']}/rows", json={"row_ids": [deleted_id]}).status_code == 204
    current = client.get("/api/v1/tasks/file/result").json()
    response = client.post("/api/v1/tasks/file/confirm", json={"expected_review_version": current["review_version"]})
    assert response.status_code == 200, response.text
    assert response.json()["row_count"] == 1
    with client.app.state.session_factory() as session:
        from document_pipeline_api.services.data_tables import materialize_task_result
        materialize_task_result(session, "file")
        session.commit()
        assert session.get(DataRowRecord, deleted_id) is None
    assert _data(client)["row_count"] == 1


def test_last_detail_deletion_clears_header_without_deleting_file(client):
    data = _seed(client)
    for row in data["rows"]:
        assert client.request("DELETE", f"/api/v1/tables/{data['id']}/rows", json={"row_ids": [row["id"]]}).status_code == 204
    result = client.get("/api/v1/tasks/file/result").json()
    assert result["result"] == {"header": {}, "items": []}
    assert not result["has_current_data"]
    assert client.get("/api/v1/tasks").json()[0]["record_count"] == 0
    assert client.get("/api/v1/tasks/file/revisions").json()["total"] == 3
    assert (client.app.state.settings.storage_dir / "file.png").exists()


def test_deleting_table_cannot_replay_original_result_or_recreate_table(client):
    data = _seed(client)
    assert client.delete(f"/api/v1/tables/{data['id']}").status_code == 204
    current = client.get("/api/v1/tasks/file/result").json()
    assert current["result"]["items"] == []
    response = client.post("/api/v1/tasks/file/confirm", json={"expected_review_version": current["review_version"]})
    assert response.status_code == 409
    assert client.get("/api/v1/tables").json() == []
    assert client.get("/api/v1/tasks/file/revisions").json()["total"] == 2
    assert (client.app.state.settings.storage_dir / "file.png").exists()


def test_confirmation_cannot_copy_current_data_to_another_table(client):
    data = _seed(client)
    row = data["rows"][0]
    response = client.patch(f"/api/v1/tables/{data['id']}/rows/{row['id']}", json={"expected_version": row["version"], "changes": {"name": "用户修正"}})
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        _seed_manual_table(session, "other", "其他表")
    current = client.get("/api/v1/tasks/file/result").json()
    response = client.post("/api/v1/tasks/file/confirm", json={"expected_review_version": current["review_version"], "target_table_id": "other"})
    assert response.status_code == 409
    assert _data(client)["rows"][0]["values"]["name"] == "用户修正"
    assert client.get("/api/v1/tables/other").json()["row_count"] == 0


def test_batch_retry_retries_only_failed_tasks(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="failed-1",
                filename="a.png",
                content_type="image/png",
                size_bytes=1,
                sha256="digest-a",
                storage_path="a.png",
                template_mode="invoice",
                status="failed",
                failure_code="processing_failed",
            )
        )
        session.add(
            TaskRecord(
                id="failed-2",
                filename="b.png",
                content_type="image/png",
                size_bytes=1,
                sha256="digest-b",
                storage_path="b.png",
                template_mode="invoice",
                status="failed",
                failure_code="processing_failed",
            )
        )
        session.add(
            TaskRecord(
                id="completed-task",
                filename="c.png",
                content_type="image/png",
                size_bytes=1,
                sha256="digest-c",
                storage_path="c.png",
                template_mode="invoice",
                status="completed",
            )
        )
        session.commit()

    response = client.post(
        "/api/v1/tasks/batch-retry",
        json={"task_ids": ["failed-1", "failed-2", "completed-task"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert sorted(payload["retried"]) == ["failed-1", "failed-2"]
    assert payload["skipped"] == 1
