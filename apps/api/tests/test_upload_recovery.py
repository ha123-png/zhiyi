from __future__ import annotations

import base64
from multiprocessing import get_context
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services.dataset_identity import advance_dataset
from document_pipeline_api.services.upload_recovery import (
    begin_upload_intent,
    finish_upload_intent,
    recover_pending_uploads,
)


def _hold_live_upload(data_dir: str, storage_dir: str, task_id: str, ready, release) -> None:
    from document_pipeline_api.services.upload_recovery import (
        begin_upload_intent,
        finish_upload_intent,
    )

    intent = begin_upload_intent(Path(data_dir), task_id)
    storage = Path(storage_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / f".{task_id}.uploading").write_bytes(b"partial")
    (storage / f"{task_id}.png").write_bytes(b"published")
    ready.set()
    release.wait(timeout=20)
    finish_upload_intent(intent, remove_marker=False)


def _hold_maintenance_file_lock(storage_dir: str, ready, release) -> None:
    from document_pipeline_api.services.file_operation_lock import file_operation_lock

    with file_operation_lock(Path(storage_dir)):
        ready.set()
        release.wait(timeout=20)


def _crash_http_upload(data_dir: str, crash_point: str) -> None:
    from pathlib import Path
    from sqlalchemy.orm import Session as OrmSession

    from document_pipeline_api.config import Settings as LocalSettings
    from document_pipeline_api.main import create_app
    from document_pipeline_api.models import TaskRecord as LocalTask

    root = Path(data_dir)
    storage = root / "uploads"
    settings = LocalSettings(
        database_url=f"sqlite:///{root / 'business.db'}",
        storage_dir=storage,
        max_upload_bytes=1024 * 1024,
        max_request_bytes=2 * 1024 * 1024,
        queue_enabled=False,
    )
    if crash_point == "after_publish":
        original_replace = Path.replace

        def exit_after_publish(source: Path, target: Path):
            result = original_replace(source, target)
            if source.name.endswith(".uploading"):
                os._exit(73)
            return result

        Path.replace = exit_after_publish
    elif crash_point == "after_commit":
        original_commit = OrmSession.commit

        def exit_after_task_commit(session: OrmSession):
            result = original_commit(session)
            marker_dir = root / "runtime" / "ingest-pending"
            for marker in marker_dir.glob("*.json"):
                if session.get(LocalTask, marker.stem) is not None:
                    os._exit(74)
            return result

        OrmSession.commit = exit_after_task_commit
    else:
        raise ValueError(f"unknown crash point: {crash_point}")

    payload = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII="
    )
    with TestClient(create_app(settings)) as client:
        client.post(
            "/api/v1/tasks",
            files={"file": ("synthetic.png", payload, "image/png")},
            data={"template_mode": "invoice"},
        )
    os._exit(99)


def _setup(tmp_path: Path):
    storage = tmp_path / "uploads"
    storage.mkdir()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'business.db'}",
        storage_dir=storage,
        queue_enabled=False,
    )
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    return settings, engine, factory


def _write_orphan(settings: Settings, task_id: str) -> bytes:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / f".{task_id}.uploading").write_bytes(b"partial synthetic upload")
    expected = b"published synthetic original"
    (settings.storage_dir / f"{task_id}.png").write_bytes(expected)
    return expected


def test_recovery_removes_unowned_final_and_staging_files(tmp_path: Path) -> None:
    settings, engine, factory = _setup(tmp_path)
    task_id = "042c6cf8-4d6b-49a5-88b2-41782f77840d"
    intent = begin_upload_intent(tmp_path, task_id)
    _write_orphan(settings, task_id)
    finish_upload_intent(intent, remove_marker=False)

    summary = recover_pending_uploads(factory, settings)

    assert summary["orphan_files_removed"] == 1
    assert summary["cleanup_failures"] == 0
    assert not (settings.storage_dir / f".{task_id}.uploading").exists()
    assert not (settings.storage_dir / f"{task_id}.png").exists()
    assert not (tmp_path / "runtime" / "ingest-pending" / f"{task_id}.json").exists()
    engine.dispose()


@pytest.mark.parametrize("create_empty_directory", [False, True])
def test_recovery_with_no_markers_does_not_acquire_shared_file_lock(
    tmp_path: Path,
    monkeypatch,
    create_empty_directory: bool,
) -> None:
    settings, engine, factory = _setup(tmp_path)
    marker_dir = tmp_path / "runtime" / "ingest-pending"
    if create_empty_directory:
        marker_dir.mkdir(parents=True)

    import importlib

    lock_module = importlib.import_module(
        "document_pipeline_api.services.file_operation_lock"
    )

    def unexpected_lock(*args, **kwargs):
        raise AssertionError("empty upload recovery must not acquire the maintenance lock")

    monkeypatch.setattr(lock_module, "file_operation_lock", unexpected_lock)

    summary = recover_pending_uploads(factory, settings)

    assert summary == {
        "scanned": 0,
        "orphan_files_removed": 0,
        "owned_uploads_preserved": 0,
        "active_uploads_skipped": 0,
        "stale_generation_markers_removed": 0,
        "cleanup_failures": 0,
        "blocked_by_maintenance": 0,
    }
    engine.dispose()


def test_recovery_keeps_committed_task_original_and_clears_marker(tmp_path: Path) -> None:
    settings, engine, factory = _setup(tmp_path)
    task_id = "af90fa7d-4a12-4b37-aada-89344c303370"
    intent = begin_upload_intent(tmp_path, task_id)
    expected = _write_orphan(settings, task_id)
    with factory() as session:
        session.add(
            TaskRecord(
                id=task_id,
                filename="source.png",
                content_type="image/png",
                size_bytes=len(expected),
                sha256="synthetic-hash",
                storage_path=f"{task_id}.png",
                template_mode="invoice",
                status=TaskStatus.QUEUED.value,
            )
        )
        session.commit()
    finish_upload_intent(intent, remove_marker=False)

    summary = recover_pending_uploads(factory, settings)

    assert summary["owned_uploads_preserved"] == 1
    assert summary["orphan_files_removed"] == 0
    assert (settings.storage_dir / f"{task_id}.png").read_bytes() == expected
    assert not (settings.storage_dir / f".{task_id}.uploading").exists()
    assert not (tmp_path / "runtime" / "ingest-pending" / f"{task_id}.json").exists()
    engine.dispose()


def test_recovery_skips_a_live_upload_lock_then_cleans_after_owner_exits(tmp_path: Path) -> None:
    settings, engine, factory = _setup(tmp_path)
    task_id = "50946ae8-8a31-49b0-afc9-64e946a2c84b"
    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_live_upload,
        args=(str(tmp_path), str(settings.storage_dir), task_id, ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=10), "test uploader did not acquire its upload lock"
        active_summary = recover_pending_uploads(factory, settings)
        assert active_summary["active_uploads_skipped"] == 1
        assert (settings.storage_dir / f"{task_id}.png").read_bytes() == b"published"
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
    assert process.exitcode == 0

    recovered = recover_pending_uploads(factory, settings)
    assert recovered["orphan_files_removed"] == 1
    assert not (settings.storage_dir / f"{task_id}.png").exists()
    engine.dispose()


def test_recovery_skips_while_shared_maintenance_lock_is_held(tmp_path: Path) -> None:
    settings, engine, factory = _setup(tmp_path)
    task_id = "fd0a7bb1-1cc0-433c-aed3-8795fe0344f2"
    intent = begin_upload_intent(tmp_path, task_id)
    expected = _write_orphan(settings, task_id)
    finish_upload_intent(intent, remove_marker=False)
    marker_path = tmp_path / "runtime" / "ingest-pending" / f"{task_id}.json"

    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_maintenance_file_lock,
        args=(str(settings.storage_dir), ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=10), "maintenance did not acquire the shared file lock"
        blocked = recover_pending_uploads(factory, settings)
        assert blocked["blocked_by_maintenance"] == 1
        assert blocked["scanned"] == 0
        assert marker_path.exists()
        assert (settings.storage_dir / f"{task_id}.png").read_bytes() == expected
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
    assert process.exitcode == 0

    recovered = recover_pending_uploads(factory, settings)
    assert recovered["orphan_files_removed"] == 1
    assert not marker_path.exists()
    assert not (settings.storage_dir / f"{task_id}.png").exists()
    engine.dispose()


def test_database_read_failure_never_authorizes_orphan_deletion(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'unreadable.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    task_id = "c1236ab2-4f20-480c-818d-f46e560f1df3"
    intent = begin_upload_intent(tmp_path, task_id)
    expected = _write_orphan(settings, task_id)
    finish_upload_intent(intent, remove_marker=False)
    (tmp_path / "unreadable.db").write_bytes(b"not a sqlite database")
    engine = build_engine(settings.database_url)
    factory = sessionmaker(engine, expire_on_commit=False)

    summary = recover_pending_uploads(factory, settings)

    assert summary["cleanup_failures"] == 1
    assert (settings.storage_dir / f"{task_id}.png").read_bytes() == expected
    assert (tmp_path / "runtime" / "ingest-pending" / f"{task_id}.json").exists()
    engine.dispose()


def test_stale_dataset_marker_never_deletes_same_named_restored_original(tmp_path: Path) -> None:
    settings, engine, factory = _setup(tmp_path)
    task_id = "b694909a-cf2f-41c2-99dc-f2f2528e5020"
    intent = begin_upload_intent(tmp_path, task_id)
    expected = _write_orphan(settings, task_id)
    finish_upload_intent(intent, remove_marker=False)
    advance_dataset(tmp_path)

    summary = recover_pending_uploads(factory, settings)

    assert summary["stale_generation_markers_removed"] == 1
    assert (settings.storage_dir / f"{task_id}.png").read_bytes() == expected
    assert not (tmp_path / "runtime" / "ingest-pending" / f"{task_id}.json").exists()
    engine.dispose()


def test_failed_orphan_cleanup_retains_intent_for_retry(tmp_path: Path, monkeypatch) -> None:
    settings, engine, factory = _setup(tmp_path)
    task_id = "67296b56-ea3b-402e-a641-71df7b851848"
    intent = begin_upload_intent(tmp_path, task_id)
    expected = _write_orphan(settings, task_id)
    finish_upload_intent(intent, remove_marker=False)
    final_path = settings.storage_dir / f"{task_id}.png"
    original_unlink = Path.unlink

    def fail_final_unlink(path: Path, *args, **kwargs):
        if path == final_path:
            raise PermissionError("synthetic locked original")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_final_unlink)
    summary = recover_pending_uploads(factory, settings)

    assert summary["cleanup_failures"] == 1
    assert final_path.read_bytes() == expected
    assert (tmp_path / "runtime" / "ingest-pending" / f"{task_id}.json").exists()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    retried = recover_pending_uploads(factory, settings)
    assert retried["orphan_files_removed"] == 1
    assert not final_path.exists()
    assert not (tmp_path / "runtime" / "ingest-pending" / f"{task_id}.json").exists()
    engine.dispose()


def test_http_upload_crash_boundaries_recover_database_and_original_consistently(
    tmp_path: Path,
) -> None:
    context = get_context("spawn")
    for crash_point, exit_code in (("after_publish", 73), ("after_commit", 74)):
        data_dir = tmp_path / crash_point
        data_dir.mkdir()
        process = context.Process(target=_crash_http_upload, args=(str(data_dir), crash_point))
        process.start()
        process.join(timeout=30)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        assert process.exitcode == exit_code

        settings = Settings(
            database_url=f"sqlite:///{data_dir / 'business.db'}",
            storage_dir=data_dir / "uploads",
            queue_enabled=False,
        )
        engine = build_engine(settings.database_url)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            tasks = list(session.query(TaskRecord).all())
        originals = list(settings.storage_dir.glob("*.png"))
        markers = list((data_dir / "runtime" / "ingest-pending").glob("*.json"))
        summary = recover_pending_uploads(factory, settings)

        if crash_point == "after_publish":
            assert tasks == []
            assert summary["orphan_files_removed"] == 1
            assert list(settings.storage_dir.glob("*.png")) == []
        else:
            assert len(tasks) == 1 and tasks[0].status == TaskStatus.QUEUED.value
            assert summary["owned_uploads_preserved"] == 1
            assert len(originals) == 1 and originals[0].exists()
        assert len(markers) == 1
        assert list((data_dir / "runtime" / "ingest-pending").glob("*.json")) == []
        engine.dispose()
