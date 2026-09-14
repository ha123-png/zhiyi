from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from document_pipeline_api.db import get_session
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import ExtractionRecord, TaskRecord
from document_pipeline_api.models.data_table import DataRowRecord, DataTableRecord
from document_pipeline_api.models.template import TemplateRecord, TemplateVersionRecord
from document_pipeline_api.models.dashboard import DashboardCard
from document_pipeline_api.schemas.dashboard import CardInput, CardOrder, CardUpdate
from document_pipeline_api.services import dashboard
from document_pipeline_api.services.dashboard_usage import usage_summary


router = APIRouter(prefix="/stats", tags=["stats"])
SessionDependency = Annotated[Session, Depends(get_session)]
DashboardRange = int | Literal["all"]


class DashboardSummary(BaseModel):
    current_tasks: int = 0
    review_pending: int = 0
    received_count: int = 0
    completed_count: int = 0
    average_elapsed_seconds: float | None = None
    processed_count: int = 0
    failed_count: int = 0
    success_rate: float | None = None
    elapsed_sample_count: int = 0
    new_rows: int = 0
    table_count: int = 0
    row_count: int = 0
    template_count: int = 0


class TrendPoint(BaseModel):
    """Bounded calendar buckets, with explicit granularity and no discarded history."""

    date: str
    total: int = 0
    completed: int = 0
    failed: int = 0
    received: int = 0
    completed_on_date: int = 0
    new_rows: int = 0
    bucket: Literal["day", "month", "year"] = "day"
    interval: int = 1
    label: str = ""


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(session: SessionDependency, days: DashboardRange | None = None) -> DashboardSummary:
    """仪表盘真实统计：平均处理耗时（来自提取记录）、处理数与失败率。

    处理数/失败数用 SQL 聚合（COUNT + WHERE），不加载任务全表（ISSUE-067）。
    """
    elapsed_query = select(func.avg(ExtractionRecord.elapsed_seconds), func.count(ExtractionRecord.elapsed_seconds))
    rows_query = select(func.count()).select_from(DataRowRecord)
    received_query = select(func.count()).select_from(TaskRecord)
    completed_query = select(func.count()).select_from(TaskRecord).where(TaskRecord.status == TaskStatus.COMPLETED.value, TaskRecord.completed_at.is_not(None))
    if days is not None and days != "all":
        start = _range_start(max(1, min(days, 366))).astimezone(timezone.utc)
        elapsed_query = elapsed_query.where(ExtractionRecord.created_at >= start)
        rows_query = rows_query.where(DataRowRecord.created_at >= start)
        received_query = received_query.where(TaskRecord.created_at >= start)
        completed_query = completed_query.where(TaskRecord.completed_at >= start)
    average_elapsed, elapsed_samples = session.execute(elapsed_query).one()
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
        current_tasks=session.scalar(select(func.count()).select_from(TaskRecord)) or 0,
        review_pending=session.scalar(select(func.count()).select_from(TaskRecord).where(TaskRecord.status == TaskStatus.NEEDS_REVIEW.value)) or 0,
        received_count=session.scalar(received_query) or 0,
        completed_count=session.scalar(completed_query) or 0,
        elapsed_sample_count=elapsed_samples,
        new_rows=session.scalar(rows_query) or 0,
        table_count=session.scalar(select(func.count()).select_from(DataTableRecord)) or 0,
        row_count=session.scalar(select(func.count()).select_from(DataRowRecord)) or 0,
        template_count=session.scalar(select(func.count()).select_from(TemplateRecord).where(TemplateRecord.is_active.is_(True))) or 0,
        average_elapsed_seconds=(
            round(float(average_elapsed), 1) if average_elapsed is not None else None
        ),
        processed_count=processed,
        failed_count=failed,
        success_rate=(
            round((processed - failed) / processed, 4) if processed > 0 else None
        ),
    )


def _range_start(days: int) -> datetime:
    return (datetime.now().astimezone() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )


def _trend_calendar(session, days: DashboardRange):
    if days != "all":
        count = max(1, min(days, 366))
        start = _range_start(count)
        return "day", 1, [(start + timedelta(days=i)).date().isoformat() for i in range(count)]
    bounds = []
    for column in (TaskRecord.created_at, TaskRecord.completed_at, DataRowRecord.created_at):
        # Convert the aggregate bounds with SQLite's same calendar expression.
        # Windows Python localtime cannot convert some valid pre-1970 values.
        bounds.extend(value for value in session.execute(select(func.date(func.min(column), "localtime"), func.date(func.max(column), "localtime"))).one() if value is not None)
    if not bounds:
        return "day", 1, []
    local_dates = [datetime.fromisoformat(value).date() for value in bounds]
    start, end = min(local_dates), max(datetime.now().astimezone().date(), max(local_dates))
    day_count = (end - start).days + 1
    if day_count <= 90:
        return "day", 1, [(start + timedelta(days=i)).isoformat() for i in range(day_count)]
    month_count = (end.year - start.year) * 12 + end.month - start.month + 1
    if month_count <= 120:
        first = start.year * 12 + start.month - 1
        return "month", 1, [f"{(first + i) // 12:04d}-{(first + i) % 12 + 1:02d}" for i in range(month_count)]
    year_count = end.year - start.year + 1
    interval = next(size for size in (1, 2, 5, 10, 20, 50, 100) if (year_count + size - 1) // size + 1 <= 120)
    first = ((start.year - 1) // interval) * interval + 1
    return "year", interval, [f"{year:04d}" for year in range(first, end.year + 1, interval)]


def _bucket_expression(column, bucket, interval):
    local = func.datetime(column, "localtime")
    if bucket == "day":
        return func.date(local)
    if bucket == "month":
        return func.strftime("%Y-%m", local)
    year = cast(func.strftime("%Y", local), Integer)
    anchor = cast((year - 1) / interval, Integer) * interval + 1
    return func.printf("%04d", anchor)


@router.get("/trend", response_model=list[TrendPoint])
def dashboard_trend(session: SessionDependency, days: DashboardRange = 7) -> list[TrendPoint]:
    """Fixed ranges use local days; all retained history uses bounded day/month/year buckets."""
    bucket, interval, labels = _trend_calendar(session, days)
    if not labels:
        return []
    start = None if days == "all" else _range_start(max(1, min(days, 366))).astimezone(timezone.utc)
    received_key = _bucket_expression(TaskRecord.created_at, bucket, interval)
    received_query = select(received_key, TaskRecord.status, func.count()).group_by(received_key, TaskRecord.status)
    if start is not None:
        received_query = received_query.where(TaskRecord.created_at >= start)
    received = {}
    for key, status, count in session.execute(received_query):
        received.setdefault(key, {})[status] = count
    completed_key = _bucket_expression(TaskRecord.completed_at, bucket, interval)
    completed_query = select(completed_key, func.count()).where(TaskRecord.status == TaskStatus.COMPLETED.value, TaskRecord.completed_at.is_not(None)).group_by(completed_key)
    rows_key = _bucket_expression(DataRowRecord.created_at, bucket, interval)
    rows_query = select(rows_key, func.count()).group_by(rows_key)
    if start is not None:
        completed_query = completed_query.where(TaskRecord.completed_at >= start)
        rows_query = rows_query.where(DataRowRecord.created_at >= start)
    completed = dict(session.execute(completed_query).all())
    new_rows = dict(session.execute(rows_query).all())
    return [TrendPoint(date=key, label=key if interval == 1 else f"{key}–{min(9999, int(key) + interval - 1)}",
        bucket=bucket, interval=interval, total=sum(received.get(key, {}).values()),
        received=sum(received.get(key, {}).values()), completed=received.get(key, {}).get(TaskStatus.COMPLETED.value, 0),
        failed=received.get(key, {}).get(TaskStatus.FAILED.value, 0), completed_on_date=completed.get(key, 0),
        new_rows=new_rows.get(key, 0)) for key in labels]


@router.get("/card-options")
def dashboard_card_options(session: SessionDependency):
    return dashboard.card_options(session)


@router.get("/cards")
def dashboard_cards(session: SessionDependency):
    return {"items": [dashboard.card_read(c) for c in session.scalars(select(DashboardCard).order_by(DashboardCard.position, DashboardCard.id))], "limit": dashboard.CARD_LIMIT}


@router.post("/cards/preview")
def preview_card(body: CardInput, session: SessionDependency, days: DashboardRange = 7):
    return dashboard.card_result(session, body, days)


@router.put("/cards/order")
def order_cards(body: CardOrder, session: SessionDependency):
    return dashboard.reorder_cards(session, body.ids)


@router.post("/cards", status_code=201)
def create_card(body: CardInput, session: SessionDependency, days: DashboardRange = 7):
    return dashboard.save_card(session, body, days=days)


@router.put("/cards/{identifier}")
def update_card(identifier: str, body: CardUpdate, session: SessionDependency, days: DashboardRange = 7):
    return dashboard.save_card(session, body, identifier, days=days)


@router.delete("/cards/{identifier}", status_code=204)
def delete_card(identifier: str, session: SessionDependency):
    card = session.get(DashboardCard, identifier)
    if card is None:
        raise HTTPException(404, "统计卡已删除。")
    session.delete(card)
    session.commit()
    return Response(status_code=204)


@router.get("/cards/{identifier}/result")
def read_card_result(identifier: str, session: SessionDependency, days: DashboardRange = 7):
    return dashboard.saved_result(session, identifier, days)


@router.get("/overview")
def dashboard_overview(session: SessionDependency, days: DashboardRange = 7):
    start = None if days == "all" else _range_start(max(1, min(days, 366))).astimezone(timezone.utc)
    usage = usage_summary(session, start)
    template_query = (select(TemplateRecord.id, TemplateVersionRecord.name, func.count(ExtractionRecord.task_id))
        .join(TemplateVersionRecord, (TemplateVersionRecord.template_id == TemplateRecord.id) & (TemplateVersionRecord.version == TemplateRecord.current_version))
        .join(ExtractionRecord, ExtractionRecord.template_id == TemplateRecord.id))
    if start is not None:
        template_query = template_query.where(ExtractionRecord.created_at >= start)
    template_rows = session.execute(template_query
        .group_by(TemplateRecord.id, TemplateVersionRecord.name)
        .order_by(func.count(ExtractionRecord.task_id).desc(), TemplateRecord.id).limit(6)).all()
    trend = dashboard_trend(session, days)
    return {"rows_trend": [{"date": point.date, "label": point.label, "count": point.new_rows} for point in trend],
            "trend_bucket": trend[0].bucket if trend else "day", "trend_interval": trend[0].interval if trend else 1,
            "templates": [{"id": identifier, "name": name, "count": count} for identifier, name, count in template_rows],
            "model_usage": usage}
