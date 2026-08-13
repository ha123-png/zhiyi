from sqlalchemy import select
from sqlalchemy.orm import Session

from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.queue import huey
from document_pipeline_api.worker import run_extraction


def enqueue_task(task_id: str) -> str:
    task = run_extraction.s(task_id)
    task.id = task_id
    huey.enqueue(task)
    return task_id


def revoke_task(task_id: str) -> None:
    huey.revoke_by_id(task_id, revoke_once=True)


def recover_missing_queued_tasks(session: Session) -> int:
    pending_ids = {task.id for task in huey.pending()}
    queued_ids = set(
        session.scalars(
            select(TaskRecord.id).where(TaskRecord.status == TaskStatus.QUEUED.value)
        )
    )
    recovered = 0
    for task_id in queued_ids:
        if task_id not in pending_ids:
            enqueue_task(task_id)
            recovered += 1
    # 自动清理垃圾消息：队列里残留的入队消息，如果对应任务在业务库已不是 queued
    # （已 completed/failed/waiting 等），说明是历史残留（worker 强杀、revoke 跳过等
    # 场景没被消费掉）。revoke_once 让 worker 下次碰到时跳过并删除，避免越积越多。
    # 只处理业务库中真实存在的任务；周期任务等未知 id 的消息不在业务库，不触碰。
    stale_candidates = pending_ids - queued_ids
    if stale_candidates:
        known_ids = set(
            session.scalars(
                select(TaskRecord.id).where(TaskRecord.id.in_(stale_candidates))
            )
        )
        for task_id in stale_candidates & known_ids:
            huey.revoke_by_id(task_id, revoke_once=True)
    return recovered
