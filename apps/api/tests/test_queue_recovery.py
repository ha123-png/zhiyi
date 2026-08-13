from types import SimpleNamespace

from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services import queueing


def test_missing_queued_task_is_reenqueued_without_duplicating_pending_message(
    tmp_path,
    monkeypatch,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'queue-recovery.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                _queued_task("already-pending"),
                _queued_task("missing-message"),
            ]
        )
        session.commit()
        monkeypatch.setattr(
            queueing.huey,
            "pending",
            lambda: [SimpleNamespace(id="already-pending")],
        )
        enqueued: list[str] = []
        monkeypatch.setattr(queueing, "enqueue_task", enqueued.append)

        recovered = queueing.recover_missing_queued_tasks(session)

        assert recovered == 1
        assert enqueued == ["missing-message"]


def test_stale_pending_message_for_non_queued_task_is_revoked(tmp_path, monkeypatch) -> None:
    # 队列里残留的历史消息（任务已完成/waiting 等非 queued 状态）应在恢复周期自动
    # revoke，让 worker 下次碰到时跳过并删除，避免垃圾消息越积越多。
    engine = build_engine(f"sqlite:///{tmp_path / 'queue-recovery.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(_queued_task("still-queued"))
        session.add(
            TaskRecord(
                id="stale-done",
                filename="stale-done.png",
                content_type="image/png",
                size_bytes=1,
                sha256="digest-stale-done",
                storage_path="stale-done.png",
                template_mode="invoice",
                status=TaskStatus.COMPLETED.value,
            )
        )
        session.commit()
        monkeypatch.setattr(
            queueing.huey,
            "pending",
            lambda: [
                SimpleNamespace(id="still-queued"),
                SimpleNamespace(id="stale-done"),
                SimpleNamespace(id="periodic-uuid"),  # 周期任务消息，业务库无此任务
            ],
        )
        revoked: list[str] = []
        monkeypatch.setattr(queueing.huey, "revoke_by_id", lambda tid, revoke_once=True: revoked.append(tid))
        enqueued: list[str] = []
        monkeypatch.setattr(queueing, "enqueue_task", enqueued.append)

        recovered = queueing.recover_missing_queued_tasks(session)

        assert recovered == 0
        assert enqueued == []
        assert revoked == ["stale-done"]


def _queued_task(task_id: str) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        filename=f"{task_id}.png",
        content_type="image/png",
        size_bytes=1,
        sha256=f"digest-{task_id}",
        storage_path=f"{task_id}.png",
        template_mode="invoice",
        status=TaskStatus.QUEUED.value,
    )
