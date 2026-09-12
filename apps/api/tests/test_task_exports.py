from fastapi.testclient import TestClient
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services import task_exports
from document_pipeline_api.services.file_operation_lock import file_operation_lock
from document_pipeline_api.services.tasks import delete_task
from image_test_data import PNG_BYTES


@pytest.fixture
def export_task(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    external = tmp_path / "external"
    external.mkdir()
    with TestClient(create_app(settings)) as client:
        template = client.get("/api/v1/templates").json()[0]
        response = client.put(f"/api/v1/templates/{template['id']}/local-export", json={"expected_revision": 0, "enabled": True, "parent_path": str(external)})
        assert response.status_code == 200
        uploaded = client.post("/api/v1/tasks", data={"template_id": template["id"]}, files={"file": ("原名.png", PNG_BYTES, "image/png")})
        assert uploaded.status_code == 201
        task_id = uploaded.json()["id"]
        yield client, settings, task_id, external / template["name"] / "原名.png"


def mark_completed(session, task_id):
    session.get(TaskRecord, task_id).status = "completed"
    session.commit()


def test_waits_for_confirmation_then_exports_once_and_deletion_keeps_copy(export_task):
    client, settings, task_id, target = export_task
    with client.app.state.session_factory() as session:
        task_exports.process_task_export(session, settings, task_id)
        assert not target.exists()
        mark_completed(session, task_id)
        task_exports.process_task_export(session, settings, task_id)
        assert target.read_bytes() == PNG_BYTES
        assert session.get(TaskRecord, task_id).file_export.status == "completed"
        task_exports.process_task_export(session, settings, task_id)
        assert list(target.parent.iterdir()) == [target]
        delete_task(session, settings, task_id)
        assert target.read_bytes() == PNG_BYTES


def test_export_conflict_does_not_turn_extraction_into_failure(export_task):
    client, settings, task_id, target = export_task
    target.parent.mkdir()
    target.write_bytes(b"user file")
    with client.app.state.session_factory() as session:
        mark_completed(session, task_id)
        task_exports.process_task_export(session, settings, task_id)
        task = session.get(TaskRecord, task_id)
        assert task.status == "completed"
        assert task.failure_code is None
        assert task.file_export.status == "failed"
        assert task.file_export.error_code == "name_conflict"
    assert target.read_bytes() == b"user file"
    assert [item["id"] for item in client.get("/api/v1/tasks?export_pending=true").json()] == [task_id]
    assert client.get("/api/v1/tasks?status=failed").json() == []


def test_recovers_publication_after_lost_success_commit_without_second_copy(export_task, monkeypatch):
    client, settings, task_id, target = export_task
    save = task_exports._save

    def lost_commit(session, task, state):
        if state.status == "completed":
            raise RuntimeError("simulated termination before success commit")
        save(session, task, state)

    with client.app.state.session_factory() as session:
        mark_completed(session, task_id)
        monkeypatch.setattr(task_exports, "_save", lost_commit)
        with pytest.raises(RuntimeError):
            task_exports.process_task_export(session, settings, task_id)
        assert target.read_bytes() == PNG_BYTES
    monkeypatch.setattr(task_exports, "_save", save)
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, task_id).file_export.status == "exporting"
        task_exports.recover_pending_exports(session, settings)
        assert session.get(TaskRecord, task_id).file_export.status == "completed"
        assert list(target.parent.iterdir()) == [target]
        moved = target.with_name("用户移动后的名字.png")
        target.rename(moved)
        task_exports.process_task_export(session, settings, task_id)
        assert not target.exists()
        assert moved.read_bytes() == PNG_BYTES


def test_os_lock_defers_publication_without_changing_intent(export_task):
    client, settings, task_id, target = export_task
    with client.app.state.session_factory() as session:
        mark_completed(session, task_id)
        with file_operation_lock(settings.storage_dir):
            task_exports.process_task_export(session, settings, task_id)
            assert not target.exists()
        task_exports.process_task_export(session, settings, task_id)
        assert target.exists()


def test_pending_action_changes_only_current_target_and_keeps_conflicting_user_file(export_task):
    client, settings, task_id, target = export_task
    target.parent.mkdir()
    target.write_bytes(b"user file")
    with client.app.state.session_factory() as session:
        mark_completed(session, task_id)
        task_exports.process_task_export(session, settings, task_id)
    new_parent = target.parent.parent / "new-location"
    new_parent.mkdir()
    response = client.post(f"/api/v1/tasks/{task_id}/export", json={"action": "retry", "filename": "新名称.png", "parent_path": str(new_parent)})
    assert response.status_code == 200, response.text
    state = response.json()["file_export"]
    assert state["status"] == "completed"
    assert response.json()["filename"] == "原名.png"
    assert (new_parent / target.parent.name / "新名称.png").read_bytes() == PNG_BYTES
    assert target.read_bytes() == b"user file"
    template_id = response.json()["template_id"]
    binding = client.get(f"/api/v1/templates/{template_id}/local-export").json()
    assert binding["parent_path"] == str(target.parent.parent)
    assert client.post(f"/api/v1/tasks/{task_id}/export", json={"action": "retry"}).status_code == 409


def test_skip_does_not_delete_conflict_or_reenter_recovery(export_task):
    client, settings, task_id, target = export_task
    target.parent.mkdir()
    target.write_bytes(b"user file")
    with client.app.state.session_factory() as session:
        mark_completed(session, task_id)
        task_exports.process_task_export(session, settings, task_id)
    response = client.post(f"/api/v1/tasks/{task_id}/export", json={"action": "skip"})
    assert response.status_code == 200
    assert response.json()["file_export"]["status"] == "skipped"
    assert client.get("/api/v1/tasks?export_pending=true").json() == []
    with client.app.state.session_factory() as session:
        task_exports.recover_pending_exports(session, settings)
    assert target.read_bytes() == b"user file"


def test_recovery_does_not_claim_replacement_even_with_identical_bytes(export_task, monkeypatch):
    client, settings, task_id, target = export_task
    save = task_exports._save

    def lost_commit(session, task, state):
        if state.status == "completed":
            raise RuntimeError("lost success commit")
        save(session, task, state)

    with client.app.state.session_factory() as session:
        mark_completed(session, task_id)
        monkeypatch.setattr(task_exports, "_save", lost_commit)
        with pytest.raises(RuntimeError):
            task_exports.process_task_export(session, settings, task_id)
    monkeypatch.setattr(task_exports, "_save", save)
    moved = target.with_name("用户留存.png")
    target.rename(moved)
    target.write_bytes(PNG_BYTES)
    with client.app.state.session_factory() as session:
        task_exports.recover_pending_exports(session, settings)
        state = session.get(TaskRecord, task_id).file_export
        assert state.status == "failed"
        assert state.error_code == "publication_uncertain"
    assert target.read_bytes() == moved.read_bytes() == PNG_BYTES
    response = client.post(f"/api/v1/tasks/{task_id}/export", json={"action": "retry"})
    assert response.status_code == 422
    assert len(list(target.parent.iterdir())) == 2
