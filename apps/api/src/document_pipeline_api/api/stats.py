from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.db import get_session
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import ExtractionRecord, TaskRecord


router = APIRouter(prefix="/stats", tags=["stats"])
SessionDependency = Annotated[Session, Depends(get_session)]


class DashboardSummary(BaseModel):
    average_elapsed_seconds: float | None = None
    processed_count: int = 0
    failed_count: int = 0
    success_rate: float | None = None


class TrendPoint(BaseModel):
    """按天聚合的任务计数（趋势图与范围统计），避免前端拉全量任务表。"""

    date: str
    total: int = 0
    completed: int = 0
    failed: int = 0


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(session: SessionDependency) -> DashboardSummary:
    """仪表盘真实统计：平均处理耗时（来自提取记录）、处理数与失败率。

    处理数/失败数用 SQL 聚合（COUNT + WHERE），不加载任务全表（ISSUE-067）。
    """
    average_elapsed = session.scalar(
        select(func.avg(ExtractionRecord.elapsed_seconds))
    )
    processed = (
        session.scalar(
            select(func.count())
            .select_from(TaskRecord)
            .where(
                TaskRecord.status.in_(
                    [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value]
                )
            )
        )
        or 0
    )
    failed = (
        session.scalar(
            select(func.count())
            .select_from(TaskRecord)
            .where(TaskRecord.status == TaskStatus.FAILED.value)
        )
        or 0
    )
    return DashboardSummary(
        average_elapsed_seconds=(
            round(float(average_elapsed), 1) if average_elapsed is not None else None
        ),
        processed_count=processed,
        failed_count=failed,
        success_rate=(
            round((processed - failed) / processed, 4) if processed > 0 else None
        ),
    )


@router.get("/trend", response_model=list[TrendPoint])
def dashboard_trend(session: SessionDependency, days: int = 7) -> list[TrendPoint]:
    """按天聚合任务数（仪表盘趋势图与范围统计）。

    SQL 按日期+状态分组聚合，不加载任务全表；天界按服务器本地时区切分，
    与前端展示保持一致。days 限制 1～366。
    """
    days = max(1, min(days, 366))
    # 本地时区午夜对齐（created_at 存 UTC，SQLite localtime 也按服务器本地切天）
    start_local = (datetime.now().astimezone() - timedelta(days=days - 1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    start_utc = start_local.astimezone(timezone.utc)
    rows = session.execute(
        select(
            func.date(func.datetime(TaskRecord.created_at, "localtime")),
            TaskRecord.status,
            func.count(),
        )
        .where(TaskRecord.created_at >= start_utc)
        .group_by(
            func.date(func.datetime(TaskRecord.created_at, "localtime")),
            TaskRecord.status,
        )
    ).all()
    buckets: dict[str, dict[str, int]] = {}
    for date_str, status, count in rows:
        buckets.setdefault(date_str, {})[status] = count

    points: list[TrendPoint] = []
    for index in range(days):
        date_str = (start_local + timedelta(days=index)).date().isoformat()
        bucket = buckets.get(date_str, {})
        points.append(
            TrendPoint(
                date=date_str,
                total=sum(bucket.values()),
                completed=bucket.get(TaskStatus.COMPLETED.value, 0),
                failed=bucket.get(TaskStatus.FAILED.value, 0),
            )
        )
    return points
