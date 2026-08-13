import json
from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import (
    ExtractionRecord,
    ReviewRevisionRecord,
    TaskRecord,
)
from document_pipeline_api.schemas.extraction import DocumentExtraction


def _result(*, seller_name: str = "模型销售方", total_amount: float = 106):
    return DocumentExtraction(
        document_type="发票",
        seller_name=seller_name,
        buyer_name="购买方",
        document_number="NO-1",
        document_date="2026-07-30",
        amount_before_tax=100,
        tax_amount=6,
        total_amount=total_amount,
        items=[
            {
                "name": "服务",
                "specification": None,
                "unit": "项",
                "quantity": 1,
                "unit_price": 100,
                "amount": 100,
                "tax_rate": "6%",
                "tax_amount": 6,
            }
        ],
    )


def _seed_review_task(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="review-task",
                filename="invoice.png",
                content_type="image/png",
                size_bytes=1,
                sha256="digest",
                storage_path="invoice.png",
                template_mode="invoice",
                status="needs_review",
            )
        )
        session.flush()
        session.add(
            ExtractionRecord(
                task_id="review-task",
                document_kind="invoice",
                model_name="test-model",
                prompt_version="test-prompt",
                elapsed_seconds=1,
                result_json=_result().model_dump_json(),
                validation_json="[]",
            )
        )
        session.commit()


def test_review_update_preserves_model_result_and_records_changes(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'review.db'}",
        storage_dir=tmp_path / "uploads",
    )
    with TestClient(create_app(settings)) as client:
        _seed_review_task(client)

        response = client.put(
            "/api/v1/tasks/review-task/review",
            json={
                "expected_version": 0,
                "result": _result(
                    seller_name="人工修正销售方",
                    total_amount=105,
                ).model_dump(mode="json"),
            },
        )

        assert response.status_code == 200
        review = response.json()
        assert review["review_version"] == 1
        assert review["original_result"]["seller_name"] == "模型销售方"
        assert review["result"]["seller_name"] == "人工修正销售方"
        assert review["validation_issues"][0]["code"] == "invoice_tax_total_mismatch"
        seller_evidence = next(
            item
            for item in review["evidence"]
            if item["field_path"] == "seller_name"
        )
        assert seller_evidence["status"] == "user_edited"
        assert seller_evidence["source"] == "user"
        assert seller_evidence["page_number"] is None

        with client.app.state.session_factory() as session:
            revision = session.query(ReviewRevisionRecord).one()
            changes = json.loads(revision.changes_json)
            assert {
                "path": "seller_name",
                "before": "模型销售方",
                "after": "人工修正销售方",
            } in changes


def test_review_update_rejects_stale_version(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stale.db'}",
        storage_dir=tmp_path / "uploads",
    )
    with TestClient(create_app(settings)) as client:
        _seed_review_task(client)
        payload = {
            "expected_version": 0,
            "result": _result(seller_name="第一次修改").model_dump(mode="json"),
        }
        assert client.put(
            "/api/v1/tasks/review-task/review",
            json=payload,
        ).status_code == 200

        stale = client.put(
            "/api/v1/tasks/review-task/review",
            json=payload,
        )

        assert stale.status_code == 409
        assert "刷新" in stale.json()["detail"]


def test_completed_task_rejects_wrong_edit_with_validation_error(
    tmp_path: Path,
) -> None:
    """已完成任务被改成错误值后，保存不更新确认记录，而是返回校验问题供前端展示与忽略。"""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'completed-review.db'}",
        storage_dir=tmp_path / "uploads",
    )
    with TestClient(create_app(settings)) as client:
        with client.app.state.session_factory() as session:
            session.add(
                TaskRecord(
                    id="completed-task",
                    filename="invoice.png",
                    content_type="image/png",
                    size_bytes=1,
                    sha256="digest",
                    storage_path="invoice.png",
                    template_mode="invoice",
                    status="completed",
                )
            )
            session.flush()
            session.add(
                ExtractionRecord(
                    task_id="completed-task",
                    document_kind="invoice",
                    model_name="test-model",
                    prompt_version="test-prompt",
                    elapsed_seconds=1,
                    result_json=_result().model_dump_json(),
                    validation_json="[]",
                )
            )
            session.commit()

        # 改错：不含税 100 + 税 6 = 106，把价税合计改成 999
        wrong = _result(total_amount=999)
        response = client.put(
            "/api/v1/tasks/completed-task/review",
            json={
                "expected_version": 0,
                "result": wrong.model_dump(mode="json"),
            },
        )

        # 不再 422 拦截：草稿与校验问题被保存，返回的校验面板里带 error 条目，可逐条忽略
        assert response.status_code == 200
        body = response.json()
        assert any(
            issue["severity"] == "error" and not issue["ignored"]
            for issue in body["validation_issues"]
        ), "保存被拦截时前端必须能拿到 error 条目"

        # 忽略该阻断问题后再保存 → 成功更新确认记录
        ignored_response = client.put(
            "/api/v1/tasks/completed-task/review",
            json={
                "expected_version": 1,
                "result": wrong.model_dump(mode="json"),
                "ignored_issue_indices": [
                    idx
                    for idx, issue in enumerate(body["validation_issues"])
                    if issue["severity"] == "error"
                ],
            },
        )
        assert ignored_response.status_code == 200
        after = ignored_response.json()
        assert all(
            issue["severity"] != "error" or issue["ignored"]
            for issue in after["validation_issues"]
        ), "忽略后保存应返回全部已忽略或无阻断的校验结果"
