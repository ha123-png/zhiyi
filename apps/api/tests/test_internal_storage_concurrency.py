"""A classification retry must preserve a running worker's original path."""
from datetime import timedelta
import hashlib
import json

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services import internal_storage
from document_pipeline_api.services.task_leases import acquire_task_lease
from document_pipeline_api.services.templates import ensure_builtin_templates
from document_pipeline_api.services.tasks import select_task_template
from document_pipeline_api.storage_paths import resolve_task_storage_path


TASK_ID = "classification-race"
ORIGINAL = b"synthetic worker input"


@pytest.fixture
def store(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'classification.db'}",
        storage_dir=tmp_path / "uploads", queue_enabled=False,
    )
    settings.storage_dir.mkdir()
    original = settings.storage_dir / f"{TASK_ID}.png"
    original.write_bytes(ORIGINAL)
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_builtin_templates(session)
        session.add(TaskRecord(
            id=TASK_ID, filename="synthetic.png", content_type="image/png",
            size_bytes=len(ORIGINAL), sha256=hashlib.sha256(ORIGINAL).hexdigest(),
            storage_path=original.name, template_mode="manual", status="queued",
            template_id="builtin-invoice", template_version=1,
        ))
        session.commit()
    yield settings, engine, original
    engine.dispose()


def _claim_and_resolve(engine, settings, *, no_wait=False):
    with Session(engine) as worker:
        if no_wait:
            worker.connection().exec_driver_sql("PRAGMA busy_timeout=0")
        assert acquire_task_lease(worker, TASK_ID, lease_for=timedelta(minutes=5)) is not None
        return resolve_task_storage_path(settings.storage_dir, worker.get(TaskRecord, TASK_ID))


def test_classification_defers_if_worker_claims_after_its_initial_state_check(store, monkeypatch):
    settings, engine, original = store
    get_template = internal_storage.get_template_version
    observed = {}

    def interleave(*args):
        observed["worker_path"] = _claim_and_resolve(engine, settings)
        return get_template(*args)

    with monkeypatch.context() as patch:
        patch.setattr(internal_storage, "get_template_version", interleave)
        with Session(engine) as classifier:
            internal_storage.classify_task_original(classifier, settings, TASK_ID)
    with Session(engine) as check:
        task = check.get(TaskRecord, TASK_ID)
        observed["after_status"] = task.status
        observed["classification"] = task.internal_storage["status"]
        assert task.status == "processing"
    observed["worker_path_exists"] = observed["worker_path"].exists()
    print(json.dumps({**observed, "worker_path": observed["worker_path"].name}, sort_keys=True))
    assert observed["worker_path"].read_bytes() == ORIGINAL
    assert original.exists()
    # Once processing ends, ordinary recovery finishes the durable move intent.
    with Session(engine) as finished:
        task = finished.get(TaskRecord, TASK_ID)
        task.status = "completed"
        task.lease_token = task.lease_expires_at = None
        finished.commit()
        internal_storage.classify_task_original(finished, settings, TASK_ID)
        task = finished.get(TaskRecord, TASK_ID)
        assert task.internal_storage["status"] == "classified"
        assert resolve_task_storage_path(settings.storage_dir, task).read_bytes() == ORIGINAL
    assert not original.exists()
    assert len(list(settings.storage_dir.rglob(f"{TASK_ID}.png"))) == 1


def test_worker_claim_is_serialized_with_the_actual_original_move(store, monkeypatch):
    settings, engine, original = store
    real_move = internal_storage._move_exclusive
    observed = {}

    def interleave(source, target):
        try:
            observed["worker_path"] = _claim_and_resolve(engine, settings, no_wait=True)
            observed["claim"] = "processing"
        except OperationalError as error:
            assert getattr(error.orig, "sqlite_errorcode", 0) & 0xFF in {5, 6}
            observed["claim"] = "serialized"
        return real_move(source, target)

    monkeypatch.setattr(internal_storage, "_move_exclusive", interleave)
    with Session(engine) as classifier:
        internal_storage.classify_task_original(classifier, settings, TASK_ID)
    print(json.dumps({"claim": observed["claim"], "old_path_exists": original.exists()}, sort_keys=True))
    if observed["claim"] == "processing":
        assert observed["worker_path"].read_bytes() == ORIGINAL
    else:
        assert observed["claim"] == "serialized"
        assert _claim_and_resolve(engine, settings).read_bytes() == ORIGINAL


@pytest.mark.parametrize("already_classified", [False, True])
def test_concurrent_template_choice_cannot_seal_the_old_folder_as_classified(store, monkeypatch, already_classified):
    settings, engine, _original = store
    with Session(engine) as setup:
        if already_classified:
            internal_storage.classify_task_original(setup, settings, TASK_ID)
        task = setup.get(TaskRecord, TASK_ID)
        task.status = "waiting_for_template"
        task.pending_reason = "template"
        setup.commit()
        original_path = resolve_task_storage_path(settings.storage_dir, task)
    get_template = internal_storage.get_template_version

    def choose_different_template(*args):
        old_template = get_template(*args)
        with Session(engine) as choosing:
            select_task_template(choosing, settings, TASK_ID, "builtin-delivery")
            # This matches the API: classification is deferred while the
            # earlier request owns the nonblocking file-operation lock.
            internal_storage.classify_task_original(choosing, settings, TASK_ID)
        return old_template

    with monkeypatch.context() as patch:
        patch.setattr(internal_storage, "get_template_version", choose_different_template)
        with Session(engine) as classifier:
            internal_storage.classify_task_original(classifier, settings, TASK_ID)
    with Session(engine) as recovery:
        task = recovery.get(TaskRecord, TASK_ID)
        assert task.template_id == "builtin-delivery"
        assert task.status == "queued"
        assert task.internal_storage["status"] != "classified"
        assert original_path.read_bytes() == ORIGINAL
        internal_storage.recover_internal_storage(recovery, settings)
        task = recovery.get(TaskRecord, TASK_ID)
        template = get_template(recovery, "builtin-delivery", 1)
        path = resolve_task_storage_path(settings.storage_dir, task)
        assert task.internal_storage["status"] == "classified"
        assert path.parent.name == template.name
        assert path.read_bytes() == ORIGINAL
    assert len(list(settings.storage_dir.rglob(f"{TASK_ID}.png"))) == 1
