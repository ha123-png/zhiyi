from document_pipeline_api.model_diagnostics import safe_diagnostic, diagnostic_summary
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from document_pipeline_api.domain.tasks import (
    ALLOWED_TRANSITIONS,
    TaskStatus,
)
from document_pipeline_api.models.task import TaskRecord, utc_now


@dataclass(frozen=True)
class TaskLease:
    token: str
    expires_at: datetime


class TaskLeaseLostError(RuntimeError):
    """Raised when a worker tries to commit after its lease was replaced."""


def acquire_task_lease(
    session: Session,
    task_id: str,
    *,
    lease_for: timedelta,
    now: datetime | None = None,
) -> TaskLease | None:
    claimed_at = now or utc_now()
    expires_at = claimed_at + lease_for
    token = str(uuid4())
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.status == TaskStatus.QUEUED.value,
        )
        .values(
            status=TaskStatus.PROCESSING.value,
            attempt_count=TaskRecord.attempt_count + 1,
            lease_token=token,
            lease_expires_at=expires_at,
            failure_code=None,
            failure_message=None,
            failure_detail=None,
            # 任务真正开始处理时重置计时起点，避免批量排队时叠加前面任务的处理时间
            started_at=claimed_at,
            updated_at=claimed_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    session.expire_all()
    return TaskLease(token=token, expires_at=expires_at)


def transition_leased_task(
    session: Session,
    task_id: str,
    token: str,
    *,
    expected: TaskStatus,
    target: TaskStatus,
    now: datetime | None = None,
    lease_for: timedelta | None = None,
) -> bool:
    if target not in ALLOWED_TRANSITIONS[expected]:
        raise ValueError(f"不允许从 {expected.value} 进入 {target.value}。")
    changed_at = now or utc_now()
    values: dict[str, object | None] = {
        "status": target.value,
        "updated_at": changed_at,
    }
    if lease_for is None:
        values.update(lease_token=None, lease_expires_at=None)
    else:
        values["lease_expires_at"] = changed_at + lease_for
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.status == expected.value,
            TaskRecord.lease_token == token,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        session.rollback()
        session.expire_all()
        return False
    session.commit()
    session.expire_all()
    return True


def renew_task_lease(
    session: Session,
    task_id: str,
    token: str,
    *,
    expected: TaskStatus,
    lease_for: timedelta,
    values: dict[str, object] | None = None,
    now: datetime | None = None,
) -> bool:
    renewed_at = now or utc_now()
    updates = {
        **(values or {}),
        "lease_expires_at": renewed_at + lease_for,
        "updated_at": renewed_at,
    }
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.status == expected.value,
            TaskRecord.lease_token == token,
        )
        .values(**updates)
    )
    if result.rowcount != 1:
        session.rollback()
        session.expire_all()
        return False
    session.commit()
    session.expire_all()
    return True


def fail_leased_task(
    session: Session,
    task_id: str,
    token: str,
    *,
    code: str,
    message: str,
    now: datetime | None = None,
) -> bool:
    failed_at = now or utc_now()
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.lease_token == token,
            TaskRecord.status.in_(
                [TaskStatus.PROCESSING.value, TaskStatus.VALIDATING.value]
            ),
        )
        .values(
            status=TaskStatus.FAILED.value,
            lease_token=None,
            lease_expires_at=None,
            failure_code=code,
            failure_message=diagnostic_summary(message),
            failure_detail=safe_diagnostic(message),
            updated_at=failed_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        session.expire_all()
        return False
    session.commit()
    session.expire_all()
    return True


def recover_expired_task_leases(
    session: Session,
    *,
    now: datetime | None = None,
) -> list[str]:
    recovered_at = now or utc_now()
    expired_ids = list(
        session.scalars(
            TaskRecord.__table__.select()
            .with_only_columns(TaskRecord.id)
            .where(
                TaskRecord.status.in_(
                    [TaskStatus.PROCESSING.value, TaskStatus.VALIDATING.value]
                ),
                TaskRecord.lease_expires_at.is_not(None),
                TaskRecord.lease_expires_at <= recovered_at,
            )
            .order_by(TaskRecord.created_at)
        )
    )
    if not expired_ids:
        return []
    session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id.in_(expired_ids),
            TaskRecord.status.in_(
                [TaskStatus.PROCESSING.value, TaskStatus.VALIDATING.value]
            ),
            TaskRecord.lease_expires_at <= recovered_at,
        )
        .values(
            status=TaskStatus.FAILED.value,
            lease_token=None,
            lease_expires_at=None,
            failure_code="worker_interrupted",
            failure_message="上次处理被意外中断，原文件仍在，可以点击重试重新处理。",
            updated_at=recovered_at,
        )
    )
    session.commit()
    session.expire_all()
    return expired_ids
