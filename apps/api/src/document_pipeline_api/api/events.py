"""任务状态变化 SSE 实时推送。

API 与 Worker 是独立进程，但共享同一 SQLite。本端点由 API 进程持有，
周期性增量查询 tasks.updated_at，把最近变化推送给前端；前端收到事件后
立即刷新，实现"上传马上开始、停止马上停止、完成马上闲置"，替代纯轮询延迟。

Worker 进程的任务状态变化（开始处理/完成/失败）同样写回 SQLite 的 updated_at，
因此跨进程天然可见，不需要进程间通信。
"""

import json
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.models.task import TaskRecord

router = APIRouter(tags=["events"])

_POLL_INTERVAL_SECONDS = 1.0
_MAX_EVENTS_PER_POLL = 100
_INITIAL_WINDOW_SECONDS = 30


def _as_aware(value: datetime) -> datetime:
    # SQLite 存储会丢失 tzinfo，读回为 naive datetime；统一按 UTC 补回，
    # 保证游标在多次轮询间可正确比较。
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def poll_task_changes(
    session: Session,
    last_poll: datetime,
) -> tuple[list[dict[str, object]], datetime]:
    """增量查询 last_poll 之后更新的任务，返回事件载荷与新的游标。

    纯函数便于测试；SSE 生成器每轮调用一次。
    """
    rows = session.execute(
        select(
            TaskRecord.id,
            TaskRecord.status,
            TaskRecord.filename,
            TaskRecord.failure_message,
            TaskRecord.updated_at,
            func.json_extract(TaskRecord.export_state_json, "$.status").label("export_status"),
        )
        .where(TaskRecord.updated_at > last_poll)
        .order_by(TaskRecord.updated_at)
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
        }
        for row in rows
    ]
    return payload, _as_aware(rows[-1].updated_at)


@router.get("/events")
def task_events(request: Request) -> StreamingResponse:
    session_factory = request.app.state.session_factory

    def event_stream():
        # 起始窗口覆盖连接建立前刚发生的状态变化，避免漏推
        last_poll = datetime.now(timezone.utc) - timedelta(
            seconds=_INITIAL_WINDOW_SECONDS
        )
        while True:
            try:
                with session_factory() as session:
                    payload, last_poll = poll_task_changes(session, last_poll)
                if payload:
                    yield (
                        f"event: task\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
            except Exception:
                # 查询失败不影响连接：下一轮重试
                pass
            # 心跳注释帧：维持代理/浏览器连接，防止长时间无数据被断开
            yield ": ping\n\n"
            time.sleep(_POLL_INTERVAL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
