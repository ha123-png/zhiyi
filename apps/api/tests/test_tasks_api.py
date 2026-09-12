from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import zipfile

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import (
    ConfirmedDocumentRecord,
    DataRowRecord,
    DataTableRecord,
    ExtractionRecord,
    TaskRecord,
)
from image_test_data import JPEG_BYTES, PNG_BYTES


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "uploads",
        max_upload_bytes=1024,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_upload_persists_file_and_lists_real_task(client: TestClient, tmp_path: Path) -> None:
    content = PNG_BYTES

    response = client.post(
        "/api/v1/tasks",
        files={"file": ("invoice.png", content, "image/png")},
        data={"template_mode": "invoice"},
    )

    assert response.status_code == 201
    task = response.json()
    assert task["filename"] == "invoice.png"
    assert task["status"] == "queued"
    assert task["template_mode"] == "invoice"
    assert task["sha256"] == hashlib.sha256(content).hexdigest()
    assert task["duplicate_of_task_id"] is None
    assert len(list((tmp_path / "uploads").glob("*.png"))) == 1
    with client.app.state.session_factory() as session:
        saved = session.get(TaskRecord, task["id"])
        assert saved is not None
        assert saved.model_config_version == "profile-v1"
        assert saved.model_profile_id == "default-local-qwen35-4b"
        assert saved.model_profile_version == 1
        assert saved.model_provider == "lm_studio"
        assert saved.model_name == "qwen3.5-4b"
        assert saved.model_context_length == 8192
        assert saved.model_secret_ref is None
        assert "api_key" not in saved.__dict__

    tasks = client.get("/api/v1/tasks")
    assert tasks.status_code == 200
    assert [item["id"] for item in tasks.json()] == [task["id"]]


def test_text_original_has_readable_preview(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/tasks",
        files={"file": ("说明.md", "# 标题\n正文".encode(), "text/markdown")},
        data={"template_mode": "invoice"},
    )
    assert uploaded.status_code == 201

    preview = client.get(f"/api/v1/tasks/{uploaded.json()['id']}/preview")

    assert preview.status_code == 200
    assert preview.json() == {
        "kind": "markdown",
        "text": "# 标题\n正文",
        "truncated": False,
        "image_count": 0,
    }


def test_docx_preview_includes_text_and_numbered_images(client: TestClient) -> None:
    buffer = io.BytesIO()
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>合同正文</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/media/image1.png", PNG_BYTES)
    uploaded = client.post(
        "/api/v1/tasks",
        files={
            "file": (
                "合同.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"template_mode": "invoice"},
    )
    assert uploaded.status_code == 201
    task_id = uploaded.json()["id"]

    preview = client.get(f"/api/v1/tasks/{task_id}/preview")
    image = client.get(f"/api/v1/tasks/{task_id}/preview/images/1")

    assert preview.status_code == 200
    assert preview.json()["text"] == "合同正文"
    assert preview.json()["image_count"] == 1
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == PNG_BYTES


def test_upload_storage_failure_returns_507_without_orphan_files(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_open = Path.open

    def fail_upload_write(path: Path, *args, **kwargs):
        if path.name.endswith(".uploading"):
            raise OSError("simulated full disk")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_upload_write)

    response = client.post(
        "/api/v1/tasks",
        files={"file": ("invoice.png", b"image", "image/png")},
    )

    assert response.status_code == 507
    assert "磁盘空间" in response.json()["detail"]
    assert client.get("/api/v1/tasks").json() == []
    assert list((tmp_path / "uploads").iterdir()) == []


def test_real_sqlite_write_lock_returns_retryable_503_and_recovers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "busy.db"
    settings = Settings(
        database_url=f"sqlite:///{database}",
        storage_dir=tmp_path / "busy-uploads",
    )
    with TestClient(create_app(settings)) as test_client:
        with test_client.app.state.session_factory() as session:
            session.execute(text("PRAGMA busy_timeout=25"))

        locker = sqlite3.connect(database)
        try:
            locker.execute("BEGIN IMMEDIATE")
            response = test_client.post(
                "/api/v1/tasks",
                files={"file": ("locked.png", PNG_BYTES, "image/png")},
            )
        finally:
            locker.rollback()
            locker.close()

        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"
        assert "稍后重试" in response.json()["detail"]
        assert test_client.get("/api/v1/tasks").json() == []
        assert list((tmp_path / "busy-uploads").iterdir()) == []

        retried = test_client.post(
            "/api/v1/tasks",
            files={"file": ("locked.png", PNG_BYTES, "image/png")},
        )
        assert retried.status_code == 201


def test_database_full_error_is_reported_without_exposing_sql(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fail_database_write(*_args, **_kwargs):
        raise OperationalError(
            "INSERT INTO sensitive_table VALUES (?)",
            {"secret": "hidden"},
            sqlite3.OperationalError("database or disk is full"),
        )

    monkeypatch.setattr(
        "document_pipeline_api.api.tasks.create_task_from_upload",
        fail_database_write,
    )

    response = client.post(
        "/api/v1/tasks",
        files={"file": ("invoice.png", b"image", "image/png")},
    )

    assert response.status_code == 507
    assert "磁盘空间" in response.json()["detail"]
    assert "sensitive_table" not in response.text
    assert "hidden" not in response.text


def test_duplicate_upload_is_marked_without_blocking_user(client: TestClient) -> None:
    upload = {"file": ("delivery.jpg", JPEG_BYTES, "image/jpeg")}

    first = client.post("/api/v1/tasks", files=upload).json()
    second = client.post("/api/v1/tasks", files=upload)

    assert second.status_code == 201
    assert second.json()["duplicate_of_task_id"] == first["id"]


def test_upload_can_pin_custom_template_before_processing(client: TestClient) -> None:
    template = client.post(
        "/api/v1/templates",
        json={
            "name": "自定义入库单",
            "description": "整理入库记录",
            "extra_instructions": "",
            "fields": [{"label": "仓库"}],
            "validation_rules": [],
            "output_mapping": {},
        },
    ).json()

    response = client.post(
        "/api/v1/tasks",
        files={"file": ("stock.png", PNG_BYTES, "image/png")},
        data={"template_mode": "manual", "template_id": template["id"]},
    )

    assert response.status_code == 201
    assert response.json()["template_mode"] == "manual"
    assert response.json()["template_id"] == template["id"]
    assert response.json()["template_version"] == 1


def test_queued_task_can_be_cancelled(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        files={"file": ("delivery.jpg", JPEG_BYTES, "image/jpeg")},
    ).json()

    response = client.post(f"/api/v1/tasks/{created['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_sync_processing_endpoint_is_not_exposed(client: TestClient) -> None:
    assert "/api/v1/tasks/{task_id}/process" not in client.app.openapi()["paths"]


def test_failed_task_can_retry_without_reuploading(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="failed-task",
                filename="kept.png",
                content_type="image/png",
                size_bytes=1,
                sha256="failed-digest",
                storage_path="kept.png",
                template_mode="invoice",
                status="failed",
                attempt_count=2,
                failure_code="model_timeout",
                failure_message="模型处理超时。",
            )
        )
        session.commit()

    response = client.post("/api/v1/tasks/failed-task/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["filename"] == "kept.png"
    assert response.json()["attempt_count"] == 2
    assert response.json()["failure_code"] is None
    assert response.json()["failure_message"] is None


def test_waiting_task_can_select_versioned_candidate_without_reupload(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="waiting-task",
                filename="unknown.png",
                content_type="image/png",
                size_bytes=1,
                sha256="waiting-digest",
                storage_path="unknown.png",
                template_mode="smart",
                candidate_templates_json=json.dumps(
                    [
                        {
                            "id": "builtin-delivery",
                            "version": 1,
                            "name": "送货单",
                            "description": "整理送货单",
                        }
                    ]
                ),
                status="waiting_for_template",
            )
        )
        session.commit()

    response = client.post(
        "/api/v1/tasks/waiting-task/template",
        json={"template_id": "builtin-delivery"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["template_id"] == "builtin-delivery"
    assert response.json()["template_version"] == 1
    assert response.json()["candidate_templates"] == []


def test_waiting_task_can_override_candidates_with_any_active_template(
    client: TestClient,
) -> None:
    """候选是推荐而非白名单；用户必须能纠正模型分类遗漏。"""
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="waiting-override",
                filename="unknown.png",
                content_type="image/png",
                size_bytes=1,
                sha256="waiting-override-digest",
                storage_path="unknown.png",
                template_mode="smart",
                candidate_templates_json=json.dumps(
                    [
                        {
                            "id": "builtin-delivery",
                            "version": 1,
                            "name": "送货单",
                            "description": "整理送货单",
                        }
                    ]
                ),
                status="waiting_for_template",
            )
        )
        session.commit()

    response = client.post(
        "/api/v1/tasks/waiting-override/template",
        json={"template_id": "builtin-invoice"},
    )

    assert response.status_code == 200
    assert response.json()["template_id"] == "builtin-invoice"
    assert response.json()["candidate_templates"] == []


def test_rejects_unsupported_file_without_leaving_artifact(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.post(
        "/api/v1/tasks",
        files={"file": ("notes.exe", b"not supported", "application/x-msdownload")},
    )

    assert response.status_code == 415
    assert list((tmp_path / "uploads").iterdir()) == []


def test_text_file_is_accepted_when_office_conversion_enabled(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.post(
        "/api/v1/tasks",
        files={"file": ("notes.txt", "仓库\n数量\t单价\n5\t10\n".encode(), "text/plain")},
        data={"template_mode": "invoice"},
    )

    assert response.status_code == 201
    assert response.json()["page_count"] == 1
    assert len(list((tmp_path / "uploads").glob("*.txt"))) == 1


def test_text_file_is_rejected_when_office_conversion_disabled(
    client: TestClient,
) -> None:
    saved = client.put(
        "/api/v1/system/settings",
        json={"image_convert": True, "office_convert": False},
    )
    assert saved.status_code == 200

    response = client.post(
        "/api/v1/tasks",
        files={"file": ("notes.txt", "内容".encode(), "text/plain")},
    )

    assert response.status_code == 415
    assert "Office" in response.json()["detail"]


def test_legacy_page_limit_does_not_reject_text(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'text-limit.db'}",
        storage_dir=tmp_path / "uploads",
        max_pdf_pages=2,
    )
    with TestClient(create_app(settings)) as test_client:
        accepted = test_client.post(
            "/api/v1/tasks",
            files={"file": ("short.txt", "行\n" * 60, "text/plain")},
        )
        rejected = test_client.post(
            "/api/v1/tasks",
            files={"file": ("long.txt", "行\n" * 200, "text/plain")},
        )

    assert accepted.status_code == 201
    assert accepted.json()["page_count"] == 1
    assert accepted.json()["planned_scope"]["selected"] == [{"kind": "line", "start": 1, "end": 60, "container": None, "columns": None, "character_start": None, "character_end": None}]
    assert rejected.status_code == 201
    assert rejected.json()["planned_scope"]["coverage"] == "complete"


def test_rejects_oversized_file_without_leaving_artifact(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.post(
        "/api/v1/tasks",
        files={"file": ("large.png", b"x" * 1025, "image/png")},
    )

    assert response.status_code == 413
    assert list((tmp_path / "uploads").iterdir()) == []


def _pdf_bytes(tmp_path: Path, pages: int) -> bytes:
    path = tmp_path / f"{pages}-pages.pdf"
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            page = document.new_page(595, 842)
            page.close()
        document.save(path)
    finally:
        document.close()
    return path.read_bytes()


def test_pdf_page_count_is_validated_before_queueing(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'pdf-admission.db'}",
        storage_dir=tmp_path / "uploads",
        max_pdf_pages=2,
    )
    with TestClient(create_app(settings)) as test_client:
        accepted = test_client.post(
            "/api/v1/tasks",
            files={
                "file": (
                    "two-pages.pdf",
                    _pdf_bytes(tmp_path, 2),
                    "application/pdf",
                )
            },
        )
        rejected = test_client.post(
            "/api/v1/tasks",
            files={
                "file": (
                    "three-pages.pdf",
                    _pdf_bytes(tmp_path, 3),
                    "application/pdf",
                )
            },
        )

    assert accepted.status_code == 201
    assert accepted.json()["page_count"] == 2
    assert rejected.status_code == 201
    assert rejected.json()["planned_scope"]["coverage"] == "complete"
    assert len(list((tmp_path / "uploads").glob("*.pdf"))) == 2


def _multi_frame_image_bytes(*, frames: int, image_format: str = "TIFF") -> bytes:
    buffer = io.BytesIO()
    pages = [Image.new("RGB", (8, 8), (index * 50, 0, 0)) for index in range(frames)]
    pages[0].save(buffer, format=image_format, save_all=True, append_images=pages[1:])
    return buffer.getvalue()


def test_native_images_are_decoded_and_mime_spoofing_is_rejected(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'real-image-type.db'}",
        storage_dir=tmp_path / "uploads",
    )
    with TestClient(create_app(settings)) as test_client:
        corrupt = test_client.post(
            "/api/v1/tasks",
            files={"file": ("broken.png", b"not-an-image", "image/png")},
        )
        spoofed = test_client.post(
            "/api/v1/tasks",
            files={"file": ("renamed.png", JPEG_BYTES, "image/png")},
        )

    assert corrupt.status_code == 422
    assert "损坏" in corrupt.json()["detail"]
    assert spoofed.status_code == 422
    assert "实际格式" in spoofed.json()["detail"]
    assert not list((tmp_path / "uploads").glob("*"))


def test_apng_all_frames_are_counted_before_queueing(tmp_path: Path) -> None:
    content = _multi_frame_image_bytes(frames=2, image_format="PNG")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'apng.db'}",
        storage_dir=tmp_path / "uploads",
        max_pdf_pages=2,
    )
    with TestClient(create_app(settings)) as test_client:
        response = test_client.post(
            "/api/v1/tasks",
            files={"file": ("animated.png", content, "image/png")},
        )

    assert response.status_code == 201
    assert response.json()["page_count"] == 2


def test_image_pixel_filename_and_request_limits_reject_before_queueing(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'input-limits.db'}",
        storage_dir=tmp_path / "uploads",
        max_image_total_pixels=3,
        max_filename_chars=8,
        max_request_bytes=256,
    )
    with TestClient(create_app(settings)) as test_client:
        pixel_limit = test_client.post(
            "/api/v1/tasks",
            files={"file": ("tiny.png", PNG_BYTES, "image/png")},
        )
        long_name = test_client.post(
            "/api/v1/tasks",
            files={"file": ("too-long-name.png", PNG_BYTES, "image/png")},
        )
        request_limit = test_client.post(
            "/api/v1/tasks",
            files={"file": ("tiny.png", b"x" * 300, "image/png")},
        )

    assert pixel_limit.status_code == 422
    assert "总像素" in pixel_limit.json()["detail"]
    assert long_name.status_code == 422
    assert "文件名" in long_name.json()["detail"]
    assert request_limit.status_code == 413
    assert "请求内容" in request_limit.json()["detail"]
    assert not list((tmp_path / "uploads").glob("*"))


def test_multi_frame_image_page_count_and_limit_are_enforced_before_queueing(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'image-pages.db'}",
        storage_dir=tmp_path / "uploads",
        max_upload_bytes=1024 * 1024,
        max_pdf_pages=2,
    )
    with TestClient(create_app(settings)) as test_client:
        accepted = test_client.post(
            "/api/v1/tasks",
            files={"file": ("two.tiff", _multi_frame_image_bytes(frames=2), "image/tiff")},
        )
        rejected = test_client.post(
            "/api/v1/tasks",
            files={"file": ("three.gif", _multi_frame_image_bytes(frames=3, image_format="GIF"), "image/gif")},
        )

    assert accepted.status_code == 201
    assert accepted.json()["page_count"] == 2
    assert rejected.status_code == 201
    assert rejected.json()["planned_scope"]["coverage"] == "complete"
    assert len(list((tmp_path / "uploads").glob("*"))) == 2


def test_active_queue_capacity_rejects_upload_without_leaking_file(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'queue-capacity.db'}",
        storage_dir=tmp_path / "uploads",
        max_active_tasks=1,
    )
    with TestClient(create_app(settings)) as test_client:
        first = test_client.post(
            "/api/v1/tasks",
            files={"file": ("first.png", PNG_BYTES, "image/png")},
        )
        second = test_client.post(
            "/api/v1/tasks",
            files={"file": ("second.png", PNG_BYTES, "image/png")},
        )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.headers["retry-after"] == "5"
    assert "1 个文件" in second.json()["detail"]
    assert len(list((tmp_path / "uploads").glob("*.png"))) == 1


def test_retry_cannot_bypass_active_queue_capacity(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'retry-capacity.db'}",
        storage_dir=tmp_path / "uploads",
        max_active_tasks=1,
    )
    with TestClient(create_app(settings)) as test_client:
        with test_client.app.state.session_factory() as session:
            session.add_all(
                [
                    TaskRecord(
                        id="already-queued",
                        filename="queued.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="queued-capacity",
                        storage_path="queued.png",
                        template_mode="invoice",
                        status="queued",
                    ),
                    TaskRecord(
                        id="retry-blocked",
                        filename="failed.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="retry-capacity",
                        storage_path="failed.png",
                        template_mode="invoice",
                        status="failed",
                    ),
                ]
            )
            session.commit()

        response = test_client.post("/api/v1/tasks/retry-blocked/retry")

    assert response.status_code == 429


def test_active_byte_and_page_budgets_are_independent(tmp_path: Path) -> None:
    byte_settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'byte-capacity.db'}",
        storage_dir=tmp_path / "byte-uploads",
        max_active_tasks=10,
        max_active_bytes=9,
    )
    with TestClient(create_app(byte_settings)) as byte_client:
        assert byte_client.post(
            "/api/v1/tasks",
            files={"file": ("first.txt", b"12345", "text/plain")},
        ).status_code == 201
        byte_rejected = byte_client.post(
            "/api/v1/tasks",
            files={"file": ("second.txt", b"67890", "text/plain")},
        )

    page_settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'page-capacity.db'}",
        storage_dir=tmp_path / "page-uploads",
        max_active_tasks=10,
        max_active_pages=2,
    )
    with TestClient(create_app(page_settings)) as page_client:
        assert page_client.post(
            "/api/v1/tasks",
            files={
                "file": (
                    "two-pages.pdf",
                    _pdf_bytes(tmp_path, 2),
                    "application/pdf",
                )
            },
        ).status_code == 201
        page_rejected = page_client.post(
            "/api/v1/tasks",
            files={"file": ("extra.png", PNG_BYTES, "image/png")},
        )

    assert byte_rejected.status_code == 429
    assert page_rejected.status_code == 429


def test_template_selection_cannot_bypass_active_queue_capacity(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'template-capacity.db'}",
        storage_dir=tmp_path / "uploads",
        max_active_tasks=1,
    )
    with TestClient(create_app(settings)) as test_client:
        with test_client.app.state.session_factory() as session:
            session.add_all(
                [
                    TaskRecord(
                        id="capacity-occupied",
                        filename="queued.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="template-capacity-queued",
                        storage_path="queued.png",
                        template_mode="invoice",
                        status="queued",
                    ),
                    TaskRecord(
                        id="selection-blocked",
                        filename="waiting.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="template-capacity-waiting",
                        storage_path="waiting.png",
                        template_mode="smart",
                        candidate_templates_json=json.dumps(
                            [
                                {
                                    "id": "builtin-delivery",
                                    "version": 1,
                                    "name": "送货单",
                                    "description": "整理送货单",
                                }
                            ]
                        ),
                        status="waiting_for_template",
                    ),
                ]
            )
            session.commit()

        response = test_client.post(
            "/api/v1/tasks/selection-blocked/template",
            json={"template_id": "builtin-delivery"},
        )

    assert response.status_code == 429


def test_concurrent_uploads_cannot_both_cross_single_task_capacity(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'concurrent-capacity.db'}",
        storage_dir=tmp_path / "uploads",
        max_active_tasks=1,
    )
    with TestClient(create_app(settings)) as test_client:

        def upload(index: int) -> int:
            return test_client.post(
                "/api/v1/tasks",
                files={
                    "file": (
                        f"concurrent-{index}.png",
                        PNG_BYTES,
                        "image/png",
                    )
                },
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(upload, range(2)))

    assert sorted(statuses) == [201, 429]
    assert len(list((tmp_path / "uploads").glob("*.png"))) == 1


def test_queued_task_can_be_paused_and_resumed(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        files={"file": ("delivery.jpg", JPEG_BYTES, "image/jpeg")},
    ).json()

    paused = client.post(f"/api/v1/tasks/{created['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/v1/tasks/{created['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"


def test_completed_task_cannot_be_paused(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="done-task",
                filename="done.png",
                content_type="image/png",
                size_bytes=1,
                sha256="done-digest",
                storage_path="done.png",
                template_mode="invoice",
                status="completed",
            )
        )
        session.commit()

    response = client.post("/api/v1/tasks/done-task/pause")

    assert response.status_code == 409
    assert "暂停" in response.json()["detail"]


def test_resume_fails_when_queue_capacity_is_full(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'resume-capacity.db'}",
        storage_dir=tmp_path / "uploads",
        max_active_tasks=1,
    )
    with TestClient(create_app(settings)) as test_client:
        with test_client.app.state.session_factory() as session:
            session.add_all(
                [
                    TaskRecord(
                        id="paused-task",
                        filename="paused.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="paused-digest",
                        storage_path="paused.png",
                        template_mode="invoice",
                        status="paused",
                    ),
                    TaskRecord(
                        id="occupying-task",
                        filename="occupying.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256="occupying-digest",
                        storage_path="occupying.png",
                        template_mode="invoice",
                        status="queued",
                    ),
                ]
            )
            session.commit()

        response = test_client.post("/api/v1/tasks/paused-task/resume")

    assert response.status_code == 429


def test_failed_task_can_be_deleted_with_records_but_keeps_table_rows(
    client: TestClient,
    tmp_path: Path,
) -> None:
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
        session.add(
            TaskRecord(
                id="del-task",
                filename="gone.png",
                content_type="image/png",
                size_bytes=1,
                sha256="del-digest",
                storage_path="del-task.png",
                template_mode="invoice",
                status="failed",
            )
        )
        session.flush()
        session.add(
            ExtractionRecord(
                task_id="del-task",
                document_kind="invoice",
                template_id=None,
                template_version=None,
                model_name="fake",
                prompt_version="v1",
                rule_engine_version="v1",
                elapsed_seconds=0,
                result_json="{}",
                validation_json="[]",
                evidence_json="[]",
            )
        )
        session.add(
            ConfirmedDocumentRecord(
                task_id="del-task",
                table_id="table-1",
                review_version=0,
                result_json="{}",
            )
        )
        session.add(
            DataRowRecord(
                table_id="table-1",
                task_id="del-task",
                item_index=0,
                row_json="{}",
            )
        )
        session.commit()
        (tmp_path / "uploads" / "del-task.png").write_bytes(b"data")

    response = client.delete("/api/v1/tasks/del-task")

    assert response.status_code == 200
    assert response.json()["kept_rows"] == 1
    assert not (tmp_path / "uploads" / "del-task.png").exists()
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, "del-task") is None
        assert session.get(DataTableRecord, "table-1") is not None
        row = session.scalars(select(DataRowRecord)).one()
        assert row.task_id is None
        assert session.scalar(select(func.count()).select_from(ExtractionRecord)) == 0
        assert (
            session.scalar(select(func.count()).select_from(ConfirmedDocumentRecord))
            == 0
        )


def test_active_task_cannot_be_deleted(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="active-task",
                filename="active.png",
                content_type="image/png",
                size_bytes=1,
                sha256="active-digest",
                storage_path="active.png",
                template_mode="invoice",
                status="queued",
            )
        )
        session.commit()

    response = client.delete("/api/v1/tasks/active-task")

    assert response.status_code == 409
    assert "删除" in response.json()["detail"]


def test_waiting_for_template_and_paused_tasks_can_be_deleted(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="waiting-task",
                filename="w.png",
                content_type="image/png",
                size_bytes=1,
                sha256="w-digest",
                storage_path="w.png",
                template_mode="smart",
                status="waiting_for_template",
            )
        )
        session.add(
            TaskRecord(
                id="paused-task",
                filename="p.png",
                content_type="image/png",
                size_bytes=1,
                sha256="p-digest",
                storage_path="p.png",
                template_mode="smart",
                status="paused",
            )
        )
        session.commit()

    for task_id in ("waiting-task", "paused-task"):
        response = client.delete(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["kept_rows"] == 0

    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, "waiting-task") is None
        assert session.get(TaskRecord, "paused-task") is None


def test_batch_delete_tasks(client: TestClient) -> None:
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
        session.add(
            TaskRecord(
                id="failed-1",
                filename="f1.png",
                content_type="image/png",
                size_bytes=1,
                sha256="f1-digest",
                storage_path="f1.png",
                template_mode="smart",
                status="failed",
            )
        )
        session.add(
            TaskRecord(
                id="failed-2",
                filename="f2.png",
                content_type="image/png",
                size_bytes=1,
                sha256="f2-digest",
                storage_path="f2.png",
                template_mode="smart",
                status="failed",
            )
        )
        session.flush()
        session.add(
            DataRowRecord(
                table_id="table-1",
                task_id="failed-1",
                item_index=0,
                row_json="{}",
            )
        )
        session.commit()

    response = client.post(
        "/api/v1/tasks/batch-delete",
        json={"task_ids": ["failed-1", "failed-2"]},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": 2, "skipped": 0, "kept_rows": 1}
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, "failed-1") is None
        assert session.get(TaskRecord, "failed-2") is None
        row = session.scalars(select(DataRowRecord)).one()
        assert row.task_id is None


def _add_task(
    session,
    task_id: str,
    *,
    status: str,
    created_at: datetime,
) -> None:
    """直接落一条任务，created_at 显式指定，保证分页排序断言稳定。"""
    session.add(
        TaskRecord(
            id=task_id,
            filename=f"{task_id}.png",
            content_type="image/png",
            size_bytes=1,
            sha256=f"{task_id}-digest",
            storage_path=f"{task_id}.png",
            template_mode="smart",
            status=status,
            created_at=created_at,
        )
    )


def test_tasks_list_pagination_limits_and_offsets(client: TestClient) -> None:
    """ISSUE-067：limit/offset 按 created_at 倒序生效，不返回全量。"""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with client.app.state.session_factory() as session:
        for index in range(5):
            _add_task(
                session,
                f"task-{index}",
                status="completed",
                created_at=base + timedelta(minutes=index),
            )
        session.commit()

    # 默认页在小数据集上仍返回全部；大数据集会被默认 100 条上限保护。
    all_tasks = client.get("/api/v1/tasks").json()
    assert [t["id"] for t in all_tasks] == [f"task-{i}" for i in range(4, -1, -1)]

    page = client.get("/api/v1/tasks", params={"limit": 2}).json()
    assert [t["id"] for t in page] == ["task-4", "task-3"]

    page = client.get("/api/v1/tasks", params={"limit": 2, "offset": 2}).json()
    assert [t["id"] for t in page] == ["task-2", "task-1"]

    page = client.get("/api/v1/tasks", params={"limit": 2, "offset": 4}).json()
    assert [t["id"] for t in page] == ["task-0"]

    page = client.get("/api/v1/tasks", params={"limit": 2, "offset": 10}).json()
    assert page == []

    assert client.get("/api/v1/tasks", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/tasks", params={"limit": -1}).status_code == 422
    assert client.get("/api/v1/tasks", params={"limit": 1001}).status_code == 422
    assert client.get("/api/v1/tasks", params={"offset": -1}).status_code == 422


def test_tasks_default_page_is_bounded_and_order_is_stable(client: TestClient) -> None:
    """省略 limit 也有资源上限；同时间任务用 id 稳定排序。"""
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with client.app.state.session_factory() as session:
        for index in range(105):
            _add_task(
                session,
                f"bounded-{index:03d}",
                status="completed",
                created_at=created_at,
            )
        session.commit()

    first = client.get("/api/v1/tasks")
    assert first.status_code == 200
    assert len(first.json()) == 100
    assert [item["id"] for item in first.json()[:2]] == [
        "bounded-104",
        "bounded-103",
    ]
    second = client.get("/api/v1/tasks", params={"offset": 100})
    assert [item["id"] for item in second.json()] == [
        f"bounded-{index:03d}" for index in range(4, -1, -1)
    ]


def test_tasks_active_only_filters_terminal_states(client: TestClient) -> None:
    """ISSUE-067：active_only 只返回仍在活动的任务，跳过终态（完成/失败/取消）。"""
    base = datetime(2026, 1, 2, tzinfo=timezone.utc)
    states = {
        "active-queued": "queued",
        "active-review": "needs_review",
        "active-paused": "paused",
        "done-completed": "completed",
        "done-failed": "failed",
        "done-cancelled": "cancelled",
    }
    with client.app.state.session_factory() as session:
        for index, (task_id, status) in enumerate(states.items()):
            _add_task(
                session,
                task_id,
                status=status,
                created_at=base + timedelta(minutes=index),
            )
        session.commit()

    active = client.get("/api/v1/tasks", params={"active_only": True}).json()
    ids = [t["id"] for t in active]
    assert set(ids) == {"active-queued", "active-review", "active-paused"}
    # active_only 跳过快照计数，record_count 恒为 0（不触碰 extraction 全表）
    assert all(t["record_count"] == 0 for t in active)


def test_tasks_record_count_limited_to_current_page(client: TestClient) -> None:
    """ISSUE-067：非 active_only 时 record_count 只对本页任务做快照计数。"""
    base = datetime(2026, 1, 3, tzinfo=timezone.utc)
    with client.app.state.session_factory() as session:
        for index in range(3):
            _add_task(
                session,
                f"page-task-{index}",
                status="completed",
                created_at=base + timedelta(minutes=index),
            )
        session.flush()
        # 只有最新一个任务有提取快照：2 条明细
        session.add(
            ExtractionRecord(
                task_id="page-task-2",
                document_kind="invoice",
                model_name="test-model",
                prompt_version="test-v1",
                elapsed_seconds=0.5,
                result_json=json.dumps({"items": [{"name": "a"}, {"name": "b"}]}),
                validation_json="[]",
            )
        )
        session.commit()

    page = client.get("/api/v1/tasks", params={"limit": 2}).json()
    by_id = {t["id"]: t for t in page}
    assert by_id["page-task-2"]["record_count"] == 2
    assert by_id["page-task-2"]["processing_elapsed_seconds"] == 0.5
    assert by_id["page-task-1"]["record_count"] == 0
    assert by_id["page-task-1"]["processing_elapsed_seconds"] is None

    # 第二页只含 page-task-0：无快照 → record_count 0
    page2 = client.get(
        "/api/v1/tasks", params={"limit": 2, "offset": 2}
    ).json()
    assert [t["id"] for t in page2] == ["page-task-0"]
    assert page2[0]["record_count"] == 0


def test_tasks_list_server_side_filters(client: TestClient) -> None:
    """ISSUE-067：status/search/template_id/since 服务端筛选生效。"""
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="inv-a",
                filename="invoice-A.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="sha-a",
                storage_path="a.pdf",
                template_mode="invoice",
                template_id="builtin-invoice",
                status="completed",
                created_at=base,
                updated_at=base,
            )
        )
        session.add(
            TaskRecord(
                id="inv-b",
                filename="invoice-B.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="sha-b",
                storage_path="b.pdf",
                template_mode="invoice",
                template_id="builtin-invoice",
                status="needs_review",
                created_at=base + timedelta(minutes=1),
                updated_at=base + timedelta(days=1),
            )
        )
        session.add(
            TaskRecord(
                id="delivery-c",
                filename="delivery-C.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="sha-c",
                storage_path="c.pdf",
                template_mode="delivery",
                template_id="builtin-delivery",
                status="completed",
                created_at=base + timedelta(minutes=2),
                updated_at=base + timedelta(days=30),
            )
        )
        session.commit()

    # status 筛选
    page = client.get("/api/v1/tasks", params={"status": "needs_review"}).json()
    assert [t["id"] for t in page] == ["inv-b"]

    # search 文件名（不区分大小写）
    page = client.get("/api/v1/tasks", params={"search": "invoice"}).json()
    assert {t["id"] for t in page} == {"inv-a", "inv-b"}

    # template_id 筛选
    page = client.get("/api/v1/tasks", params={"template_id": "builtin-delivery"}).json()
    assert [t["id"] for t in page] == ["delivery-c"]

    # since（按 updated_at 过滤）
    page = client.get(
        "/api/v1/tasks",
        params={"since": (base + timedelta(days=2)).isoformat()},
    ).json()
    assert [t["id"] for t in page] == ["delivery-c"]

    # 组合筛选：status + search
    page = client.get(
        "/api/v1/tasks",
        params={"status": "completed", "search": "invoice"},
    ).json()
    assert [t["id"] for t in page] == ["inv-a"]


def test_tasks_summary_counts_by_status(client: TestClient) -> None:
    """ISSUE-067：summary 按状态聚合计数，支持与列表相同的筛选。"""
    base = datetime(2026, 1, 11, tzinfo=timezone.utc)
    states = {
        "c1": "completed",
        "c2": "completed",
        "r1": "needs_review",
        "f1": "failed",
        "q1": "queued",
    }
    with client.app.state.session_factory() as session:
        for index, (task_id, status) in enumerate(states.items()):
            _add_task(
                session,
                task_id,
                status=status,
                created_at=base + timedelta(minutes=index),
            )
        session.commit()

    summary = client.get("/api/v1/tasks/summary").json()
    assert summary == {"total": 5, "completed": 2, "needs_review": 1, "failed": 1, "active": 2, "waiting_for_action": 0, "pending_exports": 0}

    # 带文件名筛选：只统计命中子集（c1/c2 文件名含 "c"）
    filtered = client.get("/api/v1/tasks/summary", params={"search": "c"}).json()
    assert filtered == {"total": 2, "completed": 2, "needs_review": 0, "failed": 0, "active": 0, "waiting_for_action": 0, "pending_exports": 0}


def test_summary_counts_copy_actions_beyond_list_limit_and_excludes_model_failures(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        for index in range(1005):
            task_id = f"export-{index:04d}"
            _add_task(session, task_id, status="completed", created_at=datetime(2026, 1, 11, tzinfo=timezone.utc))
            session.flush()
            session.get(TaskRecord, task_id).export_state_json = json.dumps({"status": "failed"})
        _add_task(session, "waiting", status="waiting_for_template", created_at=datetime(2026, 1, 11, tzinfo=timezone.utc))
        _add_task(session, "model-failure", status="failed", created_at=datetime(2026, 1, 11, tzinfo=timezone.utc))
        session.commit()
    summary = client.get("/api/v1/tasks/summary").json()
    assert summary["pending_exports"] == 1005
    assert summary["waiting_for_action"] == 1
    assert summary["failed"] == 1
    assert summary["completed"] == 1005
    filtered = client.get("/api/v1/tasks/summary", params={"search": "export-0000"}).json()
    assert filtered["pending_exports"] == 1
    assert filtered["waiting_for_action"] == 0
    first = client.get("/api/v1/tasks", params={"export_pending": True, "limit": 1000}).json()
    last = client.get("/api/v1/tasks", params={"export_pending": True, "limit": 1000, "offset": 1000}).json()
    assert len(first) == 1000 and len(last) == 5
    assert len({task["id"] for task in first + last}) == 1005
