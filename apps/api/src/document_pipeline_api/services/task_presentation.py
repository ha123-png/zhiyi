"""Typed pending actions, including records written before pending reasons existed."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from document_pipeline_api.models import ExtractionRecord, FileCurrentDataRecord
from document_pipeline_api.models.task import TaskRecord


PENDING_ACTIONS = {
    "template": "select_template",
    "input_scope": "confirm_input_scope",
    "target_table": "confirm_target_table",
}


def _legacy_limited_input(task: TaskRecord) -> bool:
    try:
        policy = json.loads(task.input_policy_json or "{}")
        plan = json.loads(task.input_plan_json or "{}")
        return (
            bool(policy)
            and not policy.get("allow_limited_input", False)
            and any(part.get("reason") == "input_budget" for part in plan.get("omitted", []))
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _pending_details(task: TaskRecord, *, has_result: bool, current_version: int | None) -> dict:
    reason = None
    if task.status == "waiting_for_template":
        # Existing evidence wins over an old/stale reason: never offer a second
        # extraction/template selection over an already materialized result.
        if has_result or current_version is not None:
            reason = "target_table"
        elif task.pending_reason == "input_scope":
            reason = "input_scope" if task.input_policy_json else "unknown"
        elif task.pending_reason in {None, "template"}:
            reason = "input_scope" if _legacy_limited_input(task) else "template"
        else:
            reason = "unknown"
    return {
        "has_result": has_result,
        "pending_reason": reason,
        "pending_action": PENDING_ACTIONS.get(reason),
        "pending_review_version": current_version if reason == "target_table" else None,
    }


def pending_details_for_tasks(session: Session, tasks: list[TaskRecord]) -> dict[str, dict]:
    """Resolve a page with two bounded queries, never a query for each row."""
    task_ids = [task.id for task in tasks]
    waiting_ids = [task.id for task in tasks if task.status == "waiting_for_template"]
    result_ids = set(session.scalars(
        select(ExtractionRecord.task_id).where(ExtractionRecord.task_id.in_(task_ids))
    )) if task_ids else set()
    current_versions: dict[str, int] = {}
    if waiting_ids:
        current_versions = dict(session.execute(
            select(FileCurrentDataRecord.task_id, FileCurrentDataRecord.current_version)
            .where(FileCurrentDataRecord.task_id.in_(waiting_ids))
        ).all())
    return {
        task.id: _pending_details(
            task,
            has_result=task.id in result_ids,
            current_version=current_versions.get(task.id),
        )
        for task in tasks
    }


def pending_details_for_task(task: TaskRecord, session: Session | None = None) -> dict:
    session = session or object_session(task)
    if session is not None:
        return pending_details_for_tasks(session, [task])[task.id]
    # Detached legacy records cannot prove absence of current data. Do not turn
    # an unknown old task into a destructive template-selection action.
    if task.status == "waiting_for_template":
        return {"has_result": False, "pending_reason": "unknown", "pending_action": None, "pending_review_version": None}
    return _pending_details(task, has_result=False, current_version=None)
