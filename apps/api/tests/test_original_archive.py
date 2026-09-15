import json
import os

from fastapi.testclient import TestClient
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services import original_archive, task_exports
from document_pipeline_api.services.native_files import issue_import_ticket, locked_original
from document_pipeline_api.storage_paths import resolve_task_storage_path
from image_test_data import PNG_BYTES

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows file-handle archive")


@pytest.fixture
def archive_task(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    external = tmp_path / "external"
    external.mkdir()
    source = tmp_path / "原名.png"
    source.write_bytes(PNG_BYTES)
    with TestClient(create_app(settings)) as client:
        template = client.get("/api/v1/templates").json()[0]
        binding = client.put(f"/api/v1/templates/{template['id']}/local-export", json={"expected_revision": 0, "enabled": True, "mode": "move", "parent_path": str(external)})
        assert binding.status_code == 200, binding.text
        ticket = issue_import_ticket(tmp_path, source)
        uploaded = client.post("/api/v1/tasks/native-import", json={"token": ticket["token"], "template_id": template["id"]})
        assert uploaded.status_code == 201, uploaded.text
        assert "source_file_json" not in uploaded.json()
        assert str(source) not in uploaded.text
        yield client, settings, uploaded.json()["id"], source, external / template["name"] / source.name


def run(archive_task, *, complete=True):
    client, settings, task_id, source, target = archive_task
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        if complete:
            task.status = "completed"
            session.commit()
        task_exports.process_task_export(session, settings, task_id)
        session.refresh(task)
        assert resolve_task_storage_path(settings.storage_dir, task).read_bytes() == PNG_BYTES
        return task.file_export


def retry(archive_task, **changes):
    client, _, task_id, _, _ = archive_task
    response = client.post(f"/api/v1/tasks/{task_id}/export", json={"action": "retry", **changes})
    assert response.status_code == 200, response.text
    return response.json()["file_export"]


def test_moves_only_after_completion_and_keeps_internal_original(archive_task):
    _, _, _, source, target = archive_task
    run(archive_task, complete=False)
    assert source.exists() and not target.exists()
    state = run(archive_task)
    assert state.status == "completed", state
    assert not source.exists() and target.read_bytes() == PNG_BYTES
    run(archive_task)
    assert list(target.parent.iterdir()) == [target]


def test_conflict_enters_pending_and_retry_renames_without_overwrite(archive_task):
    client, _, task_id, source, target = archive_task
    target.parent.mkdir()
    target.write_bytes(b"user data")
    state = run(archive_task)
    assert state.error_code == "name_conflict", state
    assert source.read_bytes() == PNG_BYTES and target.read_bytes() == b"user data"
    assert task_id in [t["id"] for t in client.get("/api/v1/tasks?export_pending=true").json()]
    assert client.get("/api/v1/tasks/summary").json()["completed"] == 0
    assert client.get("/api/v1/tasks?status=completed").json() == []
    state = retry(archive_task, filename="新文件名.png")
    assert state["status"] == "completed", state
    assert target.with_name("新文件名.png").read_bytes() == PNG_BYTES
    assert not source.exists() and target.read_bytes() == b"user data"
    assert client.get("/api/v1/tasks/summary").json()["completed"] == 1


@pytest.mark.parametrize("replace", [False, True])
def test_never_moves_changed_or_replaced_source(archive_task, replace):
    _, _, _, source, target = archive_task
    if replace:
        source.rename(source.with_suffix(".retained"))
        source.write_bytes(PNG_BYTES)
    else:
        source.write_bytes(b"new user edits")
    before = source.read_bytes()
    state = run(archive_task)
    assert state.error_code == "source_changed", state
    assert source.read_bytes() == before and not target.exists()


def test_busy_source_is_not_moved_and_retry_succeeds(archive_task):
    _, _, _, source, target = archive_task
    with source.open("rb"):
        state = run(archive_task)
        assert state.status == "failed", state
        assert source.exists() and not target.exists()
    assert retry(archive_task)["status"] == "completed"


def test_publication_then_denied_removal_keeps_both_and_retry_reuses_target(archive_task, monkeypatch):
    _, _, _, source, target = archive_task
    remove = original_archive.remove_locked_original
    def denied(_):
        raise PermissionError("test removal blocked")
    monkeypatch.setattr(original_archive, "remove_locked_original", denied)
    state = run(archive_task)
    assert state.status == "failed" and state.source_removal_started
    assert source.read_bytes() == target.read_bytes() == PNG_BYTES
    identity = target.stat().st_ino
    monkeypatch.setattr(original_archive, "remove_locked_original", remove)
    assert retry(archive_task)["status"] == "completed"
    assert target.stat().st_ino == identity and not source.exists()


@pytest.mark.parametrize("phase", ["after_publish", "after_remove"])
def test_crash_recovery_finishes_without_duplicate_or_second_source_delete(archive_task, monkeypatch, phase):
    client, settings, task_id, source, target = archive_task
    publish = original_archive.publish_original_copy
    remove = original_archive.remove_locked_original
    def crash_publish(*args, **kwargs):
        result = publish(*args, **kwargs)
        if phase == "after_publish":
            raise RuntimeError("forced process exit after target publication")
        return result
    def crash_remove(handle):
        remove(handle)
        handle.close()
        raise RuntimeError("forced process exit after source removal")
    monkeypatch.setattr(original_archive, "publish_original_copy", crash_publish)
    if phase == "after_remove":
        monkeypatch.setattr(original_archive, "remove_locked_original", crash_remove)
    with pytest.raises(RuntimeError):
        run(archive_task)
    assert target.read_bytes() == PNG_BYTES
    assert source.exists() == (phase == "after_publish")
    monkeypatch.setattr(original_archive, "publish_original_copy", publish)
    monkeypatch.setattr(original_archive, "remove_locked_original", remove)
    with client.app.state.session_factory() as session:
        task_exports.recover_pending_exports(session, settings)
        assert session.get(TaskRecord, task_id).file_export.status == "completed"
    assert not source.exists() and list(target.parent.iterdir()) == [target]


def test_target_replacement_after_interruption_never_deletes_source(archive_task, monkeypatch):
    _, _, _, source, target = archive_task
    def denied(_):
        raise PermissionError()
    monkeypatch.setattr(original_archive, "remove_locked_original", denied)
    run(archive_task)
    target.rename(target.with_suffix(".retained"))
    target.write_bytes(PNG_BYTES)
    state = retry(archive_task)
    assert state["error_code"] == "publication_uncertain"
    assert source.read_bytes() == target.read_bytes() == PNG_BYTES


def test_native_ticket_cannot_be_forged_reused_or_redirected(archive_task):
    client, settings, task_id, source, _ = archive_task
    assert client.post("/api/v1/tasks/native-import", json={"token": "0" * 64}).status_code == 422
    assert client.post("/api/v1/tasks/native-import", json={"token": "0" * 64, "path": str(source)}).status_code == 422
    ticket = issue_import_ticket(settings.storage_dir.parent, source)
    first = client.post("/api/v1/tasks/native-import", json={"token": ticket["token"]})
    assert first.status_code == 201
    assert client.post("/api/v1/tasks/native-import", json={"token": ticket["token"]}).status_code == 422
    with client.app.state.session_factory() as session:
        assert json.loads(session.get(TaskRecord, task_id).source_file_json)["path"] == str(source)


def test_locked_handle_blocks_source_replacement(archive_task):
    _, _, _, source, _ = archive_task
    with locked_original(source, delete=True):
        with pytest.raises(PermissionError):
            source.rename(source.with_suffix(".replacement"))
        with pytest.raises(PermissionError):
            source.write_bytes(b"changed")


def test_disk_full_before_publication_keeps_source_and_retry_can_choose_path(archive_task, monkeypatch, tmp_path):
    from document_pipeline_api.services import file_copies
    _, _, _, source, target = archive_task
    original_fsync = file_copies.os.fsync
    def full(_):
        import errno
        raise OSError(errno.ENOSPC, "injected disk full")
    monkeypatch.setattr(file_copies.os, "fsync", full)
    state = run(archive_task)
    assert state.status == "failed"
    assert source.read_bytes() == PNG_BYTES and not target.exists()
    assert list(target.parent.iterdir()) == []
    monkeypatch.setattr(file_copies.os, "fsync", original_fsync)
    next_parent = tmp_path / "other-destination"
    next_parent.mkdir()
    state = retry(archive_task, parent_path=str(next_parent))
    assert state["status"] == "completed", state


def test_browser_upload_has_no_move_authority_and_can_skip(archive_task):
    client, settings, _, source, _ = archive_task
    template = client.get("/api/v1/templates").json()[0]
    uploaded = client.post("/api/v1/tasks", data={"template_id": template["id"]}, files={"file": (source.name, PNG_BYTES, "image/png")}).json()
    with client.app.state.session_factory() as session:
        session.get(TaskRecord, uploaded["id"]).status = "completed"
        session.commit()
        task_exports.process_task_export(session, settings, uploaded["id"])
        assert session.get(TaskRecord, uploaded["id"]).file_export.error_code == "source_unavailable"
    assert source.exists()
    response = client.post(f"/api/v1/tasks/{uploaded['id']}/export", json={"action": "skip"})
    assert response.json()["archive_pending"] is False
    assert source.exists()


def test_restore_clears_move_authority_and_never_resumes_original_deletion(archive_task, tmp_path):
    from document_pipeline_api.business_backup import create_business_backup, restore_business_backup
    client, settings, task_id, source, target = archive_task
    with client.app.state.session_factory() as session:
        session.get(TaskRecord, task_id).status = "completed"
        session.commit()
    archive = create_business_backup(settings, tmp_path / "archive.dpbak")
    restored_dir = tmp_path / "restored"
    restored = Settings(database_url=f"sqlite:///{restored_dir / 'document-pipeline.db'}", storage_dir=restored_dir / "uploads", queue_enabled=False)
    restore_business_backup(restored, archive, restored_dir)
    with TestClient(create_app(restored)) as restored_client:
        with restored_client.app.state.session_factory() as session:
            task = session.get(TaskRecord, task_id)
            assert task.source_file_json is None
            assert task.file_export.status == "needs_rebind"
            task_exports.recover_pending_exports(session, restored)
        assert restored_client.post(f"/api/v1/tasks/{task_id}/export", json={"action": "retry", "parent_path": str(target.parent.parent)}).status_code == 422
    assert source.read_bytes() == PNG_BYTES and not target.exists()


@pytest.mark.parametrize("phase", ["staged", "published", "removed"])
def test_real_process_exit_releases_handles_and_recovers_journal(archive_task, phase):
    import subprocess
    import sys
    client, settings, task_id, source, target = archive_task
    with client.app.state.session_factory() as session:
        session.get(TaskRecord, task_id).status = "completed"
        session.commit()
    script = '''
import os, sys
from pathlib import Path
from sqlalchemy.orm import Session
from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.services import original_archive as archive, task_exports
phase, database, storage, task_id = sys.argv[1:]
settings = Settings(database_url=database, storage_dir=Path(storage), queue_enabled=False)
publish, remove = archive.publish_original_copy, archive.remove_locked_original
def crash_publish(*args, **kwargs):
    if phase == "staged":
        staged = kwargs["on_staged"]
        def after_stage(*values):
            staged(*values)
            os._exit(73)
        kwargs["on_staged"] = after_stage
    result = publish(*args, **kwargs)
    if phase == "published": os._exit(73)
    return result
def crash_remove(handle):
    remove(handle)
    handle.close()
    os._exit(73)
archive.publish_original_copy = crash_publish
if phase == "removed": archive.remove_locked_original = crash_remove
with Session(build_engine(database)) as session:
    task_exports.process_task_export(session, settings, task_id)
'''
    result = subprocess.run([sys.executable, "-c", script, phase, settings.database_url, str(settings.storage_dir), task_id], capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr
    assert source.exists() == (phase != "removed")
    with client.app.state.session_factory() as session:
        task_exports.recover_pending_exports(session, settings)
        # Interrupted before the no-replace rename may need an explicit retry.
        task = session.get(TaskRecord, task_id)
        if task.file_export.status == "failed":
            state = task.file_export
            assert state.error_code == "publication_interrupted", state
            state.status = "pending"
            task.export_state_json = state.model_dump_json()
            session.commit()
            task_exports.process_task_export(session, settings, task_id)
        assert task.file_export.status == "completed", task.file_export
    assert not source.exists() and target.read_bytes() == PNG_BYTES
    assert list(target.parent.iterdir()) == [target]
