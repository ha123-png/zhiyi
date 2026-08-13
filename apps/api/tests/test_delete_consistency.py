"""删除一致性 + 镜像同步 + 指定目标表机制测试。

用户批准的方案（2026-08-08）：
- 删任务只删源（任务/提取/确认/原文件），数据表行保留（task_id 置空）；
- 删数据行不影响任务/历史/统计；
- 保存到表 = 按 item_index 对齐的镜像同步（原位更新/恢复；全删光追加末尾；
  目标表被删则按模板重建）；
- 历史页记录数 = 提取快照，不依赖数据表。
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import (
    ConfirmedDocumentRecord,
    DataRowRecord,
    DataRowRevisionRecord,
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
    session.commit()


def _seed_mirror_rows(session, task_id: str, table_id: str) -> list[int]:
    row_ids: list[int] = []
    for index in (1, 2):
        row = DataRowRecord(
            table_id=table_id,
            task_id=task_id,
            item_index=index,
            row_json=json.dumps({"name": f"商品{index}"}),
        )
        session.add(row)
        session.flush()
        row_ids.append(row.id)
    session.commit()
    return row_ids


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


def test_delete_task_keeps_table_rows_and_detaches_task_id(
    client: TestClient,
    tmp_path: Path,
) -> None:
    with client.app.state.session_factory() as session:
        _seed_completed_task(session, "del-task-keep")
        table = DataTableRecord(
            id="table-keep",
            name="发票",
            template_key="invoice",
            template_version="builtin-v1",
            document_kind="invoice",
        )
        session.add(table)
        session.flush()
        _seed_mirror_rows(session, "del-task-keep", "table-keep")
        session.add(
            ConfirmedDocumentRecord(
                task_id="del-task-keep",
                table_id="table-keep",
                review_version=0,
                result_json="{}",
            )
        )
        session.commit()
        (tmp_path / "uploads" / "del-task-keep.png").write_bytes(b"data")

    response = client.delete("/api/v1/tasks/del-task-keep")

    assert response.status_code == 200
    assert response.json()["kept_rows"] == 2
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, "del-task-keep") is None
        assert session.get(ExtractionRecord, "del-task-keep") is None
        assert session.get(ConfirmedDocumentRecord, "del-task-keep") is None
        rows = list(session.scalars(select(DataRowRecord)))
        assert len(rows) == 2
        assert all(row.task_id is None for row in rows)
        assert session.get(DataTableRecord, "table-keep") is not None
    assert not (tmp_path / "uploads" / "del-task-keep.png").exists()


def test_delete_table_rows_does_not_affect_task_or_history_count(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        _seed_completed_task(session, "hist-task")
        table = DataTableRecord(
            id="table-hist",
            name="发票",
            template_key="invoice",
            template_version="builtin-v1",
            document_kind="invoice",
        )
        session.add(table)
        session.flush()
        _seed_mirror_rows(session, "hist-task", "table-hist")
        session.add(
            ConfirmedDocumentRecord(
                task_id="hist-task",
                table_id="table-hist",
                review_version=0,
                result_json="{}",
            )
        )
        session.commit()

    tasks_before = client.get("/api/v1/tasks").json()
    assert tasks_before[0]["record_count"] == 2

    with client.app.state.session_factory() as session:
        rows = list(session.scalars(select(DataRowRecord)))
        session.delete(rows[0])
        session.commit()

    # 删数据行后：任务仍在，历史计数仍是提取快照 2（不依赖数据表）
    tasks_after = client.get("/api/v1/tasks").json()
    assert tasks_after[0]["status"] == "completed"
    assert tasks_after[0]["record_count"] == 2


def test_confirm_rejects_incompatible_target_table(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        _seed_completed_task(session, "target-task")
        _seed_manual_table(session, "manual-table", "手动发票表")
        session.flush()
        _seed_mirror_rows(session, "target-task", "manual-table")
        session.add(
            ConfirmedDocumentRecord(
                task_id="target-task",
                table_id="manual-table",
                review_version=0,
                result_json="{}",
            )
        )
        session.commit()

    _seed_manual_table_helper(client, "other-table", "其他表")

    response = client.post(
        "/api/v1/tasks/target-task/confirm",
        json={"expected_review_version": 0, "target_table_id": "other-table"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "提取出字段与指定表不符，请重新选择指定表。"
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, "target-task")
        assert task.target_table_id is None
        assert not session.scalars(select(DataRowRecord).where(DataRowRecord.table_id == "other-table")).all()


def test_rejected_incompatible_target_keeps_user_edited_rows_unchanged(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        _seed_completed_task(session, "edited-target-task")
        _seed_manual_table(session, "old-table", "旧表")
        _seed_manual_table(session, "new-table", "新表")
        row_ids = _seed_mirror_rows(session, "edited-target-task", "old-table")
        edited = session.get(DataRowRecord, row_ids[0])
        assert edited is not None
        edited.row_json = json.dumps({"name": "用户校正后的商品"}, ensure_ascii=False)
        edited.row_version = 2
        session.add(
            DataRowRevisionRecord(
                row_id=edited.id,
                table_id="old-table",
                task_id="edited-target-task",
                version=2,
                operation="table_edit",
                before_json=json.dumps({"name": "商品1"}),
                after_json=edited.row_json,
                editor="user",
            )
        )
        session.add(
            ConfirmedDocumentRecord(
                task_id="edited-target-task",
                table_id="old-table",
                review_version=0,
                result_json="{}",
            )
        )
        session.commit()

    response = client.post(
        "/api/v1/tasks/edited-target-task/confirm",
        json={"expected_review_version": 0, "target_table_id": "new-table"},
    )

    assert response.status_code == 409
    with client.app.state.session_factory() as session:
        moved = session.get(DataRowRecord, row_ids[0])
        assert moved is not None
        assert moved.table_id == "old-table"
        assert json.loads(moved.row_json)["name"] == "用户校正后的商品"
        assert moved.row_version == 2
        assert not session.scalars(select(DataRowRecord).where(DataRowRecord.table_id == "new-table")).all()


def _seed_manual_table_helper(client: TestClient, table_id: str, name: str) -> None:
    with client.app.state.session_factory() as session:
        _seed_manual_table(session, table_id, name)


def test_materialize_mirrors_rows_by_item_index_restoring_deleted_position(
    client: TestClient,
) -> None:
    """多明细删掉一条明细，再保存到表：缺失的 item_index 重新插入原位。"""
    with client.app.state.session_factory() as session:
        _seed_completed_task(session, "mirror-task")
        table = DataTableRecord(
            id="mirror-table",
            name="发票",
            template_key="invoice",
            template_version="builtin-v1",
            document_kind="invoice",
        )
        session.add(table)
        session.flush()
        _seed_mirror_rows(session, "mirror-task", "mirror-table")
        session.add(
            ConfirmedDocumentRecord(
                task_id="mirror-task",
                table_id="mirror-table",
                review_version=0,
                result_json="{}",
            )
        )
        session.commit()

    # 删掉 item_index=2 那行
    with client.app.state.session_factory() as session:
        rows = list(
            session.scalars(
                select(DataRowRecord).where(DataRowRecord.table_id == "mirror-table")
            )
        )
        deleted_row = next(row for row in rows if row.item_index == 2)
        session.delete(deleted_row)
        session.commit()

    # 重新保存到表 → 行恢复到 2 条，item_index 1 和 2 都在原位
    response = client.post(
        "/api/v1/tasks/mirror-task/confirm",
        json={"expected_review_version": 0},
    )
    assert response.status_code == 200
    assert response.json()["row_count"] == 2

    with client.app.state.session_factory() as session:
        rows = list(
            session.scalars(
                select(DataRowRecord)
                .where(DataRowRecord.table_id == "mirror-table")
                .order_by(DataRowRecord.item_index)
            )
        )
        assert [row.item_index for row in rows] == [1, 2]


def test_confirm_falls_back_to_template_table_when_target_deleted(
    client: TestClient,
) -> None:
    """目标表被删除后，保存到表回退重建模板唯一表。"""
    with client.app.state.session_factory() as session:
        _seed_completed_task(session, "fallback-task", target_table_id="gone-table")
        _seed_manual_table(session, "gone-table", "将被删除的表")
        session.flush()
        _seed_mirror_rows(session, "fallback-task", "gone-table")
        session.add(
            ConfirmedDocumentRecord(
                task_id="fallback-task",
                table_id="gone-table",
                review_version=0,
                result_json="{}",
            )
        )
        session.commit()

    # 真实场景：用户删除数据表（确认记录级联删除），任务仍在历史中
    deleted = client.delete("/api/v1/tables/gone-table")
    assert deleted.status_code == 204

    response = client.post(
        "/api/v1/tasks/fallback-task/confirm",
        json={"expected_review_version": 0},
    )

    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        assert session.get(DataTableRecord, "gone-table") is None
        confirmation = session.get(ConfirmedDocumentRecord, "fallback-task")
        assert confirmation is not None
        assert confirmation.table_id != "gone-table"
        rows = list(session.scalars(select(DataRowRecord)))
        assert len(rows) == 2
        assert rows[0].table_id == confirmation.table_id
        assert rows[0].table_id is not None


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
