"""任务状态变化 SSE 实时推送。

API 与 Worker 是独立进程，但共享同一 SQLite。本端点由 API 进程持有，
周期性增量查询 tasks.updated_at，把最近变化推送给前端；前端收到事件后
立即刷新，实现"上传马上开始、停止马上停止、完成马上闲置"，替代纯轮询延迟。

Worker 进程的任务状态变化（开始处理/完成/失败）同样写回 SQLite 的 updated_at，
因此跨进程天然可见，不需要进程间通信。
"""

import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, and_, or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from document_pipeline_api.models.task import TaskRecord

router = APIRouter(tags=["events"])
logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0
_MAX_EVENTS_PER_POLL = 100
_INITIAL_WINDOW_SECONDS = 30


def _as_aware(value: datetime) -> datetime:
    # SQLite 存储会丢失 tzinfo，读回为 naive datetime；统一按 UTC 补回，
    # 保证游标在多次轮询间可正确比较。
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def encode_task_cursor(cursor: tuple[datetime, str]) -> str:
    return json.dumps([cursor[0].isoformat(), cursor[1]], separators=(",", ":"))


def restore_task_cursor(raw: str | None, fallback: datetime) -> datetime | tuple[datetime, str]:
    """EventSource sends the last delivered event ID when reconnecting."""
    try:
        value = json.loads(raw or "null")
        if (not isinstance(value, list) or len(value) != 2
                or not isinstance(value[1], str) or len(value[1]) > 256):
            return fallback
        stamp = _as_aware(datetime.fromisoformat(value[0]))
        if stamp > datetime.now(timezone.utc):
            return fallback
        return stamp, value[1]
    except (ValueError, TypeError):
        return fallback


def poll_task_changes(
    session: Session,
    last_poll: datetime | tuple[datetime, str],
) -> tuple[list[dict[str, object]], datetime | tuple[datetime, str]]:
    """增量查询 last_poll 之后更新的任务，返回事件载荷与新的游标。

    纯函数便于测试；SSE 生成器每轮调用一次。
    """
    if isinstance(last_poll, tuple):
        stamp, identifier = last_poll
        after = or_(TaskRecord.updated_at > stamp, and_(TaskRecord.updated_at == stamp, TaskRecord.id > identifier))
    else:
        after = TaskRecord.updated_at > last_poll
    rows = session.execute(
        select(
            TaskRecord.id,
            TaskRecord.status,
            TaskRecord.filename,
            TaskRecord.failure_message,
            TaskRecord.updated_at,
            func.json_extract(TaskRecord.export_state_json, "$.status").label("export_status"),
            func.json_extract(TaskRecord.export_state_json, "$.mode").label("export_mode"),
        )
        .where(after)
        .order_by(TaskRecord.updated_at, TaskRecord.id)
        .limit(_MAX_EVENTS_PER_POLL)
    ).all()
    if not rows:
        return [], last_poll
    payload = [
        {
            "id": row.id,
            "status": row.status,
            "filename": row.filename,
            "failure_message": row.failure_message,
            "export_status": row.export_status,
            "export_mode": row.export_mode,
        }
        for row in rows
    ]
    return payload, (_as_aware(rows[-1].updated_at), rows[-1].id)


@router.get("/events")
def task_events(request: Request) -> StreamingResponse:
    session_factory = request.app.state.session_factory

    def poll(cursor):
        with session_factory() as session:
            return poll_task_changes(session, cursor)

    async def event_stream():
        # 起始窗口覆盖连接建立前刚发生的状态变化，避免漏推
        last_poll = restore_task_cursor(
            request.headers.get("last-event-id"),
            datetime.now(timezone.utc) - timedelta(seconds=_INITIAL_WINDOW_SECONDS),
        )
        query_failed = False
        while not await request.is_disconnected():
            try:
                payload, last_poll = await run_in_threadpool(poll, last_poll)
                if payload:
                    yield (
                        f"id: {encode_task_cursor(last_poll)}\n"
                        f"event: task\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                query_failed = False
            except Exception:
                # 查询失败不影响连接：下一轮重试
                if not query_failed:
                    logger.warning("任务事件查询失败，将在连接中重试。", exc_info=True)
                query_failed = True
            # 心跳注释帧：维持代理/浏览器连接，防止长时间无数据被断开
            yield ": ping\n\n"
            # Drain a large timestamp batch promptly. Yielding to the event loop
            # still permits disconnect/cancellation without holding a worker thread.
            await asyncio.sleep(0 if not query_failed and len(payload) == _MAX_EVENTS_PER_POLL
                                else _POLL_INTERVAL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
