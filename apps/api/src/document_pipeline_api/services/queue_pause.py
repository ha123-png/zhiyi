"""队列级暂停/恢复（原型语义：暂停 = 冻结整个队列现状）。

暂停时：设置暂停标志、中断当前处理中的任务，并把所有排队/待入队任务也标记为
「已暂停」（前端据此显示冻结状态并提供恢复入口），同时撤销 huey 队列中所有
待执行任务，使 Worker 不再启动新任务；恢复时：清除标志、把所有暂停任务放回
队列并重新入队，整个队列继续处理。Worker 在开始处理前也会检查暂停标志作为
兜底（防撤销竞态）。
"""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models.task import TaskRecord, utc_now
from document_pipeline_api.services.system_settings import (
    get_bool_setting,
    set_bool_setting,
)

QUEUE_PAUSED_KEY = "queue_paused"


def is_queue_paused(session: Session) -> bool:
    return get_bool_setting(session, QUEUE_PAUSED_KEY, False)


def clear_pause_if_queue_empty(session: Session) -> None:
    """队列清空后自动解除暂停。

    暂停 = 冻结「队列现状」；当队列里不再有任何待处理/处理中/已暂停任务时，
    暂停已无意义。若不解除，用户「暂停 → 删除全部任务 → 再上传」时，新上传的
    文件仍会被冻结成已暂停，无法直接处理。清空后队列回到闲置，新上传默认立即执行。
    """
    remaining = session.scalar(
        select(func.count())
        .select_from(TaskRecord)
        .where(
            TaskRecord.status.in_(
                [
                    TaskStatus.CREATED.value,
                    TaskStatus.QUEUED.value,
                    TaskStatus.PROCESSING.value,
                    TaskStatus.VALIDATING.value,
                    TaskStatus.PAUSED.value,
                ]
            )
        )
    )
    if remaining == 0 and is_queue_paused(session):
        set_bool_setting(session, QUEUE_PAUSED_KEY, False)
        session.commit()


def freeze_new_task_if_paused(session: Session, task_id: str) -> bool:
    """队列冻结期间新上传/重试的任务直接进入暂停态，不能短暂漏进 Worker。"""
    if not is_queue_paused(session):
        return False
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.status.in_([TaskStatus.CREATED.value, TaskStatus.QUEUED.value]),
        )
        .values(
            status=TaskStatus.PAUSED.value,
            lease_token=None,
            lease_expires_at=None,
            updated_at=utc_now(),
        )
    )
    session.commit()
    return result.rowcount == 1


def queue_pause(session: Session, settings: Settings) -> None:
    """暂停整个队列：中断当前处理中的任务，冻结全部排队任务，Worker 不再启动新任务。"""
    set_bool_setting(session, QUEUE_PAUSED_KEY, True)
    # 中断当前正在处理的任务（保留租约的任务），并把排队/待入队任务一并标记为暂停：
    # 冻结必须对用户可见（前端靠「已暂停」显示恢复入口），否则全在排队时点暂停像没反应。
    # 待选模板/待确认等等待用户操作的任务不受影响，保持原状态。
    session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.status.in_(
                [
                    TaskStatus.PROCESSING.value,
                    TaskStatus.VALIDATING.value,
                    TaskStatus.QUEUED.value,
                    TaskStatus.CREATED.value,
                ]
            )
        )
        .values(
            status=TaskStatus.PAUSED.value,
            lease_token=None,
            lease_expires_at=None,
            updated_at=utc_now(),
        )
    )
    session.commit()
    # 撤销 huey 队列中所有待执行任务，冻结排队中的文件（已消费的任务由 Worker 侧兜底检查拦截）
    if settings.queue_enabled:
        from document_pipeline_api.queue import huey

        for task in huey.pending():
            try:
                huey.revoke_by_id(task.id, revoke_once=True)
            except Exception:
                continue


def queue_resume(session: Session, settings: Settings) -> None:
    """恢复整个队列：把所有暂停任务放回队列，重新入队，继续处理。"""
    set_bool_setting(session, QUEUE_PAUSED_KEY, False)
    session.execute(
        update(TaskRecord)
        .where(TaskRecord.status == TaskStatus.PAUSED.value)
        .values(
            status=TaskStatus.QUEUED.value,
            lease_token=None,
            lease_expires_at=None,
            # 恢复后计时从恢复时刻重新开始
            started_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    session.commit()
    if settings.queue_enabled:
        from document_pipeline_api.services.queueing import enqueue_task

        queued_ids = session.scalars(
            select(TaskRecord.id).where(
                TaskRecord.status == TaskStatus.QUEUED.value
            )
        )
        for task_id in queued_ids:
            enqueue_task(task_id)
