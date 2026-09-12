from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import (
    DataRowRecord,
    DataTableRecord,
    ExtractionRecord,
    TaskRecord,
)


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'admin.db'}",
        storage_dir=tmp_path / "uploads",
    )
    return TestClient(create_app(settings))


def test_settings_default_to_enabled(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/system/settings")

    assert response.status_code == 200
    assert response.json() == {
        "image_convert": True,
        "office_convert": True,
        "word_include_images": False,
        "allow_limited_input": False,
        "upload_limit_mb": 50, "input_text_limit": 20000, "input_page_limit": 10,
        "input_row_limit": 500, "input_docx_image_limit": 10,
    }


def test_settings_update_is_persisted(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        updated = client.put(
            "/api/v1/system/settings",
            json={
                "image_convert": False,
                "office_convert": True,
                "word_include_images": False,
        "allow_limited_input": False,
        "upload_limit_mb": 50, "input_text_limit": 20000, "input_page_limit": 10,
        "input_row_limit": 500, "input_docx_image_limit": 10,
            },
        )
        fetched = client.get("/api/v1/system/settings")

    assert updated.status_code == 200
    assert updated.json() == {
        "image_convert": False,
        "office_convert": True,
        "word_include_images": False,
        "allow_limited_input": False,
        "upload_limit_mb": 50, "input_text_limit": 20000, "input_page_limit": 10,
        "input_row_limit": 500, "input_docx_image_limit": 10,
    }
    assert fetched.json() == {
        "image_convert": False,
        "office_convert": True,
        "word_include_images": False,
        "allow_limited_input": False,
        "upload_limit_mb": 50, "input_text_limit": 20000, "input_page_limit": 10,
        "input_row_limit": 500, "input_docx_image_limit": 10,
    }


def test_clear_history_rejects_wrong_confirm_text(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/system/admin/clear-history",
            json={"confirm_text": "错误文字"},
        )

    assert response.status_code == 422
    assert "确认文字" in response.json()["detail"]


def test_clear_history_removes_history_keeps_active_and_data_rows(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            session.add(
                DataTableRecord(
                    id="table-1",
                    name="发票",
                    template_key="tpl",
                    template_version="v1",
                    document_kind="invoice",
                )
            )
            session.add_all(
                [
                    TaskRecord(
                        id="history-done",
                        filename="done.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="h-done",
                        storage_path="history-done.png",
                        template_mode="invoice",
                        status="completed",
                    ),
                    TaskRecord(
                        id="history-failed",
                        filename="failed.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="h-failed",
                        storage_path="history-failed.png",
                        template_mode="invoice",
                        status="failed",
                    ),
                    TaskRecord(
                        id="active-queued",
                        filename="queued.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="h-queued",
                        storage_path="active-queued.png",
                        template_mode="invoice",
                        status="queued",
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    DataRowRecord(
                        table_id="table-1",
                        task_id="history-done",
                        item_index=0,
                        row_json="{}",
                    ),
                    DataRowRecord(
                        table_id="table-1",
                        task_id=None,
                        item_index=1,
                        row_json="{}",
                    ),
                ]
            )
            session.commit()
            (tmp_path / "uploads" / "history-done.png").write_bytes(b"x")
            (tmp_path / "uploads" / "active-queued.png").write_bytes(b"x")

        response = client.post(
            "/api/v1/system/admin/clear-history",
            json={"confirm_text": "清除历史"},
        )

        assert response.status_code == 200
        assert response.json()["cleared_count"] == 2
        assert not (tmp_path / "uploads" / "history-done.png").exists()
        assert (tmp_path / "uploads" / "active-queued.png").exists()

        with client.app.state.session_factory() as session:
            remaining = list(
                session.scalars(select(TaskRecord).order_by(TaskRecord.id))
            )
            assert [task.id for task in remaining] == ["active-queued"]
            rows = list(session.scalars(select(DataRowRecord).order_by(DataRowRecord.item_index)))
            assert len(rows) == 2
            assert rows[0].task_id is None
            assert session.scalar(select(func.count()).select_from(ExtractionRecord)) == 0
