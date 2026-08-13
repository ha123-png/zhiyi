from huey import crontab
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.models import ExtractionRecord, TaskRecord  # noqa: F401
from document_pipeline_api.migrations import wait_for_database_head
from document_pipeline_api.queue import huey
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.services.task_leases import recover_expired_task_leases
from document_pipeline_api.services.worker_health import (
    start_worker_heartbeat,
    stop_worker_heartbeat,
)


@huey.on_startup(name="worker-heartbeat")
def start_heartbeat() -> None:
    settings = Settings.local()
    engine = build_engine(settings.database_url)
    try:
        wait_for_database_head(engine)
    finally:
        engine.dispose()
    _recover_interrupted_tasks()
    start_worker_heartbeat()


@huey.on_shutdown(name="worker-heartbeat")
def stop_heartbeat() -> None:
    stop_worker_heartbeat()


@huey.task(name="extract_document")
def run_extraction(task_id: str) -> None:
    settings = Settings.local()
    engine = build_engine(settings.database_url)
    try:
        with Session(engine) as session:
            process_task(session, settings, task_id)
    finally:
        engine.dispose()


@huey.periodic_task(crontab(minute="*"), name="recover-interrupted-tasks")
def recover_interrupted_tasks() -> None:
    _recover_interrupted_tasks()


def _recover_interrupted_tasks() -> None:
    from document_pipeline_api.services.queueing import recover_missing_queued_tasks

    settings = Settings.local()
    engine = build_engine(settings.database_url)
    try:
        with Session(engine) as session:
            recover_expired_task_leases(session)
            recover_missing_queued_tasks(session)
    finally:
        engine.dispose()
