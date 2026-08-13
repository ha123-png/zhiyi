from datetime import timedelta
import multiprocessing
from pathlib import Path
import time

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import (
    ConfirmedDocumentRecord,
    DataRowRecord,
    ExtractionRecord,
    TaskRecord,
)
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.services.task_leases import recover_expired_task_leases
from document_pipeline_api.services.tasks import retry_task


class _BlockingModelClient:
    model_name = "blocking-test-model"

    def __init__(self, started) -> None:
        self.started = started

    def extract_image(self, _path, _prompt, _result_type):
        self.started.set()
        time.sleep(3600)
        raise AssertionError("阻塞模型不应自然返回。")


class _SuccessfulModelClient:
    model_name = "successful-test-model"

    def extract_image(self, _path, _prompt, result_type):
        return result_type.model_validate(
            {
                "document_type": "发票",
                "seller_name": "销售方",
                "buyer_name": "购买方",
                "document_number": "RECOVERED-1",
                "document_date": "2026-08-01",
                "amount_before_tax": 100,
                "tax_amount": 6,
                "total_amount": 106,
                "items": [
                    {
                        "name": "恢复后的明细",
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


def _run_blocked_worker(
    database_url: str,
    storage_dir: str,
    task_id: str,
    started,
) -> None:
    engine = build_engine(database_url)
    settings = Settings(
        database_url=database_url,
        storage_dir=Path(storage_dir),
        model_timeout_seconds=30,
    )
    try:
        with Session(engine) as session:
            process_task(
                session,
                settings,
                task_id,
                client=_BlockingModelClient(started),
            )
    finally:
        engine.dispose()


def test_force_killed_worker_recovers_and_retries_without_duplicate_facts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "worker-kill.db"
    database_url = f"sqlite:///{database}"
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    source_file = storage_dir / "force-killed.png"
    source_file.write_bytes(b"image")
    settings = Settings(
        database_url=database_url,
        storage_dir=storage_dir,
        model_timeout_seconds=30,
    )
    engine = build_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id="force-killed",
                filename="source.png",
                content_type="image/png",
                size_bytes=5,
                sha256="force-killed-digest",
                storage_path=str(source_file),
                template_mode="invoice",
                status=TaskStatus.QUEUED.value,
            )
        )
        session.commit()

    context = multiprocessing.get_context("spawn")
    started = context.Event()
    worker = context.Process(
        target=_run_blocked_worker,
        args=(database_url, str(storage_dir), "force-killed", started),
        name="document-pipeline-isolated-recovery-test",
    )
    worker.start()
    try:
        assert worker.pid is not None and worker.pid > 4
        assert started.wait(timeout=10), "专用 Worker 未在 10 秒内进入模型调用。"
        with Session(engine) as session:
            processing = session.get(TaskRecord, "force-killed")
            assert processing is not None
            assert processing.status == TaskStatus.PROCESSING.value
            assert processing.lease_token is not None
            assert processing.lease_expires_at is not None
            assert session.get(ExtractionRecord, "force-killed") is None

        worker.terminate()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert worker.exitcode is not None and worker.exitcode != 0
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=5)

    with Session(engine) as session:
        interrupted = session.get(TaskRecord, "force-killed")
        assert interrupted is not None
        assert interrupted.lease_expires_at is not None
        assert recover_expired_task_leases(
            session,
            now=interrupted.lease_expires_at + timedelta(seconds=1),
        ) == ["force-killed"]
        session.refresh(interrupted)
        assert interrupted.status == TaskStatus.FAILED.value
        assert interrupted.failure_code == "worker_interrupted"
        assert source_file.exists()
        assert session.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
        assert session.get(ExtractionRecord, "force-killed") is None
        assert session.scalar(select(func.count(DataRowRecord.id))) == 0

        retried = retry_task(session, settings, "force-killed")
        assert retried.status == TaskStatus.QUEUED.value

    with Session(engine) as session:
        result = process_task(
            session,
            settings,
            "force-killed",
            client=_SuccessfulModelClient(),
        )
        assert result is not None
        recovered = session.get(TaskRecord, "force-killed")
        assert recovered is not None
        assert recovered.status == TaskStatus.COMPLETED.value
        assert recovered.attempt_count == 2
        assert session.get(ConfirmedDocumentRecord, "force-killed") is not None
        assert session.scalar(
            select(func.count(DataRowRecord.id)).where(
                DataRowRecord.task_id == "force-killed"
            )
        ) == 1
        assert session.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
    engine.dispose()
