import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, delete, func, insert, literal, or_, select, update
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.domain.tasks import (
    InvalidTaskTransition,
    TaskState,
    TaskStatus,
)
from document_pipeline_api.models import (
    ConfirmedDocumentRecord,
    DataRowRecord,
    ExtractionRecord,
    FileCurrentDataRecord,
    ReviewRevisionRecord,
)
from document_pipeline_api.models.task import TaskRecord, utc_now
from document_pipeline_api.models.system_setting import PENDING_DUPLICATE_PREFIX, SystemSettingRecord
from document_pipeline_api.services.file_formats import (
    CONVERTIBLE_IMAGE_TYPES,
    TEXT_CONTENT_TYPES,
    UnsupportedImageError,
    UnsupportedTextFileError,
)
from document_pipeline_api.services.pdf_rendering import (
    UnsupportedPdfError,
)
from document_pipeline_api.services.model_runtime import model_snapshot_values
from document_pipeline_api.services.processing_input import prepare_input, policy_from_settings
from document_pipeline_api.services.input_scope import InputBudgetError
from document_pipeline_api.schemas.input_scope import InputPolicy
from document_pipeline_api.schemas.tasks import TaskBatchRetryResult, TaskGroup, TaskRetrySkipped
from document_pipeline_api.services.task_presentation import pending_details_for_task
from document_pipeline_api.services.queue_pause import clear_pause_if_queue_empty
from document_pipeline_api.services.read_snapshot import begin_write_snapshot
from document_pipeline_api.services.system_settings import get_bool_setting, upload_limit_bytes
from document_pipeline_api.services.templates import get_active_template
from document_pipeline_api.storage_paths import task_storage_name
from document_pipeline_api.services.file_operation_lock import guard_file_operation
from document_pipeline_api.services.upload_recovery import (
    begin_upload_intent,
    finish_upload_intent,
)


ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    **CONVERTIBLE_IMAGE_TYPES,
    **TEXT_CONTENT_TYPES,
}
ACTIVE_CAPACITY_STATUSES = {
    TaskStatus.QUEUED.value,
    TaskStatus.PROCESSING.value,
    TaskStatus.VALIDATING.value,
}
RETRYABLE_TASK_STATUSES = {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
HISTORY_CLEARABLE_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.NEEDS_REVIEW.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


class UploadFingerprintMismatch(HTTPException):
    def __init__(self):
        super().__init__(409, "所选文件内容与原来提交的文件不同。请重新选择原文件。")


async def create_task_from_upload(
    session: Session,
    settings: Settings,
    upload: UploadFile,
    template_mode: str,
    template_id: str | None = None,
    *,
    target_table_id: str | None = None,
    source_file: dict | None = None,
    duplicate_policy: Literal["reject", "allow"] = "reject",
    expected_sha256: str | None = None,
    pending_duplicate_id: str | None = None,
) -> TaskRecord:
    # All ingress adapters share the same semantic modes. The worker's legacy
    # fallback treats unknown/no-template modes as smart; admitting those here
    # would create distinct duplicate keys for the same actual extraction.
    if template_mode not in {"smart", "manual", "invoice", "delivery"}:
        await upload.close()
        raise HTTPException(422, "template_mode 只能是 smart、manual、invoice 或 delivery。")
    if template_mode == "manual" and not template_id:
        await upload.close()
        raise HTTPException(422, "手动提取必须选择模板。")
    if duplicate_policy not in {"reject", "allow"}:
        await upload.close()
        raise HTTPException(422, "duplicate_policy 只能是 reject 或 allow。")
    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="当前仅支持 PDF、JPG、PNG 文件；其他格式请先在设置中开启对应转换。",
        )
    if upload.content_type in CONVERTIBLE_IMAGE_TYPES and not get_bool_setting(
        session, "image_convert", True
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="图片自动格式转换已关闭，当前仅支持 JPG、PNG 和 PDF 文件。",
        )
    if upload.content_type in TEXT_CONTENT_TYPES and not get_bool_setting(
        session, "office_convert", True
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Office 文档自动转换已关闭，当前仅支持 JPG、PNG 和 PDF 文件。",
        )
    if not upload.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空。")
    if len(upload.filename) > settings.max_filename_chars:
        raise HTTPException(
            status_code=422,
            detail=f"文件名不能超过 {settings.max_filename_chars} 个字符。",
        )

    task_id = str(uuid4())
    suffix = ALLOWED_CONTENT_TYPES[upload.content_type]
    temporary_path = settings.storage_dir / f".{task_id}.uploading"
    final_path = settings.storage_dir / f"{task_id}{suffix}"
    digest = hashlib.sha256()
    size = 0
    upload_intent = None
    remove_upload_intent = False
    commit_attempted = False
    committed = False

    try:
        # Publish a durable per-ID marker before creating either the staging
        # or final file. A startup/worker recovery pass can then distinguish
        # an interrupted upload from a task-owned original.
        upload_intent = begin_upload_intent(settings.storage_dir.parent, task_id)
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > upload_limit_bytes(session, settings.max_upload_bytes):
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="文件超过你设置的单文件上传上限，尚未保存。请在设置中调整上限后重新上传。",
                    )
                digest.update(chunk)
                target.write(chunk)

        sha256 = digest.hexdigest()
        if expected_sha256 is not None and sha256 != expected_sha256:
            raise UploadFingerprintMismatch()
        policy = policy_from_settings(session)
        try:
            prepared, page_count = prepare_input(temporary_path, upload.content_type, settings, policy)
        except (UnsupportedTextFileError, UnsupportedImageError, UnsupportedPdfError, InputBudgetError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=422, detail="文件损坏或无法读取，请确认文件有效且未加密。") from error
        processing_units = max(1, sum(unit.text is None for unit in prepared.units))
        duplicate_id = session.scalar(
            select(TaskRecord.id)
            .where(TaskRecord.sha256 == sha256)
            .order_by(TaskRecord.created_at.desc())
            .limit(1)
        )
        temporary_path.replace(final_path)
        selected_id = template_id or (
            f"builtin-{template_mode}" if template_mode in {"invoice", "delivery"} else None
        )
        selected_template = get_active_template(session, selected_id) if selected_id else None
        _insert_task_if_capacity_available(
            session,
            settings,
            task_id=task_id,
            filename=upload.filename,
            content_type=upload.content_type,
            size_bytes=size,
            page_count=page_count,
            processing_units=processing_units,
            sha256=sha256,
            storage_path=task_storage_name(task_id, upload.content_type),
            template_mode="manual" if selected_template else template_mode,
            template_id=selected_template.id if selected_template else None,
            template_version=(
                selected_template.version if selected_template else None
            ),
            duplicate_of_task_id=duplicate_id,
            target_table_id=target_table_id,
            duplicate_policy=duplicate_policy,
        )
        task = session.get(TaskRecord, task_id)
        task.source_file_json = json.dumps(source_file, ensure_ascii=False) if source_file else None
        task.input_policy_json = policy.model_dump_json()
        task.input_plan_json = prepared.scope.model_dump_json()
        if selected_template is None and template_mode == "smart":
            from document_pipeline_api.services.autonomous_extraction import frozen_pool
            frozen_pool(session, task)
        if selected_template is not None:
            from document_pipeline_api.services.local_exports import snapshot_task_export
            snapshot_task_export(session, task, selected_template)
        if pending_duplicate_id:
            pending = session.get(SystemSettingRecord, PENDING_DUPLICATE_PREFIX + pending_duplicate_id)
            if duplicate_policy != "allow" or expected_sha256 is None:
                raise HTTPException(422, "确认重复文件时需要核对原件。")
            if pending is None:
                raise HTTPException(409, "这条待确认记录已经处理，请刷新任务列表。")
            saved = json.loads(pending.value)
            if saved.get("sha256") != sha256 or saved.get("selection") != (selected_id or "smart"):
                raise HTTPException(409, "文件内容或模板与待确认记录不一致。")
            session.delete(pending)
        commit_attempted = True
        session.commit()
        committed = True
        remove_upload_intent = True
        task = session.get(TaskRecord, task_id)
        if task is None:
            raise RuntimeError("已入队任务没有成功写入数据库。")
        return task
    except OSError as error:
        session.rollback()
        if commit_attempted:
            # A failed COMMIT can be ambiguous. Keep the marker and let the
            # recovery pass decide from a fresh database read whether the row
            # owns this original.
            remove_upload_intent = committed
        else:
            remove_upload_intent = _discard_upload_paths(temporary_path, final_path)
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="无法保存上传文件，请检查磁盘空间和文件夹权限后重试。",
        ) from error
    except Exception:
        session.rollback()
        if commit_attempted:
            remove_upload_intent = committed
        else:
            remove_upload_intent = _discard_upload_paths(temporary_path, final_path)
        raise
    finally:
        try:
            await upload.close()
        finally:
            if upload_intent is not None:
                finish_upload_intent(
                    upload_intent,
                    remove_marker=remove_upload_intent,
                )


def _discard_upload_paths(*paths) -> bool:
    removed = True
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            removed = False
    for path in paths:
        try:
            if path.exists():
                removed = False
        except OSError:
            removed = False
    return removed


def _insert_task_if_capacity_available(
    session: Session,
    settings: Settings,
    *,
    task_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    page_count: int,
    processing_units: int,
    sha256: str,
    storage_path: str,
    template_mode: str,
    template_id: str | None,
    template_version: int | None,
    duplicate_of_task_id: str | None,
    target_table_id: str | None = None,
    duplicate_policy: Literal["reject", "allow"] = "reject",
) -> None:
    intent = {"mode": template_mode, "template_id": template_id,
              "template_version": template_version, "target_table_id": target_table_id}
    intent_json = json.dumps(intent, sort_keys=True, separators=(",", ":"))
    # Old records have no immutable snapshot. Smart stays smart after matching;
    # for manual legacy records only their retained template can be compared.
    legacy_intent = [TaskRecord.template_mode == template_mode,
                     TaskRecord.target_table_id == target_table_id]
    if template_mode != "smart":
        legacy_intent += [TaskRecord.template_id == template_id,
                          TaskRecord.template_version == template_version]
    duplicates = select(TaskRecord.id).where(
        TaskRecord.sha256 == sha256,
        # Cancelling a needs-review task keeps its extracted facts. Terminal
        # state alone therefore does not mean it is safe to create another set.
        or_(TaskRecord.status.not_in([TaskStatus.FAILED.value, TaskStatus.CANCELLED.value]),
            select(ExtractionRecord.task_id).where(ExtractionRecord.task_id == TaskRecord.id)
            .correlate(TaskRecord).exists(),
            select(FileCurrentDataRecord.task_id).where(FileCurrentDataRecord.task_id == TaskRecord.id)
            .correlate(TaskRecord).exists()),
        or_(TaskRecord.upload_intent_json == intent_json,
            and_(TaskRecord.upload_intent_json.is_(None), *legacy_intent)),
    )
    capacity_conditions = _capacity_conditions(
        settings,
        additional_bytes=size_bytes,
        additional_pages=processing_units,
    )
    now = utc_now()
    columns = [
        "id",
        "filename",
        "content_type",
        "size_bytes",
        "page_count",
        "processing_units",
        "sha256",
        "upload_intent_json",
        "storage_path",
        "template_mode",
        "template_id",
        "template_version",
        "candidate_templates_json",
        "model_config_version",
        "model_profile_id",
        "model_profile_version",
        "model_provider",
        "model_base_url",
        "model_name",
        "model_reasoning_effort",
        "model_timeout_seconds",
        "model_context_length",
        "model_temperature",
        "model_secret_ref",
        "status",
        "attempt_count",
        "lease_token",
        "lease_expires_at",
        "failure_code",
        "failure_message",
        "duplicate_of_task_id",
        "target_table_id",
        "started_at",
        "created_at",
        "updated_at",
    ]
    model_snapshot = model_snapshot_values(settings, session)
    values = select(
        literal(task_id),
        literal(filename),
        literal(content_type),
        literal(size_bytes),
        literal(page_count),
        literal(processing_units),
        literal(sha256),
        literal(intent_json),
        literal(storage_path),
        literal(template_mode),
        literal(template_id),
        literal(template_version),
        literal("[]"),
        literal(model_snapshot["model_config_version"]),
        literal(model_snapshot["model_profile_id"]),
        literal(model_snapshot["model_profile_version"]),
        literal(model_snapshot["model_provider"]),
        literal(model_snapshot["model_base_url"]),
        literal(model_snapshot["model_name"]),
        literal(model_snapshot["model_reasoning_effort"]),
        literal(model_snapshot["model_timeout_seconds"]),
        literal(model_snapshot["model_context_length"]),
        literal(model_snapshot["model_temperature"]),
        literal(model_snapshot["model_secret_ref"]),
        literal(TaskStatus.QUEUED.value),
        literal(0),
        literal(None),
        literal(None),
        literal(None),
        literal(None),
        literal(duplicate_of_task_id),
        literal(target_table_id),
        literal(now),
        literal(now),
        literal(now),
    ).where(*capacity_conditions)
    if duplicate_policy == "reject":
        # The predicate and insert share the SQLite write statement/lock. A
        # preceding SELECT alone permits concurrent uploads to both enqueue.
        values = values.where(~duplicates.exists())
    result = session.execute(insert(TaskRecord).from_select(columns, values))
    if result.rowcount != 1:
        if duplicate_policy == "reject":
            duplicate_id = session.scalar(duplicates.order_by(TaskRecord.created_at.desc()).limit(1))
            if duplicate_id is not None:
                duplicate = session.get(TaskRecord, duplicate_id)
                raise HTTPException(409, {
                    "code": "duplicate_upload",
                    "message": "这份文件已按相同方式导入。再次提取会新建任务，可能重复生成数据并产生模型费用。",
                    "duplicate_task_id": duplicate_id,
                    "duplicate_status": duplicate.status,
                    "duplicate_sha256": sha256,
                })
        raise _capacity_error(settings)


def _capacity_conditions(
    settings: Settings,
    *,
    additional_bytes: int,
    additional_pages: int,
) -> tuple[object, object, object]:
    active_filter = TaskRecord.status.in_(ACTIVE_CAPACITY_STATUSES)
    active_count = (
        select(func.count(TaskRecord.id)).where(active_filter).scalar_subquery()
    )
    active_bytes = (
        select(func.coalesce(func.sum(TaskRecord.size_bytes), 0))
        .where(active_filter)
        .scalar_subquery()
    )
    active_pages = (
        select(func.coalesce(func.sum(func.coalesce(TaskRecord.processing_units, TaskRecord.page_count)), 0))
        .where(active_filter)
        .scalar_subquery()
    )
    return (
        active_count < settings.max_active_tasks,
        active_bytes + additional_bytes <= settings.max_active_bytes,
        active_pages + additional_pages <= settings.max_active_pages,
    )


def _capacity_error(settings: Settings) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            "当前待处理文件容量不足，请等待部分任务完成后再试。"
            f"当前上限为 {settings.max_active_tasks} 个文件、"
            f"{settings.max_active_bytes} 字节、{settings.max_active_pages} 个输入容量单位。"
        ),
        headers={"Retry-After": "5"},
    )


# 兼容旧 active_only 调用的范围；新任务监控用互斥 group 与 terminal_since 展示窗口。
ACTIVE_TASK_STATUSES = {
    TaskStatus.CREATED.value,
    TaskStatus.QUEUED.value,
    TaskStatus.PROCESSING.value,
    TaskStatus.VALIDATING.value,
    TaskStatus.WAITING_FOR_TEMPLATE.value,
    TaskStatus.NEEDS_REVIEW.value,
    TaskStatus.PAUSED.value,
}


def task_group_expression(group: TaskGroup):
    """Mutually exclusive list groups; legacy active_only keeps its old scope."""
    from sqlalchemy import true
    export_status = func.json_extract(TaskRecord.export_state_json, "$.status")
    export_attention = func.coalesce(
        and_(TaskRecord.status == "completed", export_status.in_(["failed", "needs_rebind"])), False,
    )
    export_running = func.coalesce(
        and_(TaskRecord.status == "completed", export_status.in_(["awaiting_confirmation", "pending", "exporting"])), False,
    )
    return {
        "all": true(),
        "active": or_(TaskRecord.status.in_(["created", "queued", "processing", "validating", "paused"]), export_running),
        "pending": or_(TaskRecord.status.in_(["waiting_for_template", "needs_review"]), export_attention),
        "completed": and_(TaskRecord.status == "completed", ~export_attention, ~export_running),
        "failed": TaskRecord.status == "failed",
        "cancelled": TaskRecord.status == "cancelled",
    }[group]


def task_name_matches(search: str):
    pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return (
        TaskRecord.filename.ilike(pattern, escape="\\")
        | func.json_extract(TaskRecord.file_name_json, "$.confirmed_filename").ilike(pattern, escape="\\")
        | func.json_extract(TaskRecord.file_name_json, "$.suggested_filename").ilike(pattern, escape="\\")
        | func.json_extract(TaskRecord.export_state_json, "$.confirmed_name").ilike(pattern, escape="\\")
    )


def normalize_task_since(value: datetime) -> datetime:
    """Task timestamps are stored as UTC; timezone-less API values mean UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def task_terminal_window_expression(since: datetime):
    """Hide only quiet terminal records; unfinished/actionable exports stay visible.

    Use the displayed update time: completion can precede a later export or review,
    and cancellation has no separate completion timestamp. This is display-only.
    """
    quiet_terminal = or_(task_group_expression("completed"), TaskRecord.status == "cancelled")
    return or_(~quiet_terminal, TaskRecord.updated_at >= normalize_task_since(since))


def list_tasks(
    session: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    active_only: bool = False,
    export_pending: bool = False,
    status: str | None = None,
    search: str | None = None,
    template_id: str | None = None,
    since: datetime | None = None,
    terminal_since: datetime | None = None,
    task_ids: list[str] | None = None,
    group: TaskGroup | None = None,
) -> list[TaskRecord]:
    """任务列表。

    limit/offset 分页；active_only 只拉还在动的任务（全局任务条/状态监控轮询）；
    status/search/template_id/since 供历史页服务端筛选；terminal_since 只收起安静终态，
    与所有筛选取交集后再分页，避免把整表拉入内存或只过滤当前一页。
    """
    # created_at 可能同一毫秒写入多条；增加主键作为稳定次序，避免 offset 翻页时
    # 同时间任务在两页之间重复或遗漏。
    statement = select(TaskRecord).order_by(
        TaskRecord.created_at.desc(),
        TaskRecord.id.desc(),
    )
    if task_ids is not None:
        # An explicitly empty scope must stay empty, rather than become history.
        statement = statement.where(TaskRecord.id.in_(list(dict.fromkeys(task_ids))))
    if active_only:
        statement = statement.where(
            TaskRecord.status.in_(ACTIVE_TASK_STATUSES)
        )
    if group is not None:
        statement = statement.where(task_group_expression(group))
    from document_pipeline_api.services.task_exports import archive_pending_expression, pending_export_expression
    if export_pending:
        statement = statement.where(pending_export_expression())
    if status is not None:
        statement = statement.where(TaskRecord.status.in_(status.split(",")))
        if not export_pending and "completed" in status.split(","):
            statement = statement.where(~func.coalesce(archive_pending_expression(), False))
    if search:
        statement = statement.where(
            task_name_matches(search)
        )
    if template_id:
        statement = statement.where(TaskRecord.template_id == template_id)
    if since is not None:
        statement = statement.where(TaskRecord.updated_at >= normalize_task_since(since))
    if terminal_since is not None:
        statement = statement.where(task_terminal_window_expression(terminal_since))
    if offset:
        statement = statement.offset(offset)
    statement = statement.limit(limit)
    return list(session.scalars(statement))


def cancel_task(session: Session, task_id: str) -> TaskRecord:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")

    try:
        TaskState(status=TaskStatus(task.status)).transition_to(TaskStatus.CANCELLED)
    except InvalidTaskTransition as error:
        raise HTTPException(status_code=409, detail="当前状态不能取消。") from error

    # 围栏更新：只在任务仍处于读取时的状态才取消。若 Worker 恰好在"读取→写入"之间
    # 完成了任务（终态已落库），本次 UPDATE 命中 0 行，不会把已完成覆盖成已取消。
    result = session.execute(
        update(TaskRecord)
        .where(TaskRecord.id == task_id, TaskRecord.status == task.status)
        .values(
            status=TaskStatus.CANCELLED.value,
            lease_token=None,
            lease_expires_at=None,
            updated_at=utc_now(),
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试。")
    session.commit()
    cancelled = session.get(TaskRecord, task_id)
    if cancelled is None:
        raise RuntimeError("取消后的任务不存在。")
    return cancelled


def retry_task(
    session: Session,
    settings: Settings,
    task_id: str,
    *, use_current_settings: bool = False, expected_status: str | None = None,
) -> TaskRecord:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    previous_status = task.status
    previous_updated_at = task.updated_at
    if expected_status is not None and previous_status != expected_status:
        raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试。")
    if previous_status not in RETRYABLE_TASK_STATUSES:
        raise HTTPException(status_code=409, detail="只有失败或已取消的任务可以重试。")

    target = TaskStatus.QUEUED
    if previous_status == TaskStatus.CANCELLED.value:
        existing_result = session.scalar(
            select(ExtractionRecord.task_id).where(ExtractionRecord.task_id == task_id)
        )
        if existing_result is not None or session.get(FileCurrentDataRecord, task_id) is not None:
            target = (TaskStatus.WAITING_FOR_TEMPLATE if task.pending_reason == "target_table"
                      else TaskStatus.NEEDS_REVIEW)
    next_state = TaskState(status=TaskStatus(previous_status)).transition_to(target)

    policy_values = {}
    capacity_conditions = ()
    if use_current_settings and target == TaskStatus.QUEUED:
        from document_pipeline_api.storage_paths import resolve_task_storage_path
        policy = policy_from_settings(session)
        try:
            prepared, _ = prepare_input(resolve_task_storage_path(settings.storage_dir, task), task.content_type, settings, policy)
        except (UnsupportedTextFileError, UnsupportedImageError, UnsupportedPdfError, InputBudgetError, OSError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        policy_values = {"input_policy_json": policy.model_dump_json(), "input_plan_json": prepared.scope.model_dump_json(),
                         "processing_units": max(1, sum(unit.text is None for unit in prepared.units))}
    now = utc_now()
    if target == TaskStatus.QUEUED:
        capacity_conditions = _capacity_conditions(
            settings,
            additional_bytes=task.size_bytes,
            additional_pages=policy_values.get("processing_units", task.processing_units or task.page_count),
        )
        policy_values.update(started_at=now, pending_reason=None)
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.status == previous_status,
            TaskRecord.updated_at == previous_updated_at,
            *capacity_conditions,
        )
        .values(
            **policy_values,
            status=next_state.status.value,
            lease_token=None,
            lease_expires_at=None,
            failure_code=None,
            failure_message=None,
            failure_detail=None,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        current = session.get(TaskRecord, task_id)
        if (target != TaskStatus.QUEUED or current is None
                or current.status != previous_status or current.updated_at != previous_updated_at):
            raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试。")
        raise _capacity_error(settings)
    session.commit()
    updated_task = session.get(TaskRecord, task_id)
    if updated_task is None:
        raise RuntimeError("重试任务不存在。")
    return updated_task


def retry_tasks(
    session: Session,
    settings: Settings,
    task_ids: list[str],
) -> TaskBatchRetryResult:
    """Keep partial successes and a truthful reason for each skipped request."""
    result = TaskBatchRetryResult()
    seen: set[str] = set()
    messages = {
        "duplicate": "同一任务在本次请求中重复，未重复提交。",
        "not_found": "任务已不存在。",
        "not_failed": "只有失败或已取消的任务可以重试。",
        "state_changed": "任务状态已变化，请刷新后重试。",
        "capacity": "当前待处理容量不足，请稍后重试。",
        "rejected": "本次重试未获接受，请单独重试以查看详情。",
    }
    for task_id in task_ids:
        reason = None
        if task_id in seen:
            reason = "duplicate"
        else:
            seen.add(task_id)
            # Read the current state after earlier attempts in the same batch.
            session.expire_all()
            task = session.get(TaskRecord, task_id)
            if task is None:
                reason = "not_found"
            elif task.status not in RETRYABLE_TASK_STATUSES:
                reason = "not_failed"
        if reason is not None:
            result.skipped_tasks.append(TaskRetrySkipped(task_id=task_id, reason=reason, message=messages[reason]))
            continue
        try:
            retry_task(session, settings, task_id, expected_status=task.status)
        except HTTPException as error:
            session.rollback()
            reason = {404: "not_found", 409: "state_changed", 429: "capacity"}.get(error.status_code, "rejected")
            result.skipped_tasks.append(TaskRetrySkipped(task_id=task_id, reason=reason, message=messages[reason]))
        else:
            result.retried.append(task_id)
    result.skipped = len(result.skipped_tasks)
    return result


def select_task_template(
    session: Session,
    settings: Settings,
    task_id: str,
    template_id: str,
) -> TaskRecord:
    begin_write_snapshot(session)
    session.expire_all()
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if task.status != TaskStatus.WAITING_FOR_TEMPLATE.value:
        raise HTTPException(status_code=409, detail="这个任务当前不需要选择模板。")
    if session.get(ExtractionRecord, task_id) is not None or session.get(FileCurrentDataRecord, task_id) is not None:
        raise HTTPException(status_code=409, detail="这份文件已有提取结果，请确认已有数据；不能重新选择模板覆盖初始提取证据。")
    if pending_details_for_task(task, session)["pending_action"] != "select_template":
        raise HTTPException(status_code=409, detail="这个任务当前需要其他确认，请刷新任务状态。")

    candidates = json.loads(task.candidate_templates_json)
    candidate = next(
        (item for item in candidates if item["id"] == template_id),
        None,
    )
    # 候选只是智能匹配的推荐顺序，不是权限白名单。用户必须能够纠正模型，
    # 从全部当前有效模板中选择；否则一次分类遗漏会把任务永久困在待选状态。
    template = get_active_template(session, template_id)
    version = candidate["version"] if candidate is not None else template.version
    from document_pipeline_api.services.local_exports import build_export_snapshot
    from document_pipeline_api.services.templates import get_template_version
    export_snapshot = task.export_state_json
    if task.template_id != template_id or export_snapshot is None:
        export_snapshot = build_export_snapshot(session, get_template_version(session, template_id, version))
    next_status = TaskState(status=TaskStatus(task.status)).transition_to(
        TaskStatus.QUEUED
    ).status.value
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.status == TaskStatus.WAITING_FOR_TEMPLATE.value,
            *_capacity_conditions(
                settings,
                additional_bytes=task.size_bytes,
                additional_pages=task.processing_units or task.page_count,
            ),
        )
        .values(
            template_id=template_id,
            template_version=version,
            export_state_json=export_snapshot,
            candidate_templates_json="[]",
            pending_reason=None,
            status=next_status,
            # 选择模板重新入队：本次处理尝试重新开始计时
            started_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise _capacity_error(settings)
    session.commit()
    updated_task = session.get(TaskRecord, task_id)
    if updated_task is None:
        raise RuntimeError("模板选择后的任务不存在。")
    return updated_task


def accept_task_scope(session: Session, settings: Settings, task_id: str) -> TaskRecord:
    begin_write_snapshot(session)
    session.expire_all()
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if pending_details_for_task(task, session)["pending_action"] != "confirm_input_scope" or not task.input_policy_json:
        raise HTTPException(status_code=409, detail="这个任务当前不需要确认读取范围。")
    policy = InputPolicy.model_validate_json(task.input_policy_json)
    policy.accept_partial = True
    policy.allow_limited_input = True
    result = session.execute(update(TaskRecord).where(
        TaskRecord.id == task_id,
        TaskRecord.status == TaskStatus.WAITING_FOR_TEMPLATE.value,
        TaskRecord.pending_reason == task.pending_reason,
        *_capacity_conditions(settings, additional_bytes=task.size_bytes, additional_pages=task.processing_units or task.page_count),
    ).values(input_policy_json=policy.model_dump_json(), pending_reason=None,
             status=TaskStatus.QUEUED.value, started_at=utc_now(), updated_at=utc_now()))
    if result.rowcount != 1:
        session.rollback()
        raise _capacity_error(settings)
    session.commit()
    return session.get(TaskRecord, task_id)


def pause_task(session: Session, task_id: str) -> TaskRecord:
    """暂停排队中或处理中的任务。

    视觉上立即生效；正在进行的模型调用无法硬性中断，
    但其后的状态迁移因租约/状态不匹配而安全丢弃结果（不落库）。
    """
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    try:
        TaskState(status=TaskStatus(task.status)).transition_to(TaskStatus.PAUSED)
    except InvalidTaskTransition as error:
        raise HTTPException(status_code=409, detail="当前状态不能暂停。") from error

    result = session.execute(
        update(TaskRecord)
        .where(TaskRecord.id == task_id, TaskRecord.status == task.status)
        .values(
            status=TaskStatus.PAUSED.value,
            lease_token=None,
            lease_expires_at=None,
            updated_at=utc_now(),
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试。")
    session.commit()
    paused = session.get(TaskRecord, task_id)
    if paused is None:
        raise RuntimeError("暂停后的任务不存在。")
    return paused


def resume_task(
    session: Session,
    settings: Settings,
    task_id: str,
) -> TaskRecord:
    """把已暂停任务重新放回队列；恢复时重新检查容量。"""
    from document_pipeline_api.services.queue_pause import is_queue_paused

    if is_queue_paused(session):
        raise HTTPException(status_code=409, detail="队列仍处于暂停状态，请先启动整个队列。")
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if task.status != TaskStatus.PAUSED.value:
        raise HTTPException(status_code=409, detail="只有已暂停任务可以恢复。")
    try:
        TaskState(status=TaskStatus(task.status)).transition_to(TaskStatus.QUEUED)
    except InvalidTaskTransition as error:
        raise HTTPException(status_code=409, detail="只有已暂停任务可以恢复。") from error

    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.status == TaskStatus.PAUSED.value,
            *_capacity_conditions(
                settings,
                additional_bytes=task.size_bytes,
                additional_pages=task.processing_units or task.page_count,
            ),
        )
        .values(
            status=TaskStatus.QUEUED.value,
            lease_token=None,
            lease_expires_at=None,
            # 恢复继续处理：计时从恢复时刻重新开始
            started_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    if result.rowcount != 1:
        session.rollback()
        current = session.get(TaskRecord, task_id)
        if current is None or current.status != TaskStatus.PAUSED.value:
            raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试。")
        raise _capacity_error(settings)
    session.commit()
    resumed = session.get(TaskRecord, task_id)
    if resumed is None:
        raise RuntimeError("恢复后的任务不存在。")
    return resumed


DELETABLE_TASK_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.NEEDS_REVIEW.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    # 待选模板：尚未产生任何数据，可直接删除（无需确认式确认流程由前端决定）
    TaskStatus.WAITING_FOR_TEMPLATE.value,
    # 暂停：处理尚未完成，防止上传错文件时删除（不产生数据）
    TaskStatus.PAUSED.value,
}


def _task_storage_paths(
    settings: Settings,
    tasks: list[TaskRecord],
) -> list[Path]:
    """解析任务原文件路径；引用无效的任务不阻塞删除。"""
    from document_pipeline_api.storage_paths import resolve_task_storage_path, ensure_managed_path

    paths: list[Path] = []
    for task in tasks:
        try:
            path = resolve_task_storage_path(settings.storage_dir, task)
            paths.append(ensure_managed_path(settings.storage_dir, path))
        except ValueError:
            continue
    return paths


def _delete_task_records(session: Session, task_ids: list[str]) -> None:
    if not task_ids:
        return
    from document_pipeline_api.models import DataRowRevisionRecord, FileCurrentDataRecord, FileRevisionRecord
    session.execute(delete(SystemSettingRecord).where(
        SystemSettingRecord.key.startswith(PENDING_DUPLICATE_PREFIX),
        func.json_valid(SystemSettingRecord.value),
        func.json_extract(SystemSettingRecord.value, "$.existingTaskId").in_(task_ids),
    ))
    session.execute(delete(DataRowRevisionRecord).where(DataRowRevisionRecord.task_id.in_(task_ids)))
    session.execute(delete(FileRevisionRecord).where(FileRevisionRecord.task_id.in_(task_ids)))
    session.execute(delete(DataRowRecord).where(DataRowRecord.task_id.in_(task_ids)))
    session.execute(delete(FileCurrentDataRecord).where(FileCurrentDataRecord.task_id.in_(task_ids)))
    session.execute(
        delete(ConfirmedDocumentRecord).where(ConfirmedDocumentRecord.task_id.in_(task_ids))
    )
    session.execute(
        delete(ExtractionRecord).where(ExtractionRecord.task_id.in_(task_ids))
    )
    session.execute(
        delete(ReviewRevisionRecord).where(ReviewRevisionRecord.task_id.in_(task_ids))
    )
    session.execute(
        update(TaskRecord)
        .where(TaskRecord.duplicate_of_task_id.in_(task_ids))
        .values(duplicate_of_task_id=None)
    )
    session.execute(delete(TaskRecord).where(TaskRecord.id.in_(task_ids)))


def _delete_files_and_records(session: Session, settings: Settings, tasks: list[TaskRecord]) -> None:
    from document_pipeline_api.services.original_cleanup import (
        enqueue_original_deletions, enqueue_rendered_deletions, drain_original_deletions,
    )
    enqueue_original_deletions(session, settings, _task_storage_paths(settings, tasks))
    enqueue_rendered_deletions(session, [task.id for task in tasks])
    _delete_task_records(session, [task.id for task in tasks])
    session.commit()
    drain_original_deletions(session, settings)


@guard_file_operation
def delete_task(session: Session, settings: Settings, task_id: str) -> int:
    """删除整个文件：当前数据、该文件修订、提取历史与内部原文件一起删除。

    旧接口保留行数固定返回 0；独立合并/导入的数据不属于此文件。
    """
    # The file lock excludes relocation/cleanup, but retries and worker claims
    # use database writes. Hold their write boundary before deciding eligibility.
    begin_write_snapshot(session)
    session.expire_all()
    task = session.get(TaskRecord, task_id)
    if task is None:
        session.rollback()
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if task.status not in DELETABLE_TASK_STATUSES:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="只有已完成、失败、已取消、待选模板或已暂停的任务可以删除。",
        )
    _delete_files_and_records(session, settings, [task])
    clear_pause_if_queue_empty(session)
    return 0


@guard_file_operation
def delete_tasks_batch(
    session: Session, settings: Settings, task_ids: list[str], *, pending_ids: list[str] | None = None,
) -> tuple[int, int]:
    """批量删除任务：仅删除可删除状态的任务。

    返回 (实际删除数, 被删任务保留的数据表行数合计)，不可删任务静默跳过，
    由调用方把 skipped 数如实返回给前端，避免"显示删了 N 个、实际删更少"。
    单次事务完成（含批量确认弹窗一次即可删除全部选中任务）。
    """
    if not task_ids and not pending_ids:
        return 0, 0
    begin_write_snapshot(session)
    session.expire_all()
    tasks = list(
        session.scalars(select(TaskRecord).where(TaskRecord.id.in_(task_ids)))
    )
    deletable = [
        task
        for task in tasks
        if task.status in DELETABLE_TASK_STATUSES
    ]
    if not deletable and not pending_ids:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="只有已完成、失败、已取消、待选模板或已暂停的任务可以删除。",
        )
    if pending_ids:
        session.execute(delete(SystemSettingRecord).where(SystemSettingRecord.key.in_(
            [PENDING_DUPLICATE_PREFIX + item_id for item_id in pending_ids])))
    if deletable:
        _delete_files_and_records(session, settings, deletable)
        clear_pause_if_queue_empty(session)
    else:
        session.commit()
    return len(deletable), 0


@guard_file_operation
def clear_history(session: Session, settings: Settings) -> int:
    """清理全部历史任务：已完成/失败/取消/待确认，保留进行中任务及独立数据。"""
    begin_write_snapshot(session)
    session.expire_all()
    tasks = list(
        session.scalars(
            select(TaskRecord).where(
                TaskRecord.status.in_(HISTORY_CLEARABLE_STATUSES)
            )
        )
    )
    if not tasks:
        session.rollback()
        return 0
    _delete_files_and_records(session, settings, tasks)
    return len(tasks)
