import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import (
    ConfirmedDocumentRecord,
    DataRowRecord,
    DataTableRecord,
    ExtractionRecord,
)
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.model_providers.base import ModelTimeoutError
from document_pipeline_api.schemas.extraction import (
    DocumentExtraction,
    ReviewUpdate,
    TemplateExtraction,
)
from document_pipeline_api.schemas.templates import TemplateCreate
from document_pipeline_api.services.data_tables import confirm_task, materialize_task_result
from document_pipeline_api.services.extraction import process_task, save_review
from document_pipeline_api.services.rule_evaluation import RuleEvaluation
from document_pipeline_api.services.system_settings import set_bool_setting
from document_pipeline_api.services.task_leases import (
    acquire_task_lease,
    recover_expired_task_leases,
    transition_leased_task,
)
from document_pipeline_api.services.templates import create_template
from image_test_data import PNG_BYTES


class StubModelClient:
    model_name = "test-model"

    def __init__(self, *, total_amount: float = 106) -> None:
        self.total_amount = total_amount

    def extract_image(self, _path, _prompt, result_type):
        return result_type.model_validate(
            {
                "document_type": "发票",
                "seller_name": "销售方",
                "buyer_name": "购买方",
                "document_number": "NO-1",
                "document_date": "2026-07-30",
                "amount_before_tax": 100,
                "tax_amount": 6,
                "total_amount": self.total_amount,
                "items": [
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
            }
        )

    def close(self) -> None:
        pass


def test_wrong_target_table_keeps_extraction_and_reselect_confirms_without_rerun(
    tmp_path: Path,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'wrong-target.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "wrong-target.png"
    image.write_bytes(b"image")
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'wrong-target.db'}", storage_dir=tmp_path)

    with Session(engine) as session:
        delivery = DataTableRecord(id="delivery-table", name="送货单", template_key="delivery", template_version="builtin-v1", document_kind="delivery")
        invoice = DataTableRecord(id="invoice-table", name="发票", template_key="invoice", template_version="builtin-v1", document_kind="invoice")
        task = TaskRecord(id="wrong-target", filename="invoice.png", content_type="image/png", size_bytes=5, sha256="wrong-target", storage_path=str(image), template_mode="invoice", target_table_id=delivery.id, status=TaskStatus.QUEUED.value)
        session.add_all([delivery, invoice, task])
        session.commit()

        extraction = process_task(session, settings, task.id, client=StubModelClient())
        assert extraction is not None
        saved = session.get(TaskRecord, task.id)
        assert saved is not None and saved.status == TaskStatus.WAITING_FOR_TEMPLATE.value
        assert "提取出字段与指定表不符" in saved.candidate_templates_json
        assert session.get(ExtractionRecord, task.id) is not None
        assert session.scalar(select(func.count(DataRowRecord.id)).where(DataRowRecord.task_id == task.id)) == 0

        confirmation = confirm_task(session, task.id, 0, target_table_id=invoice.id)
        assert confirmation.table_id == invoice.id
        assert session.get(TaskRecord, task.id).status == TaskStatus.COMPLETED.value
        assert session.scalar(select(func.count(DataRowRecord.id)).where(DataRowRecord.task_id == task.id)) == 1

    engine.dispose()


class SmartTemplateClient:
    model_name = "test-model"

    def __init__(self, decision: dict, *, score: float = 98.5) -> None:
        self.decision = decision
        self.score = score

    def extract_image(self, _path, _prompt, result_type):
        if result_type.__name__ == "TemplateMatchDecision":
            return result_type.model_validate(self.decision)
        return result_type.model_validate(
            {
                "header": {"store_name": "一号门店"},
                "items": [{"score": self.score}],
            }
        )


class TimeoutModelClient:
    model_name = "slow-model"

    def extract_image(self, _path, _prompt, _result_type):
        raise ModelTimeoutError("模型理解文件超时，请重试或更换模型。")


class StorageFailureModelClient:
    model_name = "storage-failure-model"

    def extract_image(self, _path, _prompt, _result_type):
        raise OSError("simulated unreadable storage")


class MultiPageModelClient(StubModelClient):
    def __init__(self) -> None:
        super().__init__()
        self.page_names: list[str] = []

    def extract_image(self, path, prompt, result_type):
        self.page_names = [path.name]
        return super().extract_image(path, prompt, result_type)

    def extract_images(self, paths, prompt, result_type):
        self.page_names = [path.name for path in paths]
        return super().extract_image(paths[0], prompt, result_type)


def test_process_task_persists_valid_result_and_enters_table(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "task-1.png"
    image.write_bytes(b"image")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path,
        model_name="test-model",
    )

    with Session(engine) as session:
        task = TaskRecord(
            id="task-1",
            filename="invoice.png",
            content_type="image/png",
            size_bytes=5,
            sha256="digest",
            storage_path=str(image),
            template_mode="invoice",
            status=TaskStatus.QUEUED.value,
        )
        session.add(task)
        session.commit()

        extraction = process_task(
            session,
            settings,
            task.id,
            client=StubModelClient(),
        )

        assert extraction is not None
        assert extraction.result.total_amount == 106
        assert extraction.validation_issues == []
        seller_evidence = next(
            item
            for item in extraction.evidence
            if item.field_path == "seller_name"
        )
        assert seller_evidence.page_number == 1
        assert seller_evidence.status == "page_only"
        assert seller_evidence.location_verified is True
        saved_task = session.get(TaskRecord, task.id)
        assert saved_task is not None
        assert saved_task.status == TaskStatus.COMPLETED.value
        assert saved_task.attempt_count == 1
        assert saved_task.lease_token is None
        assert saved_task.lease_expires_at is None

        with pytest.raises(HTTPException) as duplicate:
            process_task(
                session,
                settings,
                task.id,
                client=StubModelClient(),
            )
        assert duplicate.value.status_code == 409
        assert session.scalar(
            select(func.count(DataRowRecord.id)).where(
                DataRowRecord.task_id == task.id
            )
        ) == 1


def test_worker_builds_provider_and_lease_from_queued_task_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'snapshot.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "snapshot-worker.png"
    image.write_bytes(b"image")
    startup_settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'snapshot.db'}",
        storage_dir=tmp_path,
        model_provider="lm_studio",
        model_base_url="http://later.example/v1",
        model_name="later-model",
        model_timeout_seconds=180,
    )
    captured = {}

    def build_from_snapshot(settings, *, timeout_seconds):
        captured["provider"] = settings.model_provider
        captured["base_url"] = settings.model_base_url
        captured["model"] = settings.model_name
        captured["timeout"] = timeout_seconds
        return StubModelClient()

    monkeypatch.setattr(
        "document_pipeline_api.services.extraction.build_model_provider",
        build_from_snapshot,
    )
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id="snapshot-worker",
                filename="snapshot.png",
                content_type="image/png",
                size_bytes=5,
                sha256="snapshot-digest",
                storage_path=str(image),
                template_mode="invoice",
                status=TaskStatus.QUEUED.value,
                model_config_version="environment-v1",
                model_provider="ollama",
                model_base_url="http://snapshot.example",
                model_name="snapshot-model",
                model_timeout_seconds=42,
                model_secret_ref="environment",
            )
        )
        session.commit()

        assert process_task(session, startup_settings, "snapshot-worker") is not None

    assert captured == {
        "provider": "ollama",
        "base_url": "http://snapshot.example",
        "model": "snapshot-model",
        "timeout": 42,
    }
    engine.dispose()


def test_invalid_model_snapshot_becomes_predictable_failed_task(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'invalid-snapshot.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'invalid-snapshot.db'}",
        storage_dir=tmp_path,
    )
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id="invalid-snapshot",
                filename="invalid.png",
                content_type="image/png",
                size_bytes=1,
                sha256="invalid-snapshot-digest",
                storage_path=str(tmp_path / "invalid.png"),
                status=TaskStatus.QUEUED.value,
                model_config_version="environment-v1",
                model_provider="lm_studio",
                model_base_url="http://127.0.0.1:1234/v1",
                model_name=None,
                model_timeout_seconds=30,
                model_secret_ref="environment",
            )
        )
        session.commit()

        assert process_task(session, settings, "invalid-snapshot") is None
        failed = session.get(TaskRecord, "invalid-snapshot")
        assert failed is not None
        assert failed.status == TaskStatus.FAILED.value
        assert failed.failure_code == "model_configuration_invalid"
        assert failed.lease_token is None

    engine.dispose()


def test_process_task_materializes_invalid_result_before_review(
    tmp_path: Path,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "needs-review.png"
    image.write_bytes(b"image")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'review.db'}",
        storage_dir=tmp_path,
        model_name="test-model",
    )

    with Session(engine) as session:
        task = TaskRecord(
            id="needs-review",
            filename="invoice.png",
            content_type="image/png",
            size_bytes=5,
            sha256="review-digest",
            storage_path=str(image),
            template_mode="invoice",
            status=TaskStatus.QUEUED.value,
        )
        session.add(task)
        session.commit()

        extraction = process_task(
            session,
            settings,
            task.id,
            client=StubModelClient(total_amount=999),
        )

        assert extraction is not None
        assert extraction.validation_issues
        saved_task = session.get(TaskRecord, task.id)
        assert saved_task is not None
        assert saved_task.status == TaskStatus.NEEDS_REVIEW.value
        assert saved_task.lease_token is None
        assert session.scalar(
            select(func.count(DataRowRecord.id)).where(
                DataRowRecord.task_id == task.id
            )
        ) == 1


def test_crash_before_final_commit_leaves_no_partial_extraction_or_fact_rows(
    tmp_path: Path,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'pre-commit-crash.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id="pre-commit-crash",
                filename="invoice.png",
                content_type="image/png",
                size_bytes=5,
                sha256="pre-commit-crash-digest",
                storage_path=str(tmp_path / "invoice.png"),
                template_mode="invoice",
                status=TaskStatus.VALIDATING.value,
                lease_token="active-token",
            )
        )
        session.commit()
        result = StubModelClient().extract_image(
            tmp_path / "invoice.png",
            "",
            DocumentExtraction,
        )
        session.add(
            ExtractionRecord(
                task_id="pre-commit-crash",
                document_kind="invoice",
                model_name="test-model",
                prompt_version="test-prompt",
                elapsed_seconds=1,
                result_json=result.model_dump_json(),
                validation_json="[]",
            )
        )
        session.flush()

        materialize_task_result(session, "pre-commit-crash")

        assert session.get(ExtractionRecord, "pre-commit-crash") is not None
        assert session.scalar(select(func.count(DataRowRecord.id))) == 1
        session.rollback()

    with Session(engine) as session:
        task = session.get(TaskRecord, "pre-commit-crash")
        assert task is not None
        assert task.status == TaskStatus.VALIDATING.value
        assert task.lease_token == "active-token"
        assert session.get(ExtractionRecord, "pre-commit-crash") is None
        assert session.scalar(select(func.count(DataRowRecord.id))) == 0


def test_smart_match_uses_custom_template_schema_and_version(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'custom.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "custom.png"
    image.write_bytes(b"image")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'custom.db'}",
        storage_dir=tmp_path,
    )

    with Session(engine) as session:
        template = create_template(session, _custom_template())
        task = _add_task(session, image, "smart-custom")

        extraction = process_task(
            session,
            settings,
            task.id,
            client=SmartTemplateClient(
                {"outcome": "matched", "template_ids": [template.id]}
            ),
        )

        assert extraction is not None
        assert extraction.document_kind.value == "custom"
        assert extraction.template_id == template.id
        assert extraction.template_version == 1
        assert extraction.result.header == {"store_name": "一号门店"}
        saved_task = session.get(TaskRecord, task.id)
        assert saved_task.template_id == template.id
        assert saved_task.template_version == 1

        invalid_task = _add_task(session, image, "smart-custom-invalid")
        invalid = process_task(
            session,
            settings,
            invalid_task.id,
            client=SmartTemplateClient(
                {"outcome": "matched", "template_ids": [template.id]},
                score=120,
            ),
        )
        assert invalid is not None
        assert invalid.validation_issues[0].field == "items[0].score"
        assert "不能大于 100" in invalid.validation_issues[0].message
        assert session.get(TaskRecord, invalid_task.id).status == "needs_review"

        corrected = save_review(
            session,
            invalid_task.id,
            ReviewUpdate(
                expected_version=0,
                result=TemplateExtraction(
                    header={"store_name": "一号门店"},
                    items=[{"score": 99}],
                ),
            ),
        )
        assert corrected.validation_issues == []
        assert session.get(TaskRecord, invalid_task.id).status == "completed"


def test_ambiguous_match_persists_versioned_candidates(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'ambiguous.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "ambiguous.png"
    image.write_bytes(b"image")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ambiguous.db'}",
        storage_dir=tmp_path,
    )

    with Session(engine) as session:
        custom = create_template(session, _custom_template())
        task = _add_task(session, image, "smart-ambiguous")

        result = process_task(
            session,
            settings,
            task.id,
            client=SmartTemplateClient(
                {
                    "outcome": "ambiguous",
                    "template_ids": ["builtin-delivery", custom.id],
                }
            ),
        )

        assert result is None
        saved_task = session.get(TaskRecord, task.id)
        assert saved_task.status == TaskStatus.WAITING_FOR_TEMPLATE.value
        assert saved_task.lease_token is None
        assert json.loads(saved_task.candidate_templates_json) == [
            {
                "id": "builtin-delivery",
                "version": 1,
                "name": "送货单",
                "description": "整理供货方、收货方、单号、日期、合计和货品明细。",
            },
            {
                "id": custom.id,
                "version": 1,
                "name": "门店评分表",
                "description": "整理门店评分记录",
            },
        ]


def test_old_worker_cannot_write_after_task_is_reclaimed(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'fenced.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "fenced-task.png"
    image.write_bytes(b"image")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'fenced.db'}",
        storage_dir=tmp_path,
        model_name="test-model",
    )

    class ReclaimingClient(StubModelClient):
        def extract_image(self, path, prompt, result_type):
            with Session(engine) as other_session:
                task = other_session.get(TaskRecord, "fenced-task")
                assert task is not None
                assert task.lease_expires_at is not None
                recovered = recover_expired_task_leases(
                    other_session,
                    now=task.lease_expires_at + timedelta(seconds=1),
                )
                assert recovered == ["fenced-task"]
                task = other_session.get(TaskRecord, "fenced-task")
                assert task is not None
                task.status = TaskStatus.QUEUED.value
                other_session.commit()
                replacement = acquire_task_lease(
                    other_session,
                    "fenced-task",
                    lease_for=timedelta(minutes=4),
                )
                assert replacement is not None
            return super().extract_image(path, prompt, result_type)

    with Session(engine) as session:
        session.add(
            TaskRecord(
                id="fenced-task",
                filename="invoice.png",
                content_type="image/png",
                size_bytes=5,
                sha256="fenced-digest",
                storage_path=str(image),
                template_mode="invoice",
                status=TaskStatus.QUEUED.value,
            )
        )
        session.commit()

        result = process_task(
            session,
            settings,
            "fenced-task",
            client=ReclaimingClient(),
        )

        assert result is None
        assert session.get(ExtractionRecord, "fenced-task") is None
        task = session.get(TaskRecord, "fenced-task")
        assert task is not None
        assert task.status == TaskStatus.PROCESSING.value
        assert task.attempt_count == 2


def test_old_worker_cannot_auto_confirm_after_validation_lease_is_reclaimed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'confirm-fenced.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "confirm-fenced.png"
    image.write_bytes(b"image")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'confirm-fenced.db'}",
        storage_dir=tmp_path,
        model_name="test-model",
    )
    replacement_tokens: list[str] = []

    def reclaim_before_final_write(*_args, **_kwargs):
        with Session(engine) as other_session:
            task = other_session.get(TaskRecord, "confirm-fenced")
            assert task is not None
            assert task.status == TaskStatus.VALIDATING.value
            assert task.lease_expires_at is not None
            assert recover_expired_task_leases(
                other_session,
                now=task.lease_expires_at + timedelta(seconds=1),
            ) == ["confirm-fenced"]
            task = other_session.get(TaskRecord, "confirm-fenced")
            assert task is not None
            task.status = TaskStatus.QUEUED.value
            other_session.commit()
            replacement = acquire_task_lease(
                other_session,
                "confirm-fenced",
                lease_for=timedelta(minutes=4),
            )
            assert replacement is not None
            replacement_tokens.append(replacement.token)
            assert transition_leased_task(
                other_session,
                "confirm-fenced",
                replacement.token,
                expected=TaskStatus.PROCESSING,
                target=TaskStatus.VALIDATING,
                lease_for=timedelta(minutes=1),
            )
        return RuleEvaluation(engine_version="builtin-v1", issues=[])

    monkeypatch.setattr(
        "document_pipeline_api.services.extraction.evaluate_rules",
        reclaim_before_final_write,
    )

    with Session(engine) as session:
        session.add(
            TaskRecord(
                id="confirm-fenced",
                filename="invoice.png",
                content_type="image/png",
                size_bytes=5,
                sha256="confirm-fenced-digest",
                storage_path=str(image),
                template_mode="invoice",
                status=TaskStatus.QUEUED.value,
            )
        )
        session.commit()

        result = process_task(
            session,
            settings,
            "confirm-fenced",
            client=StubModelClient(),
        )

        assert result is None
        session.expire_all()
        task = session.get(TaskRecord, "confirm-fenced")
        assert task is not None
        assert task.status == TaskStatus.VALIDATING.value
        assert task.lease_token == replacement_tokens[0]
        assert task.attempt_count == 2
        assert session.get(ExtractionRecord, "confirm-fenced") is None
        assert session.get(ConfirmedDocumentRecord, "confirm-fenced") is None
        assert session.scalar(select(func.count(DataRowRecord.id))) == 0


def test_model_failure_is_persisted_without_exposing_internal_details(
    tmp_path: Path,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'timeout.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "invoice.png"
    image.write_bytes(b"image")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'timeout.db'}",
        storage_dir=tmp_path,
    )
    with Session(engine) as session:
        task = _add_task(session, image, "timeout-task")

        try:
            process_task(
                session,
                settings,
                task.id,
                client=TimeoutModelClient(),
            )
        except ModelTimeoutError:
            pass
        else:
            raise AssertionError("模型超时必须返回类型化错误。")

        saved = session.get(TaskRecord, task.id)
        assert saved is not None
        assert saved.status == TaskStatus.FAILED.value
        assert saved.failure_code == "model_timeout"
        assert saved.failure_message == "模型理解文件超时，请重试或更换模型。"
        assert saved.lease_token is None


def test_worker_storage_failure_has_distinct_actionable_status(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'storage-failure.db'}")
    Base.metadata.create_all(engine)
    image = tmp_path / "invoice.png"
    image.write_bytes(b"image")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'storage-failure.db'}",
        storage_dir=tmp_path,
    )
    with Session(engine) as session:
        task = _add_task(session, image, "storage-failure")

        with pytest.raises(OSError):
            process_task(
                session,
                settings,
                task.id,
                client=StorageFailureModelClient(),
            )

        saved = session.get(TaskRecord, task.id)
        assert saved is not None
        assert saved.status == TaskStatus.FAILED.value
        assert saved.failure_code == "storage_unavailable"
        assert "磁盘空间" in (saved.failure_message or "")
        assert saved.lease_token is None


def test_multi_page_pdf_is_understood_as_one_document(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'multi-page.db'}")
    Base.metadata.create_all(engine)
    pdf_path = tmp_path / "multi-page.pdf"
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(2):
            page = document.new_page(595, 842)
            page.close()
        document.save(pdf_path)
    finally:
        document.close()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'multi-page.db'}",
        storage_dir=tmp_path,
        max_pdf_pages=3,
    )
    client = MultiPageModelClient()
    with Session(engine) as session:
        task = TaskRecord(
            id="multi-page",
            filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
            page_count=2,
            sha256="multi-page-digest",
            storage_path=str(pdf_path),
            template_mode="invoice",
            status=TaskStatus.QUEUED.value,
        )
        session.add(task)
        session.commit()

        result = process_task(session, settings, task.id, client=client)

        assert result is not None
        assert client.page_names == ["page-1.png", "page-2.png"]
        assert result.evidence
        assert {item.status for item in result.evidence} == {"unavailable"}
        assert all(item.page_number is None for item in result.evidence)
        assert session.get(TaskRecord, task.id).status == TaskStatus.COMPLETED.value


def test_multi_frame_tiff_is_understood_as_one_document(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'multi-frame.db'}")
    Base.metadata.create_all(engine)
    tiff_path = tmp_path / "multi-frame.tiff"
    first = Image.new("RGB", (16, 16), "red")
    second = Image.new("RGB", (16, 16), "blue")
    first.save(tiff_path, format="TIFF", save_all=True, append_images=[second])
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'multi-frame.db'}",
        storage_dir=tmp_path,
        max_pdf_pages=3,
    )
    client = MultiPageModelClient()
    with Session(engine) as session:
        task = TaskRecord(
            id="multi-frame",
            filename="invoice.tiff",
            content_type="image/tiff",
            size_bytes=tiff_path.stat().st_size,
            page_count=2,
            sha256="multi-frame-digest",
            storage_path=str(tiff_path),
            template_mode="invoice",
            status=TaskStatus.QUEUED.value,
        )
        session.add(task)
        session.commit()

        result = process_task(session, settings, task.id, client=client)

        assert result is not None
        assert client.page_names == [
            "multi-frame-image-1.png",
            "multi-frame-image-2.png",
        ]
        assert session.get(TaskRecord, task.id).status == TaskStatus.COMPLETED.value


def test_docx_embedded_images_follow_word_include_images_setting(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'docx-images.db'}")
    Base.metadata.create_all(engine)
    buffer = io.BytesIO()
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>发票正文</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/media/image1.png", PNG_BYTES)
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'docx-images.db'}",
        storage_dir=tmp_path,
        model_name="test-model",
        max_pdf_pages=3,
    )

    def run_task(task_id: str) -> MultiPageModelClient:
        client = MultiPageModelClient()
        task_docx = tmp_path / f"{task_id}.docx"
        task_docx.write_bytes(buffer.getvalue())
        with Session(engine) as session:
            session.add(
                TaskRecord(
                    id=task_id,
                    filename="invoice.docx",
                    content_type=docx_mime,
                    size_bytes=task_docx.stat().st_size,
                    sha256=task_id,
                    storage_path=str(task_docx),
                    template_mode="invoice",
                    status=TaskStatus.QUEUED.value,
                )
            )
            session.commit()
            result = process_task(session, settings, task_id, client=client)
            assert result is not None
        return client

    # 开启：文字页与内嵌图片一起交给模型
    with Session(engine) as session:
        set_bool_setting(session, "word_include_images", True)
        session.commit()
    with_images = run_task("docx-with-images")
    assert with_images.page_names
    assert any("-docx-image-" in name for name in with_images.page_names)

    # 关闭：只提取文本，不带内嵌图片
    with Session(engine) as session:
        set_bool_setting(session, "word_include_images", False)
        session.commit()
    text_only = run_task("docx-text-only")
    assert text_only.page_names
    assert all("-docx-image-" not in name for name in text_only.page_names)


def _add_task(session: Session, image: Path, task_id: str) -> TaskRecord:
    stored_image = image.with_name(f"{task_id}.png")
    if stored_image != image:
        stored_image.write_bytes(image.read_bytes())
    task = TaskRecord(
        id=task_id,
        filename=image.name,
        content_type="image/png",
        size_bytes=5,
        sha256=task_id,
        storage_path=str(stored_image),
        template_mode="smart",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.commit()
    return task


def _custom_template() -> TemplateCreate:
    return TemplateCreate.model_validate(
        {
            "name": "门店评分表",
            "description": "整理门店评分记录",
            "extra_instructions": "无法判断时留空",
            "fields": [
                {"key": "store_name", "label": "门店", "section": "header"},
                {
                    "key": "score",
                    "label": "得分",
                    "section": "item",
                    "value_type": "number",
                },
            ],
            "validation_rules": [],
            "deterministic_rules": [
                {
                    "kind": "range",
                    "field": "items[].score",
                    "minimum": 0,
                    "maximum": 100,
                }
            ],
            "output_mapping": {},
        }
    )
