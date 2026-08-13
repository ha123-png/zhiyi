import hashlib
import json
from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete, func, insert, literal, select, update
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
    ReviewRevisionRecord,
)
from document_pipeline_api.models.task import TaskRecord, utc_now
from document_pipeline_api.services.file_formats import (
    CONVERTIBLE_IMAGE_TYPES,
    RASTER_IMAGE_TYPES,
    TEXT_CONTENT_TYPES,
    UnsupportedImageError,
    UnsupportedTextFileError,
    extract_text,
    inspect_image_frame_count,
    split_text_pages,
)
from document_pipeline_api.services.pdf_rendering import (
    UnsupportedPdfError,
    inspect_pdf_page_count,
)
from document_pipeline_api.services.model_runtime import model_snapshot_values
from document_pipeline_api.services.queue_pause import clear_pause_if_queue_empty
from document_pipeline_api.services.system_settings import get_bool_setting
from document_pipeline_api.services.templates import get_active_template
from document_pipeline_api.storage_paths import task_storage_name


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
HISTORY_CLEARABLE_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.NEEDS_REVIEW.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


async def create_task_from_upload(
    session: Session,
    settings: Settings,
    upload: UploadFile,
    template_mode: str,
    template_id: str | None = None,
    *,
    target_table_id: str | None = None,
) -> TaskRecord:
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

    try:
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="文件超过当前允许的大小。",
                    )
                digest.update(chunk)
                target.write(chunk)

        page_count = 1
        if upload.content_type == "application/pdf":
            try:
                page_count = inspect_pdf_page_count(
                    temporary_path,
                    max_pages=settings.max_pdf_pages,
                )
            except UnsupportedPdfError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(
                    status_code=422,
                    detail="PDF 文件损坏或无法读取。",
                ) from error
        elif upload.content_type in TEXT_CONTENT_TYPES:
            try:
                text = extract_text(
                    temporary_path,
                    upload.content_type,
                    max_uncompressed_bytes=settings.max_import_uncompressed_bytes,
                    max_rows=settings.max_import_rows,
                    max_columns=settings.max_import_columns,
                )
                text_pages = split_text_pages(
                    text,
                    max_pages=settings.max_pdf_pages,
                )
                page_count = len(text_pages)
            except UnsupportedTextFileError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        elif upload.content_type in RASTER_IMAGE_TYPES:
            try:
                page_count = inspect_image_frame_count(
                    temporary_path,
                    expected_content_type=upload.content_type,
                    max_frames=settings.max_pdf_pages,
                    max_total_pixels=settings.max_image_total_pixels,
                )
            except UnsupportedImageError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

        sha256 = digest.hexdigest()
        duplicate_id = session.scalar(
            select(TaskRecord.id)
            .where(TaskRecord.sha256 == sha256)
            .order_by(TaskRecord.created_at.desc())
            .limit(1)
        )
        temporary_path.replace(final_path)
        selected_template = (
            get_active_template(session, template_id) if template_id else None
        )
        _insert_task_if_capacity_available(
            session,
            settings,
            task_id=task_id,
            filename=upload.filename,
            content_type=upload.content_type,
            size_bytes=size,
            page_count=page_count,
            sha256=sha256,
            storage_path=task_storage_name(task_id, upload.content_type),
            template_mode="manual" if selected_template else template_mode,
            template_id=selected_template.id if selected_template else None,
            template_version=(
                selected_template.version if selected_template else None
            ),
            duplicate_of_task_id=duplicate_id,
            target_table_id=target_table_id,
        )
        session.commit()
        task = session.get(TaskRecord, task_id)
        if task is None:
            raise RuntimeError("已入队任务没有成功写入数据库。")
        return task
    except OSError as error:
        session.rollback()
        _discard_upload_paths(temporary_path, final_path)
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="无法保存上传文件，请检查磁盘空间和文件夹权限后重试。",
        ) from error
    except Exception:
        session.rollback()
        _discard_upload_paths(temporary_path, final_path)
        raise
    finally:
        await upload.close()


def _discard_upload_paths(*paths) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _insert_task_if_capacity_available(
    session: Session,
    settings: Settings,
    *,
    task_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    page_count: int,
    sha256: str,
    storage_path: str,
    template_mode: str,
    template_id: str | None,
    template_version: int | None,
    duplicate_of_task_id: str | None,
    target_table_id: str | None = None,
) -> None:
    capacity_conditions = _capacity_conditions(
        settings,
        additional_bytes=size_bytes,
        additional_pages=page_count,
    )
    now = utc_now()
    columns = [
        "id",
        "filename",
        "content_type",
        "size_bytes",
        "page_count",
        "sha256",
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
        literal(sha256),
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
    result = session.execute(insert(TaskRecord).from_select(columns, values))
    if result.rowcount != 1:
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
        select(func.coalesce(func.sum(TaskRecord.page_count), 0))
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
            f"{settings.max_active_bytes} 字节、{settings.max_active_pages} 页。"
        ),
        headers={"Retry-After": "5"},
    )


# 状态监控与全局任务条只关心"还在动"的任务；终态任务（已完成/失败/取消）交给历史页。
# 轮询接口用 active_only=True 只拉这些状态，避免万级历史任务每次全量加载。
ACTIVE_TASK_STATUSES = {
    TaskStatus.CREATED.value,
    TaskStatus.QUEUED.value,
    TaskStatus.PROCESSING.value,
    TaskStatus.VALIDATING.value,
    TaskStatus.WAITING_FOR_TEMPLATE.value,
    TaskStatus.NEEDS_REVIEW.value,
    TaskStatus.PAUSED.value,
}


def list_tasks(
    session: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    active_only: bool = False,
    status: str | None = None,
    search: str | None = None,
    template_id: str | None = None,
    since: datetime | None = None,
) -> list[TaskRecord]:
    """任务列表。

    limit/offset 分页；active_only 只拉还在动的任务（全局任务条/状态监控轮询）；
    status/search/template_id/since 供历史页服务端筛选，避免万级历史全量拉进前端内存。
    """
    # created_at 可能同一毫秒写入多条；增加主键作为稳定次序，避免 offset 翻页时
    # 同时间任务在两页之间重复或遗漏。
    statement = select(TaskRecord).order_by(
        TaskRecord.created_at.desc(),
        TaskRecord.id.desc(),
    )
    if active_only:
        statement = statement.where(
            TaskRecord.status.in_(ACTIVE_TASK_STATUSES)
        )
    if status is not None:
        statement = statement.where(TaskRecord.status == status)
    if search:
        statement = statement.where(
            TaskRecord.filename.ilike(f"%{search}%")
        )
    if template_id:
        statement = statement.where(TaskRecord.template_id == template_id)
    if since is not None:
        statement = statement.where(TaskRecord.updated_at >= since)
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
) -> TaskRecord:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    try:
        next_state = TaskState(status=TaskStatus(task.status)).transition_to(
            TaskStatus.QUEUED
        )
    except InvalidTaskTransition as error:
        raise HTTPException(status_code=409, detail="只有失败任务可以重试。") from error

    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.status == TaskStatus.FAILED.value,
            *_capacity_conditions(
                settings,
                additional_bytes=task.size_bytes,
                additional_pages=task.page_count,
            ),
        )
        .values(
            status=next_state.status.value,
            lease_token=None,
            lease_expires_at=None,
            failure_code=None,
            failure_message=None,
            # 重试后计时从零重新开始：重置本次处理尝试的开始时间
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
        raise RuntimeError("重试任务不存在。")
    return updated_task


def retry_tasks(
    session: Session,
    settings: Settings,
    task_ids: list[str],
) -> list[str]:
    """批量重试失败任务；每个任务独立计容量，超出容量的任务跳过并返回已重试列表。"""
    if not task_ids:
        return []
    failed_ids = list(
        session.scalars(
            select(TaskRecord.id).where(
                TaskRecord.id.in_(task_ids),
                TaskRecord.status == TaskStatus.FAILED.value,
            )
        )
    )
    retried: list[str] = []
    for task_id in failed_ids:
        try:
            retry_task(session, settings, task_id)
        except HTTPException:
            session.rollback()
            continue
        retried.append(task_id)
    return retried


def select_task_template(
    session: Session,
    settings: Settings,
    task_id: str,
    template_id: str,
) -> TaskRecord:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if task.status != TaskStatus.WAITING_FOR_TEMPLATE.value:
        raise HTTPException(status_code=409, detail="这个任务当前不需要选择模板。")

    candidates = json.loads(task.candidate_templates_json)
    candidate = next(
        (item for item in candidates if item["id"] == template_id),
        None,
    )
    # 候选只是智能匹配的推荐顺序，不是权限白名单。用户必须能够纠正模型，
    # 从全部当前有效模板中选择；否则一次分类遗漏会把任务永久困在待选状态。
    template = get_active_template(session, template_id)
    version = candidate["version"] if candidate is not None else template.version
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
                additional_pages=task.page_count,
            ),
        )
        .values(
            template_id=template_id,
            template_version=version,
            candidate_templates_json="[]",
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
                additional_pages=task.page_count,
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
) -> list[object]:
    """解析任务原文件路径；引用无效的任务不阻塞删除。"""
    from document_pipeline_api.storage_paths import resolve_task_storage_path

    paths: list[object] = []
    for task in tasks:
        try:
            paths.append(resolve_task_storage_path(settings.storage_dir, task))
        except ValueError:
            continue
    return paths


def _delete_task_records(session: Session, task_ids: list[str]) -> None:
    if not task_ids:
        return
    session.execute(
        update(DataRowRecord)
        .where(DataRowRecord.task_id.in_(task_ids))
        .values(task_id=None)
    )
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


def _discard_task_files(paths: list[object]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def delete_task(session: Session, settings: Settings, task_id: str) -> int:
    """删除单个历史任务：断开数据行溯源、删除提取/确认/评审记录与原文件。

    返回保留的数据表行数（数据表本身不删除，任务与数据行解耦）。
    """
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if task.status not in DELETABLE_TASK_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="只有已完成、失败、已取消、待选模板或已暂停的任务可以删除。",
        )
    kept_rows = session.scalar(
        select(func.count(DataRowRecord.id)).where(DataRowRecord.task_id == task_id)
    )
    paths = _task_storage_paths(settings, [task])
    _delete_task_records(session, [task_id])
    session.commit()
    _discard_task_files(paths)
    clear_pause_if_queue_empty(session)
    return kept_rows or 0


def delete_tasks_batch(
    session: Session, settings: Settings, task_ids: list[str]
) -> tuple[int, int]:
    """批量删除任务：仅删除可删除状态的任务。

    返回 (实际删除数, 被删任务保留的数据表行数合计)，不可删任务静默跳过，
    由调用方把 skipped 数如实返回给前端，避免"显示删了 N 个、实际删更少"。
    单次事务完成（含批量确认弹窗一次即可删除全部选中任务）。
    """
    if not task_ids:
        return 0, 0
    tasks = list(
        session.scalars(select(TaskRecord).where(TaskRecord.id.in_(task_ids)))
    )
    deletable = [
        task
        for task in tasks
        if task.status in DELETABLE_TASK_STATUSES
    ]
    if not deletable:
        raise HTTPException(
            status_code=409,
            detail="只有已完成、失败、已取消、待选模板或已暂停的任务可以删除。",
        )
    kept_rows = session.scalar(
        select(func.count(DataRowRecord.id)).where(
            DataRowRecord.task_id.in_([task.id for task in deletable])
        )
    )
    paths = _task_storage_paths(settings, deletable)
    _delete_task_records(session, [task.id for task in deletable])
    session.commit()
    _discard_task_files(paths)
    clear_pause_if_queue_empty(session)
    return len(deletable), kept_rows or 0


def clear_history(session: Session, settings: Settings) -> int:
    """清理全部历史任务：已完成/失败/取消/待确认，保留进行中任务和数据表数据。"""
    tasks = list(
        session.scalars(
            select(TaskRecord).where(
                TaskRecord.status.in_(HISTORY_CLEARABLE_STATUSES)
            )
        )
    )
    if not tasks:
        return 0
    task_ids = [task.id for task in tasks]
    paths = _task_storage_paths(settings, tasks)
    _delete_task_records(session, task_ids)
    session.commit()
    _discard_task_files(paths)
    return len(task_ids)
