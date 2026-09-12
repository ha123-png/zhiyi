from fastapi.testclient import TestClient
import pytest

from document_pipeline_api.business_backup import create_business_backup, inspect_business_backup
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services import internal_storage
from document_pipeline_api.services.tasks import delete_task
from document_pipeline_api.storage_paths import resolve_task_storage_path
from image_test_data import PNG_BYTES


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as client:
        yield client


def upload(client, template_id=None):
    result = client.post("/api/v1/tasks", data={"template_id": template_id} if template_id else {}, files={"file": ("原件.png", PNG_BYTES, "image/png")})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_manual_template_stores_one_original_and_backup_remains_portable(client, tmp_path):
    template = client.get("/api/v1/templates").json()[0]
    task_id = upload(client, template["id"])
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        path = resolve_task_storage_path(settings.storage_dir, task)
        assert path.parent.name == template["name"]
        assert path.read_bytes() == PNG_BYTES
        assert not (settings.storage_dir / f"{task_id}.png").exists()
        assert len(list(settings.storage_dir.rglob(f"{task_id}.png"))) == 1
        assert task.internal_storage["status"] == "classified"
    assert client.get(f"/api/v1/tasks/{task_id}/file").content == PNG_BYTES
    archive = create_business_backup(settings, tmp_path / "portable.dpbak")
    assert inspect_business_backup(archive)["files"][0]["archive_path"] == f"files/{task_id}.png"


@pytest.mark.parametrize("after_move", [False, True])
def test_interrupted_move_keeps_original_readable_and_retries_without_copy(client, monkeypatch, after_move):
    task_id = upload(client)
    template = client.get("/api/v1/templates").json()[0]
    settings = client.app.state.settings
    move = internal_storage._move_exclusive

    def interrupted(source, target):
        if after_move:
            move(source, target)
        raise RuntimeError("simulated process interruption")

    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        task.template_id, task.template_version = template["id"], template["version"]
        session.commit()
        monkeypatch.setattr(internal_storage, "_move_exclusive", interrupted)
        with pytest.raises(RuntimeError):
            internal_storage.classify_task_original(session, settings, task_id)
    assert client.get(f"/api/v1/tasks/{task_id}/file").content == PNG_BYTES
    monkeypatch.setattr(internal_storage, "_move_exclusive", move)
    with client.app.state.session_factory() as session:
        internal_storage.classify_task_original(session, settings, task_id)
        assert session.get(TaskRecord, task_id).internal_storage["status"] == "classified"
    assert len(list(settings.storage_dir.rglob(f"{task_id}.png"))) == 1


def test_internal_name_collision_keeps_each_template_separate(client):
    ids = []
    for name in ("数学/课程", "数学\\课程"):
        response = client.post("/api/v1/templates", json={"name": name, "fields": [{"key": "date", "label": "日期", "section": "header"}]})
        assert response.status_code == 201, response.text
        ids.append(upload(client, response.json()["id"]))
    with client.app.state.session_factory() as session:
        paths = [resolve_task_storage_path(client.app.state.settings.storage_dir, session.get(TaskRecord, task_id)) for task_id in ids]
        assert {path.parent.name for path in paths} == {"数学_课程", "数学_课程 (2)"}
        assert all(path.read_bytes() == PNG_BYTES for path in paths)


def test_deleting_legacy_external_reference_does_not_delete_external_file(client, tmp_path):
    task_id = upload(client)
    settings = client.app.state.settings
    external = tmp_path / "external"
    external.mkdir()
    target = external / f"{task_id}.png"
    target.write_bytes(PNG_BYTES)
    (settings.storage_dir / f"{task_id}.png").unlink()
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        task.storage_path = str(target)
        task.status = "completed"
        session.commit()
        assert resolve_task_storage_path(settings.storage_dir, task) == target
        delete_task(session, settings, task_id)
    assert target.read_bytes() == PNG_BYTES


def test_template_directory_link_cannot_escape_managed_storage(client, tmp_path):
    import os
    settings = client.app.state.settings
    outside = tmp_path / "outside"
    outside.mkdir()
    link = settings.storage_dir / "templates"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    template = client.get("/api/v1/templates").json()[0]
    task_id = upload(client, template["id"])
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task.internal_storage["status"] == "failed"
        assert resolve_task_storage_path(settings.storage_dir, task).read_bytes() == PNG_BYTES
    assert not list(outside.iterdir())
