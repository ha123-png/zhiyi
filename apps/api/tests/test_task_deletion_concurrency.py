"""Deletion must not act on a task status superseded by retry/worker claims."""
from datetime import timedelta
import hashlib
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services import tasks
from document_pipeline_api.services.original_cleanup import pending_cleanup_count
from document_pipeline_api.services.task_leases import acquire_task_lease


TASK_ID = "deletion-race"
ORIGINAL = b"synthetic original retained while processing"


@pytest.fixture
def store(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'deletion.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    settings.storage_dir.mkdir()
    original = settings.storage_dir / f"{TASK_ID}.txt"
    original.write_bytes(ORIGINAL)
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(TaskRecord(
            id=TASK_ID, filename="synthetic.txt", content_type="text/plain",
            size_bytes=len(ORIGINAL), sha256=hashlib.sha256(ORIGINAL).hexdigest(),
            storage_path=original.name, template_mode="smart", status="failed",
        ))
        session.commit()
    yield settings, engine, original
    engine.dispose()


def _delete(session, settings, operation):
    if operation == "single":
        return tasks.delete_task(session, settings, TASK_ID)
    if operation == "batch":
        return tasks.delete_tasks_batch(session, settings, [TASK_ID])
    return tasks.clear_history(session, settings)


def _retry_and_claim(engine, settings, *, no_wait=False):
    with Session(engine) as writer:
        if no_wait:
            writer.connection().exec_driver_sql("PRAGMA busy_timeout=0")
        tasks.retry_task(writer, settings, TASK_ID)
        assert acquire_task_lease(writer, TASK_ID, lease_for=timedelta(minutes=5)) is not None


@pytest.mark.parametrize("operation", ["single", "batch", "history"])
def test_delete_and_retry_are_serialized_before_deletion_decides(store, monkeypatch, operation):
    settings, engine, original = store
    real_delete = tasks._delete_files_and_records
    observed = {}

    def interleave(session, settings, selected):
        observed["selected_status"] = selected[0].status
        try:
            _retry_and_claim(engine, settings, no_wait=True)
            observed["retry"] = "processing"
        except OperationalError as error:
            assert getattr(error.orig, "sqlite_errorcode", 0) & 0xFF in {5, 6}
            observed["retry"] = "serialized"
        return real_delete(session, settings, selected)

    monkeypatch.setattr(tasks, "_delete_files_and_records", interleave)
    with Session(engine) as deleting:
        try:
            _delete(deleting, settings, operation)
        except HTTPException as error:
            assert error.status_code == 409
            observed["delete"] = "conflict"
    with Session(engine) as check:
        remaining = check.get(TaskRecord, TASK_ID)
        observed["after_status"] = remaining.status if remaining else None
        observed["cleanup_count"] = pending_cleanup_count(check)
    observed["original_exists"] = original.exists()
    print(json.dumps({"operation": operation, **observed}, sort_keys=True))
    assert observed["selected_status"] == "failed"
    if observed["retry"] == "processing":
        assert observed["after_status"] == "processing", observed
        assert original.read_bytes() == ORIGINAL
    else:
        assert observed["retry"] == "serialized"
        assert observed["after_status"] is None
        assert not original.exists()
    assert observed["cleanup_count"] == 0


@pytest.mark.parametrize("operation", ["single", "batch", "history"])
def test_delete_rechecks_a_cached_task_after_retry_commits(store, operation):
    settings, engine, original = store
    with Session(engine) as deleting:
        # Retaining the object also retains the Session identity-map entry.
        stale = deleting.get(TaskRecord, TASK_ID)
        assert stale.status == "failed"
        _retry_and_claim(engine, settings)
        if operation == "history":
            assert _delete(deleting, settings, operation) == 0
        else:
            with pytest.raises(HTTPException) as conflict:
                _delete(deleting, settings, operation)
            assert conflict.value.status_code == 409
    with Session(engine) as check:
        assert check.get(TaskRecord, TASK_ID).status == "processing"
        assert pending_cleanup_count(check) == 0
    assert original.read_bytes() == ORIGINAL


@pytest.mark.parametrize("operation,state", [
    ("single", None), ("single", "processing"),
    ("batch", "processing"), ("history", "processing"),
])
def test_noop_or_rejected_deletion_releases_its_write_transaction(store, operation, state):
    settings, engine, original = store
    with Session(engine) as setup:
        task = setup.get(TaskRecord, TASK_ID)
        if state is None:
            setup.delete(task)
        else:
            task.status = state
        setup.commit()
    with Session(engine) as deleting:
        if operation == "history":
            assert _delete(deleting, settings, operation) == 0
        else:
            with pytest.raises(HTTPException) as conflict:
                _delete(deleting, settings, operation)
            assert conflict.value.status_code == (404 if state is None else 409)
        # Keep the caller's Session open: the completed no-op must not keep
        # unrelated requests from committing their own database work.
        with Session(engine) as writer:
            writer.connection().exec_driver_sql("PRAGMA busy_timeout=0")
            writer.execute(update(TaskRecord).values(filename="Another request.txt"))
            writer.commit()
    assert original.read_bytes() == ORIGINAL
