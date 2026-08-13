from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.services.task_leases import (
    acquire_task_lease,
    recover_expired_task_leases,
    transition_leased_task,
)


def _task(task_id: str, status: TaskStatus = TaskStatus.QUEUED) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        filename=f"{task_id}.png",
        content_type="image/png",
        size_bytes=1,
        sha256=f"digest-{task_id}",
        storage_path=f"{task_id}.png",
        template_mode="invoice",
        status=status.value,
    )


def test_only_one_worker_can_acquire_a_queued_task(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'lease.db'}")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    with Session(engine) as session:
        task = _task("task-1")
        # 模拟批量上传：任务早已创建，排队等待较长时间后才开始处理
        task.started_at = now - timedelta(minutes=30)
        session.add(task)
        session.commit()

        first = acquire_task_lease(
            session,
            "task-1",
            now=now,
            lease_for=timedelta(minutes=4),
        )
        second = acquire_task_lease(
            session,
            "task-1",
            now=now,
            lease_for=timedelta(minutes=4),
        )

        assert first is not None
        assert second is None
        task = session.get(TaskRecord, "task-1")
        assert task is not None
        assert task.status == TaskStatus.PROCESSING.value
        assert task.attempt_count == 1
        # 开始处理时计时起点重置为当前时刻，而不是上传/排队时刻
        # （SQLite 存储会丢失 tzinfo，读回为 naive datetime，比较前补回 UTC）
        assert task.started_at is not None
        assert task.started_at.replace(tzinfo=timezone.utc) == now


def test_stale_worker_token_cannot_advance_a_reclaimed_task(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'fence.db'}")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add(_task("task-1"))
        session.commit()
        first = acquire_task_lease(
            session,
            "task-1",
            now=now,
            lease_for=timedelta(seconds=1),
        )
        assert first is not None
        assert recover_expired_task_leases(
            session,
            now=now + timedelta(seconds=2),
        ) == ["task-1"]

        task = session.get(TaskRecord, "task-1")
        assert task is not None
        task.status = TaskStatus.QUEUED.value
        session.commit()
        second = acquire_task_lease(
            session,
            "task-1",
            now=now + timedelta(seconds=3),
            lease_for=timedelta(minutes=4),
        )
        assert second is not None

        assert not transition_leased_task(
            session,
            "task-1",
            first.token,
            expected=TaskStatus.PROCESSING,
            target=TaskStatus.VALIDATING,
            now=now + timedelta(seconds=3),
            lease_for=timedelta(minutes=1),
        )
        session.refresh(task)
        assert task.status == TaskStatus.PROCESSING.value
        assert task.lease_token == second.token


def test_expired_processing_tasks_fail_with_actionable_reason(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    with Session(engine) as session:
        expired = _task("expired")
        active = _task("active")
        session.add_all([expired, active])
        session.commit()
        acquire_task_lease(
            session,
            "expired",
            now=now,
            lease_for=timedelta(seconds=1),
        )
        acquire_task_lease(
            session,
            "active",
            now=now,
            lease_for=timedelta(minutes=5),
        )

        recovered = recover_expired_task_leases(
            session,
            now=now + timedelta(seconds=2),
        )

        assert recovered == ["expired"]
        session.refresh(expired)
        session.refresh(active)
        assert expired.status == TaskStatus.FAILED.value
        assert expired.failure_code == "worker_interrupted"
        assert "重新处理" in (expired.failure_message or "")
        assert expired.lease_token is None
        assert active.status == TaskStatus.PROCESSING.value
