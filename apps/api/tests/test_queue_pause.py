from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.main import create_app
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.services import extraction
from document_pipeline_api.services.task_leases import acquire_task_lease


def _task(task_id: str, status: str) -> TaskRecord:
    now = datetime.now(timezone.utc)
    lease = now + timedelta(minutes=5) if status == "processing" else None
    return TaskRecord(
        id=task_id,
        filename=f"{task_id}.txt",
        content_type="text/plain",
        size_bytes=1,
        sha256=f"digest-{task_id}",
        storage_path=f"{task_id}.txt",
        template_mode="smart",
        status=status,
        lease_token="token" if lease else None,
        lease_expires_at=lease,
        created_at=now,
        updated_at=now,
    )


def test_queue_pause_freezes_queue_and_interrupts_processing(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'queue.db'}",
        storage_dir=tmp_path / "uploads",
        model_name="test-model",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        engine = build_engine(settings.database_url)
        with Session(engine) as session:
            session.add_all(
                [_task("processing-1", "processing"), _task("queued-1", "queued")]
            )
            session.commit()

        assert client.post("/api/v1/queue/pause").json() == {"paused": True}
        assert client.get("/api/v1/queue/status").json() == {"paused": True}

        with Session(engine) as session:
            processing = session.get(TaskRecord, "processing-1")
            queued = session.get(TaskRecord, "queued-1")
            # 冻结语义：当前处理中的任务被中断为暂停；排队任务也标记为暂停（可见冻结），
            # 前端据此显示冻结状态并提供「恢复全部」入口
            assert processing is not None and processing.status == "paused"
            assert processing.lease_token is None
            assert queued is not None and queued.status == "paused"
            assert queued.lease_token is None


def test_queue_resume_puts_all_paused_tasks_back(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'queue2.db'}",
        storage_dir=tmp_path / "uploads",
        model_name="test-model",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        engine = build_engine(settings.database_url)
        with Session(engine) as session:
            session.add_all(
                [_task("paused-1", "paused"), _task("queued-1", "queued")]
            )
            session.commit()
        client.post("/api/v1/queue/pause")

        assert client.post("/api/v1/queue/resume").json() == {"paused": False}
        assert client.get("/api/v1/queue/status").json() == {"paused": False}

        with Session(engine) as session:
            paused = session.get(TaskRecord, "paused-1")
            queued = session.get(TaskRecord, "queued-1")
            # 全部放回队列继续处理
            assert paused is not None and paused.status == "queued"
            assert queued is not None and queued.status == "queued"


def test_upload_during_paused_queue_stays_frozen_until_queue_start(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'upload-paused.db'}",
        storage_dir=tmp_path / "uploads",
        model_name="test-model",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/queue/pause").status_code == 200
        created = client.post(
            "/api/v1/tasks",
            files={"file": ("frozen.txt", b"hello", "text/plain")},
            data={"template_mode": "smart"},
        )
        assert created.status_code == 201
        assert created.json()["status"] == "paused"

        task_id = created.json()["id"]
        blocked = client.post(f"/api/v1/tasks/{task_id}/resume")
        assert blocked.status_code == 409
        assert "启动整个队列" in blocked.json()["detail"]

        assert client.post("/api/v1/queue/resume").json() == {"paused": False}
        tasks = client.get("/api/v1/tasks", params={"active_only": True}).json()
        assert next(task for task in tasks if task["id"] == task_id)["status"] == "queued"


def test_worker_starting_during_pause_race_reverts_to_paused(
    tmp_path, monkeypatch
) -> None:
    """暂停与 Worker 拿租约竞态：暂停事务在「首次检查标志 → 获取租约」之间提交，
    任务刚拿到租约但队列已被冻结，必须退回为已暂停，不能继续处理。"""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'race.db'}",
        storage_dir=tmp_path / "uploads",
        model_name="test-model",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        engine = build_engine(settings.database_url)
        with Session(engine) as session:
            session.add(_task("race-1", "queued"))
            session.commit()

        real_acquire = acquire_task_lease

        def acquire_after_pause(session, task_id, *, lease_for, now=None):
            # 模拟暂停已提交但任务没被暂停 UPDATE 命中的竞态（Worker 读到旧快照）：
            # 只落暂停标志，不标记任务，让真实获取租约成功，再由二次检查把租约退回
            from document_pipeline_api.services.system_settings import set_bool_setting
            from document_pipeline_api.services.queue_pause import QUEUE_PAUSED_KEY

            set_bool_setting(session, QUEUE_PAUSED_KEY, True)
            session.commit()
            return real_acquire(session, task_id, lease_for=lease_for, now=now)

        monkeypatch.setattr(
            "document_pipeline_api.services.extraction.acquire_task_lease",
            acquire_after_pause,
        )

        with pytest.raises(HTTPException) as excinfo:
            with Session(engine) as session:
                extraction.process_task(session, settings, "race-1")
        assert excinfo.value.status_code == 409

        with Session(engine) as session:
            task = session.get(TaskRecord, "race-1")
            # 冻结语义：暂停后任务不能开始处理，必须退回为已暂停且释放租约
            assert task is not None and task.status == "paused"
            assert task.lease_token is None
            assert client.get("/api/v1/queue/status").json() == {"paused": True}


def test_pause_is_cleared_when_queue_emptied(tmp_path) -> None:
    """暂停后删除全部任务：队列清空自动解除暂停，新上传不再被冻结。"""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'empty.db'}",
        storage_dir=tmp_path / "uploads",
        model_name="test-model",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        engine = build_engine(settings.database_url)
        with Session(engine) as session:
            session.add_all(
                [_task("paused-1", "paused"), _task("paused-2", "paused")]
            )
            session.commit()
        client.post("/api/v1/queue/pause")
        assert client.get("/api/v1/queue/status").json() == {"paused": True}

        deleted = client.post(
            "/api/v1/tasks/batch-delete",
            json={"task_ids": ["paused-1", "paused-2"]},
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] == 2

        # 队列空了，暂停标志自动清除：新上传默认直接处理，不再被冻结
        assert client.get("/api/v1/queue/status").json() == {"paused": False}

        uploaded = client.post(
            "/api/v1/tasks",
            files={"file": ("fresh.txt", "仓库\n数量\t单价\n5\t10\n".encode(), "text/plain")},
            data={"template_mode": "invoice"},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["status"] == "queued"


def test_pause_kept_when_partial_delete_leaves_active_tasks(tmp_path) -> None:
    """只删一部分暂停任务、队列仍有任务时，暂停保持不变。"""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'partial.db'}",
        storage_dir=tmp_path / "uploads",
        model_name="test-model",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        engine = build_engine(settings.database_url)
        with Session(engine) as session:
            session.add_all(
                [_task("paused-1", "paused"), _task("paused-2", "paused")]
            )
            session.commit()
        client.post("/api/v1/queue/pause")

        deleted = client.post(
            "/api/v1/tasks/batch-delete",
            json={"task_ids": ["paused-1"]},
        )
        assert deleted.status_code == 200

        # 还剩一个暂停任务：队列未清空，暂停继续生效
        assert client.get("/api/v1/queue/status").json() == {"paused": True}
