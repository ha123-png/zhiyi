import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_secrets import ModelSecretStore
from document_pipeline_api.models import ModelProfileVersionRecord, TaskRecord
from document_pipeline_api.services import clear_data
from image_test_data import PNG_BYTES
from test_model_secrets import MemoryCredentialBackend


@pytest.mark.skipif(os.name != "nt", reason="Windows connection recovery")
def test_clear_connection_recovery_is_bounded_and_does_not_retry_other_failures(monkeypatch):
    monkeypatch.setattr(clear_data.time, "sleep", Mock())
    transient = OperationalError(None, None, SimpleNamespace(sqlite_errorcode=4874, sqlite_errorname="SQLITE_IOERR_SHMSIZE"))
    connected = object()
    engine = Mock()
    engine.connect.side_effect = [transient, connected]
    assert clear_data._open_clear_connection(engine) is connected
    assert engine.connect.call_count == 2
    engine.connect.reset_mock(side_effect=True)
    engine.connect.side_effect = transient
    with pytest.raises(OperationalError):
        clear_data._open_clear_connection(engine)
    assert engine.connect.call_count == 6
    engine.connect.reset_mock(side_effect=True)
    engine.connect.side_effect = OperationalError(None, None, SimpleNamespace(sqlite_errorcode=11))
    with pytest.raises(OperationalError):
        clear_data._open_clear_connection(engine)
    assert engine.connect.call_count == 1


def setup_client(root: Path):
    settings = Settings(database_url=f"sqlite:///{root / 'document-pipeline.db'}", storage_dir=root / "uploads", queue_enabled=False)
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    return TestClient(create_app(settings, model_secret_store=store)), store


def upload(client):
    result = client.post("/api/v1/tasks", files={"file": ("原件.png", PNG_BYTES, "image/png")})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def clear(client):
    return client.post("/api/v1/system/admin/clear-data", json={"confirm_text": "清除全部本地数据"})


def test_clear_removes_business_data_and_scoped_credentials_but_keeps_external_files(tmp_path):
    root = tmp_path / "data"
    client, store = setup_client(root)
    with client:
        upload(client)
        secret_ref = store.put("synthetic-test-secret")
        unrelated_ref = store.put("another-installation-secret")
        with client.app.state.session_factory() as session:
            session.scalars(select(ModelProfileVersionRecord)).first().secret_ref = secret_ref
            session.commit()
        for path in (root / "backups" / "user.dpbak", root / "models" / "model.gguf", tmp_path / "external" / "副本.png"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"must survive")
        (root / "desktop-settings.json").write_text('{"export_directory":"old"}')
        response = clear(client)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "succeeded"
        assert response.json()["cleared_tasks"] == 1
        assert client.get("/api/v1/tasks").json() == []
        assert len(client.get("/api/v1/templates").json()) == 2
        assert not list((root / "uploads").iterdir())
        assert not (root / "desktop-settings.json").exists()
        assert store.get(unrelated_ref) == "another-installation-secret"
        assert client.get("/api/v1/system/admin/clear-data").json()["incomplete"] is False
    for path in (root / "backups" / "user.dpbak", root / "models" / "model.gguf", tmp_path / "external" / "副本.png"):
        assert path.read_bytes() == b"must survive"
    assert len(store._backend.values) == 1


def test_partial_failure_blocks_new_writes_and_can_resume(tmp_path, monkeypatch):
    root = tmp_path / "data"
    client, _ = setup_client(root)
    remove = clear_data._remove_owned
    with client:
        upload(client)

        def fail(root, path):
            if path.name == "uploads":
                raise PermissionError("file held by external application")
            remove(root, path)

        monkeypatch.setattr(clear_data, "_remove_owned", fail)
        response = clear(client)
        assert response.status_code == 409
        assert "部分数据" in response.json()["detail"]
        assert client.get("/api/v1/system/admin/clear-data").json()["incomplete"] is True
        assert client.post("/api/v1/tasks", files={"file": ("new.png", PNG_BYTES, "image/png")}).status_code == 503
        monkeypatch.setattr(clear_data, "_remove_owned", remove)
        response = clear(client)
        assert response.status_code == 200, response.text
        assert response.json()["cleared_tasks"] == 1
        assert not list((root / "uploads").iterdir())
        assert client.get("/api/v1/system/admin/clear-data").json()["incomplete"] is False


def test_clear_unlinks_directory_junction_without_visiting_external_target(tmp_path):
    client, _ = setup_client(tmp_path / "data")
    outside = tmp_path / "external"
    outside.mkdir()
    sentinel = outside / "belongs-to-user.txt"
    sentinel.write_bytes(b"unchanged")
    with client:
        link = client.app.state.settings.storage_dir / "outside-link"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
        response = clear(client)
        assert response.status_code == 200, response.text
    assert sentinel.read_bytes() == b"unchanged"


def test_result_write_failure_keeps_processing_blocked_until_retry(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path / "data")
    write_json = clear_data._write_json

    def fail_result(path, value):
        if path.name == "clear-data-result.json":
            raise PermissionError("result file unavailable")
        write_json(path, value)

    with client:
        upload(client)
        monkeypatch.setattr(clear_data, "_write_json", fail_result)
        assert clear(client).status_code == 409
        assert client.get("/api/v1/system/admin/clear-data").json()["incomplete"] is True
        assert client.post("/api/v1/tasks", files={"file": ("new.png", PNG_BYTES, "image/png")}).status_code == 503
        monkeypatch.setattr(clear_data, "_write_json", write_json)
        response = clear(client)
        assert response.status_code == 200, response.text
        assert response.json()["cleared_tasks"] == 1
        assert client.get("/api/v1/system/admin/clear-data").json()["incomplete"] is False


def test_confirmation_and_active_processing_guard_leave_data_untouched(tmp_path):
    client, _ = setup_client(tmp_path / "data")
    with client:
        task_id = upload(client)
        response = client.post("/api/v1/system/admin/clear-data", json={"confirm_text": "清除历史"})
        assert response.status_code == 422
        with client.app.state.session_factory() as session:
            session.get(TaskRecord, task_id).status = "processing"
            session.commit()
        assert clear(client).status_code == 409
        assert len(client.get("/api/v1/tasks").json()) == 1


def test_supervised_clear_schedules_stop_instead_of_erasing_running_database(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path / "data")
    calls = []
    monkeypatch.setenv("DOCUMENT_PIPELINE_SUPERVISED", "1")
    monkeypatch.setattr("document_pipeline_api.supervisor.request_clear_data", lambda root: calls.append(root) or True)
    with client:
        upload(client)
        response = clear(client)
        assert response.json() == {"scheduled": True, "restart_required": True}
        assert calls == [tmp_path / "data"]
        assert len(client.get("/api/v1/tasks").json()) == 1


def test_clear_waits_for_inflight_writes_and_rejects_new_mutations(tmp_path):
    client, _ = setup_client(tmp_path / "data")
    started, finish = threading.Event(), threading.Event()

    @client.app.post("/test-held-write")
    def held_write():
        started.set()
        assert finish.wait(5)
        from document_pipeline_api.services.system_settings import set_setting
        with client.app.state.session_factory() as session:
            set_setting(session, "held-write", "must be cleared")
            session.commit()
        return {"saved": True}

    with client, ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, "/test-held-write")
        assert started.wait(2)
        clearing = pool.submit(clear, client)
        try:
            deadline = time.monotonic() + 2
            while not client.app.state.maintenance_active and time.monotonic() < deadline:
                time.sleep(0.01)
            assert client.app.state.maintenance_active
            assert client.put("/api/v1/system/settings", json={"office_convert": True, "image_convert": True}).status_code == 503
        finally:
            finish.set()
        assert first.result().status_code == 200
        assert clearing.result().status_code == 200
        from document_pipeline_api.models.system_setting import SystemSettingRecord
        with client.app.state.session_factory() as session:
            assert session.get(SystemSettingRecord, "held-write") is None
