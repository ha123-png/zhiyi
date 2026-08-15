"""ISSUE-069：普通用户备份 API 测试（创建/列表/状态/删除/保留/恢复）。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from image_test_data import PNG_BYTES


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'document-pipeline.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _add_task(client: TestClient) -> str:
    """通过真实上传创建任务：business_backup 要求原文件与 storage_path 一致。"""
    response = client.post(
        "/api/v1/tasks",
        files={"file": ("invoice.png", PNG_BYTES, "image/png")},
        data={"template_mode": "invoice"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _backup_dir(client: TestClient) -> Path:
    return client.app.state.settings.storage_dir.parent / "backups"


def test_backup_status_empty_then_after_create(client: TestClient) -> None:
    status = client.get("/api/v1/backups/status").json()
    assert status["count"] == 0
    assert status["last_success_at"] is None
    assert status["retention"] == 10

    _add_task(client)
    response = client.post("/api/v1/backups")
    assert response.status_code == 201
    backup = response.json()
    assert backup["name"].endswith(".dpbak")
    assert backup["size_bytes"] > 0

    backups = client.get("/api/v1/backups").json()
    assert [item["name"] for item in backups] == [backup["name"]]

    status = client.get("/api/v1/backups/status").json()
    assert status["count"] == 1
    assert status["last_success_at"] is not None
    assert (_backup_dir(client) / backup["name"]).is_file()


def test_backup_retention_keeps_newest(client: TestClient, tmp_path: Path) -> None:
    # 预置 12 个按时间递增命名的占位备份
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        name = f"20260809T100000{index:05d}Z.dpbak"
        (backup_dir / name).write_bytes(b"placeholder")

    _add_task(client)
    response = client.post("/api/v1/backups")
    assert response.status_code == 201
    created = response.json()["name"]

    backups = client.get("/api/v1/backups").json()
    assert len(backups) == 10
    assert backups[0]["name"] == created  # 最新在前


def test_delete_backup(client: TestClient) -> None:
    _add_task(client)
    created = client.post("/api/v1/backups").json()["name"]

    assert client.delete(f"/api/v1/backups/{created}").status_code == 204
    assert client.get("/api/v1/backups").json() == []
    assert not (_backup_dir(client) / created).exists()

    # 非法名称 / 不存在
    assert client.delete("/api/v1/backups/not-a-backup.dpbak").status_code == 404
    assert client.delete(f"/api/v1/backups/{created}").status_code == 404


def test_restore_returns_previous_data_and_keeps_current_after_failure(
    client: TestClient,
) -> None:
    task_id = _add_task(client)
    # 上传任务为 queued，先落到 completed 才能通过 API 删除
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.status = "completed"
        session.commit()
    created = client.post("/api/v1/backups").json()["name"]

    # 备份后删除任务，模拟数据丢失
    assert client.delete(f"/api/v1/tasks/{task_id}").status_code == 200
    assert client.get("/api/v1/tasks").json() == []

    # 恢复：任务回来
    response = client.post(f"/api/v1/backups/{created}/restore")
    assert response.status_code == 200
    result = response.json()
    assert result["restart_required"] is True
    task_ids = [t["id"] for t in client.get("/api/v1/tasks").json()]
    assert task_id in task_ids

    # 损坏的备份：恢复失败且不覆盖当前数据
    corrupt = _backup_dir(client) / "20260810T000000000000Z.dpbak"
    corrupt.write_bytes(b"not a zip archive")
    failed = client.post("/api/v1/backups/20260810T000000000000Z.dpbak/restore")
    assert failed.status_code == 409
    assert "备份格式损坏" in failed.json()["detail"]
    task_ids = [t["id"] for t in client.get("/api/v1/tasks").json()]
    assert task_id in task_ids


def test_formal_runtime_schedules_restore_through_supervisor(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_task(client)
    created = client.post("/api/v1/backups").json()["name"]
    runtime_dir = client.app.state.settings.storage_dir.parent / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    (runtime_dir / "supervisor.json").write_text("{}", encoding="utf-8")
    scheduled: list[tuple[Path, str]] = []

    def fake_request_restore(data_dir: Path, name: str) -> bool:
        scheduled.append((data_dir, name))
        return True

    monkeypatch.setattr(
        "document_pipeline_api.supervisor.request_restore",
        fake_request_restore,
    )
    response = client.post(f"/api/v1/backups/{created}/restore")

    assert response.status_code == 200
    assert response.json() == {
        "rollback_dir": None,
        "restart_required": True,
        "scheduled": True,
    }
    assert scheduled == [(client.app.state.settings.storage_dir.parent, created)]


def test_backup_status_reports_supervised_restore_result(client: TestClient) -> None:
    runtime_dir = client.app.state.settings.storage_dir.parent / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    (runtime_dir / "restore-result.json").write_text(
        '{"state":"failed","message":"恢复失败但原数据保持不变"}',
        encoding="utf-8",
    )

    response = client.get("/api/v1/backups/status")

    assert response.status_code == 200
    assert response.json()["restore_state"] == "failed"
    assert "原数据保持不变" in response.json()["restore_message"]
