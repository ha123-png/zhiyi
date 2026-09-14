from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from document_pipeline_api.api.events import poll_task_changes
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models.task import TaskRecord


def _task(task_id: str, status: str = "queued", now: datetime | None = None) -> TaskRecord:
    stamp = now or datetime.now(timezone.utc)
    return TaskRecord(
        id=task_id,
        filename=f"{task_id}.txt",
        content_type="text/plain",
        size_bytes=1,
        sha256=f"digest-{task_id}",
        storage_path=f"{task_id}.txt",
        template_mode="smart",
        status=status,
        created_at=stamp,
        updated_at=stamp,
    )


def test_poll_task_changes_returns_only_newer_than_cursor(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'poll.db'}")
    Base.metadata.create_all(engine)
    old_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    with Session(engine) as session:
        session.add(_task("old", now=old_time))
        session.add(_task("new"))
        session.commit()

    with Session(engine) as session:
        # 游标落在旧任务之后、新任务之前：只应推新任务
        cursor = old_time + timedelta(seconds=1)
        payload, new_cursor = poll_task_changes(session, cursor)

    ids = {item["id"] for item in payload}
    assert "new" in ids
    assert "old" not in ids
    assert new_cursor[0] > cursor
    # 事件载荷携带文件名与失败原因：前端全局日志与失败 toast 依赖这两个字段
    new_event = next(item for item in payload if item["id"] == "new")
    assert new_event["filename"] == "new.txt"
    assert "failure_message" in new_event
    # 游标推进后不再重复推送
    with Session(engine) as session:
        payload2, _ = poll_task_changes(session, new_cursor)
    assert payload2 == []


def test_poll_task_changes_keeps_cursor_when_no_changes(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'poll2.db'}")
    Base.metadata.create_all(engine)
    cursor = datetime.now(timezone.utc)
    with Session(engine) as session:
        payload, new_cursor = poll_task_changes(session, cursor)
    assert payload == []
    assert new_cursor == cursor


def test_bulk_updates_with_identical_timestamp_are_not_skipped(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'bulk.db'}")
    Base.metadata.create_all(engine)
    stamp = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add_all([_task(f"task-{i:03}", now=stamp) for i in range(250)])
        session.commit()
        cursor = stamp - timedelta(seconds=1)
        seen = []
        for _ in range(4):
            payload, cursor = poll_task_changes(session, cursor)
            seen.extend(item["id"] for item in payload)
        assert len(seen) == len(set(seen)) == 250
