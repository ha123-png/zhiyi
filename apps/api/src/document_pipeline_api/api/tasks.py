import json
from document_pipeline_api.schemas.file_export import ExportAction
import mimetypes
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path as ApiPath,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.public_errors import public_error_message
from document_pipeline_api.db import get_session
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import ExtractionRecord
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.schemas.extraction import ExtractionRead, ReviewUpdate
from document_pipeline_api.schemas.data_tables import ConfirmRequest, ConfirmationRead
from document_pipeline_api.schemas.tasks import (
    TaskRead,
    TaskTemplateSelection,
    TasksSummary,
)
from document_pipeline_api.services.extraction import get_extraction, save_review
from document_pipeline_api.services.data_tables import confirm_task, get_confirmation
from document_pipeline_api.services.tasks import (
    cancel_task,
    create_task_from_upload,
    delete_task,
    delete_tasks_batch,
    list_tasks,
    pause_task,
    resume_task,
    retry_task,
    retry_tasks,
    select_task_template,
    accept_task_scope,
)
from document_pipeline_api.services.file_formats import (
    UnsupportedTextFileError,
    extract_docx_blocks,
    extract_text,
    extract_xlsx_sheets,
)
from document_pipeline_api.services.queue_pause import (
    freeze_new_task_if_paused,
    is_queue_paused,
)
from document_pipeline_api.storage_paths import resolve_task_storage_path


router = APIRouter(prefix="/tasks", tags=["tasks"])
SessionDependency = Annotated[Session, Depends(get_session)]

_TEXT_PREVIEW_TYPES = {
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_PREVIEW_CHARS = 200_000
_MAX_DOCX_PREVIEW_IMAGES = 50
_MAX_DOCX_PREVIEW_IMAGE_BYTES = 20 * 1024 * 1024


class TextPreviewRead(BaseModel):
    kind: str
    text: str
    truncated: bool = False
    image_count: int = 0


class XlsxSheetRead(BaseModel):
    name: str
    columns: list[str]
    rows: list[list[str]]


class XlsxPreviewRead(BaseModel):
    sheets: list[XlsxSheetRead]


class DocxBlockRead(BaseModel):
    type: str
    text: str = ""
    level: int = 0
    rows: list[list[str]] = Field(default_factory=list)
    image_index: int = 0
    caption: str = ""


class DocxPreviewRead(BaseModel):
    blocks: list[DocxBlockRead]


@router.get("", response_model=list[TaskRead])
def get_tasks(
    request: Request,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    active_only: bool = False,
    export_pending: bool = False,
    status: str | None = None,
    search: str | None = None,
    template_id: str | None = None,
    since: datetime | None = None,
) -> list[TaskRead]:
    """任务列表。limit/offset 用于历史页分页；active_only 供全局任务条与状态监控轮询
    （只拉还在动的任务，避免万级历史任务每次全量加载）；status/search/template_id/since
    供历史页服务端筛选。历史记录数 = 提取快照（当时提取出多少条明细就是多少条），不依赖数据表。
    """
    # 1.0 前把旧的“省略 limit 即全表返回”收紧为默认 100 条。继续全量返回会让
    # 一个长期运行的本地实例最终耗尽 API/浏览器内存；集成方应显式翻页。
    tasks = list_tasks(
        session,
        limit=limit,
        offset=offset,
        active_only=active_only,
        export_pending=export_pending,
        status=status,
        search=search,
        template_id=template_id,
        since=since,
    )
    task_ids = [task.id for task in tasks]
    if not task_ids:
        return []
    record_counts: dict[str, int] = {}
    elapsed_seconds: dict[str, float] = {}
    processing_engines: dict[str, str] = {}
    processing_models: dict[str, str] = {}
    if not active_only:
        # 只对本页任务做快照计数（子查询，不遍历全部 extraction 记录）
        pairs = session.execute(
            select(
                ExtractionRecord.task_id,
                ExtractionRecord.document_kind,
                ExtractionRecord.result_json,
                ExtractionRecord.elapsed_seconds,
                ExtractionRecord.model_name,
            ).where(ExtractionRecord.task_id.in_(task_ids))
        ).all()
        for task_id, document_kind, result_json, elapsed, model_name in pairs:
            record_counts[task_id] = _extraction_snapshot_rows(
                document_kind,
                result_json,
            )
            elapsed_seconds[task_id] = round(float(elapsed), 1)
            processing_engines[task_id] = "multimodal_model"
            processing_models[task_id] = model_name
    return [
        TaskRead.model_validate(task).model_copy(
            update={
                "internal_storage": _storage_description(request.app.state.settings, task),
                "record_count": record_counts.get(task.id, 0),
                "processing_elapsed_seconds": elapsed_seconds.get(task.id),
                "processing_engine": processing_engines.get(task.id),
                "processing_model": processing_models.get(task.id),
            }
        )
        for task in tasks
    ]


def _storage_description(settings, task):
    if not task.internal_storage:
        return None
    try:
        return {**task.internal_storage, "absolute_path": str(resolve_task_storage_path(settings.storage_dir, task))}
    except (ValueError, OSError):
        return {**task.internal_storage, "error": "原件位置无法验证，请检查存储目录。"}


def _extraction_snapshot_rows(document_kind: str, result_json: str) -> int:
    """提取快照的记录数：明细取 items 长度；没有明细但表头字段有内容时算 1 条，
    更符合直觉——只有表头没有明细的提取（如自定义模板）也算一条记录。"""
    try:
        payload = json.loads(result_json)
    except (TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 1 if payload else 0
    items = payload.get("items")
    if isinstance(items, list):
        if items:
            return len(items)
        items = []
    # 表头：自定义模板结果在 header 下，内置单据结果在顶层（items 之外的字段）
    header = payload.get("header") if isinstance(payload.get("header"), dict) else payload
    if any(header.get(key) not in (None, "") for key in header):
        return 1
    return 0


@router.get("/summary", response_model=TasksSummary)
def tasks_summary(
    session: SessionDependency,
    search: str | None = None,
    template_id: str | None = None,
    since: datetime | None = None,
) -> TasksSummary:
    """各状态任务计数（历史页统计条与 tab 计数）。

    支持与列表相同的筛选参数，便于在"搜索/模板/时间筛选"下计算总页数；
    用 SQL 聚合（status 列在复合索引内），不加载任务全表。
    """
    statement = select(TaskRecord.status, func.count()).group_by(TaskRecord.status)
    if search:
        from document_pipeline_api.services.tasks import task_name_matches
        statement = statement.where(task_name_matches(search))
    if template_id:
        statement = statement.where(TaskRecord.template_id == template_id)
    if since is not None:
        statement = statement.where(TaskRecord.updated_at >= since)
    rows = session.execute(statement).all()
    summary = TasksSummary(total=sum(count for _, count in rows))
    from document_pipeline_api.services.tasks import ACTIVE_TASK_STATUSES
    for status_value, count in rows:
        if status_value in ACTIVE_TASK_STATUSES:
            summary.active += count
        if status_value == TaskStatus.WAITING_FOR_TEMPLATE.value:
            summary.waiting_for_action = count
        if status_value == TaskStatus.COMPLETED.value:
            summary.completed = count
        elif status_value == TaskStatus.NEEDS_REVIEW.value:
            summary.needs_review = count
        elif status_value == TaskStatus.FAILED.value:
            summary.failed = count
    export_count = select(func.count()).select_from(TaskRecord).where(
        TaskRecord.status == TaskStatus.COMPLETED.value,
        func.json_extract(TaskRecord.export_state_json, "$.status").in_(["failed", "needs_rebind"]),
    )
    if statement.whereclause is not None:
        export_count = export_count.where(statement.whereclause)
    summary.pending_exports = session.scalar(export_count) or 0
    return summary


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    request: Request,
    session: SessionDependency,
    file: Annotated[UploadFile, File()],
    template_mode: Annotated[str, Form()] = "smart",
    template_id: Annotated[str | None, Form()] = None,
    target_table_id: Annotated[str | None, Form()] = None,
) -> TaskRead:
    settings: Settings = request.app.state.settings
    task = await create_task_from_upload(
        session,
        settings,
        file,
        template_mode,
        template_id,
        target_table_id=target_table_id,
    )
    from document_pipeline_api.services.internal_storage import classify_task_original
    classify_task_original(session, settings, task.id)
    frozen = freeze_new_task_if_paused(session, task.id)
    if settings.queue_enabled and not frozen:
        from document_pipeline_api.services.queueing import enqueue_task

        enqueue_task(task.id)
    return TaskRead.model_validate(task)


@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel(request: Request, task_id: str, session: SessionDependency) -> TaskRead:
    task = cancel_task(session, task_id)
    settings: Settings = request.app.state.settings
    if settings.queue_enabled:
        from document_pipeline_api.services.queueing import revoke_task

        revoke_task(task_id)
    return TaskRead.model_validate(task)


@router.post("/{task_id}/retry", response_model=TaskRead)
def retry(request: Request, task_id: str, session: SessionDependency, use_current_settings: bool = False) -> TaskRead:
    settings: Settings = request.app.state.settings
    task = retry_task(session, settings, task_id, use_current_settings=use_current_settings)
    frozen = freeze_new_task_if_paused(session, task.id)
    if settings.queue_enabled and not frozen:
        from document_pipeline_api.services.queueing import enqueue_task

        enqueue_task(task.id)
    return TaskRead.model_validate(task)


class BatchRetryRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1)


@router.post("/batch-retry")
def retry_batch(
    request: Request,
    body: BatchRetryRequest,
    session: SessionDependency,
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    retried = retry_tasks(session, settings, body.task_ids)
    runnable = [
        task_id
        for task_id in retried
        if not freeze_new_task_if_paused(session, task_id)
    ]
    if settings.queue_enabled:
        from document_pipeline_api.services.queueing import enqueue_task

        for task_id in runnable:
            enqueue_task(task_id)
    return {"retried": retried, "skipped": len(body.task_ids) - len(retried)}


class BatchDeleteRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1)


@router.post("/batch-delete")
def delete_batch(
    request: Request,
    body: BatchDeleteRequest,
    session: SessionDependency,
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    deleted, kept_rows = delete_tasks_batch(session, settings, body.task_ids)
    return {
        "deleted": deleted,
        "skipped": len(body.task_ids) - deleted,
        "kept_rows": kept_rows,
    }


@router.post("/{task_id}/pause", response_model=TaskRead)
def pause(request: Request, task_id: str, session: SessionDependency) -> TaskRead:
    task = pause_task(session, task_id)
    settings: Settings = request.app.state.settings
    if settings.queue_enabled:
        from document_pipeline_api.services.queueing import revoke_task

        revoke_task(task_id)
    return TaskRead.model_validate(task)


@router.post("/{task_id}/resume", response_model=TaskRead)
def resume(request: Request, task_id: str, session: SessionDependency) -> TaskRead:
    settings: Settings = request.app.state.settings
    if is_queue_paused(session):
        raise HTTPException(status_code=409, detail="队列仍处于暂停状态，请先启动整个队列。")
    task = resume_task(session, settings, task_id)
    if settings.queue_enabled:
        from document_pipeline_api.services.queueing import enqueue_task

        enqueue_task(task.id)
    return TaskRead.model_validate(task)


@router.delete("/{task_id}")
def delete(request: Request, task_id: str, session: SessionDependency) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    kept_rows = delete_task(session, settings, task_id)
    return {"task_id": task_id, "kept_rows": kept_rows}


@router.post("/{task_id}/template", response_model=TaskRead)
def select_template(
    request: Request,
    task_id: str,
    selection: TaskTemplateSelection,
    session: SessionDependency,
) -> TaskRead:
    settings: Settings = request.app.state.settings
    if selection.target_table_id:
        confirmation = confirm_task(
            session,
            task_id,
            0,
            target_table_id=selection.target_table_id,
        )
        from document_pipeline_api.services.task_exports import process_task_export
        process_task_export(session, settings, task_id)
        task = session.get(TaskRecord, confirmation.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="没有找到这个任务。")
        return TaskRead.model_validate(task)
    if not selection.template_id:
        raise HTTPException(status_code=422, detail="请选择模板或数据表。")
    task = select_task_template(
        session,
        settings,
        task_id,
        selection.template_id,
    )
    from document_pipeline_api.services.internal_storage import classify_task_original
    classify_task_original(session, settings, task.id)
    frozen = freeze_new_task_if_paused(session, task.id)
    if settings.queue_enabled and not frozen:
        from document_pipeline_api.services.queueing import enqueue_task

        enqueue_task(task.id)
    return TaskRead.model_validate(task)


@router.post("/{task_id}/input-scope", response_model=TaskRead)
def accept_scope(request: Request, task_id: str, session: SessionDependency) -> TaskRead:
    settings: Settings = request.app.state.settings
    task = accept_task_scope(session, settings, task_id)
    frozen = freeze_new_task_if_paused(session, task.id)
    if settings.queue_enabled and not frozen:
        from document_pipeline_api.services.queueing import enqueue_task
        enqueue_task(task.id)
    return TaskRead.model_validate(task)


@router.get("/{task_id}/diagnostics")
def task_diagnostics(task_id: str, session: SessionDependency):
    from document_pipeline_api.model_diagnostics import safe_diagnostic
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    detail = task.failure_detail or task.failure_message or "当前任务没有失败诊断。"
    return {"code": task.failure_code, "detail": safe_diagnostic(detail), "attempt": task.attempt_count}


@router.get("/{task_id}/result", response_model=ExtractionRead)
def result(task_id: str, session: SessionDependency) -> ExtractionRead:
    return get_extraction(session, task_id)


@router.post("/{task_id}/export", response_model=TaskRead)
def export_action(task_id: str, body: ExportAction, request: Request, session: SessionDependency) -> TaskRecord:
    from document_pipeline_api.services.task_exports import act_on_task_export
    return act_on_task_export(session, request.app.state.settings, task_id, body)


@router.put("/{task_id}/review", response_model=ExtractionRead)
def update_review(
    task_id: str,
    update: ReviewUpdate,
    session: SessionDependency,
    request: Request,
) -> ExtractionRead:
    result = save_review(session, task_id, update)
    from document_pipeline_api.services.task_exports import process_task_export
    process_task_export(session, request.app.state.settings, task_id)
    return result


@router.post("/{task_id}/confirm", response_model=ConfirmationRead)
def confirm(
    task_id: str,
    request: ConfirmRequest,
    session: SessionDependency,
    http_request: Request,
) -> ConfirmationRead:
    confirmation = confirm_task(
        session,
        task_id,
        request.expected_review_version,
        target_table_id=request.target_table_id,
        filename=request.filename,
    )
    from document_pipeline_api.services.task_exports import process_task_export
    process_task_export(session, http_request.app.state.settings, task_id)
    return confirmation


@router.get("/{task_id}/confirmation", response_model=ConfirmationRead)
def confirmation(task_id: str, session: SessionDependency) -> ConfirmationRead:
    return get_confirmation(session, task_id)


@router.get("/{task_id}/file", response_class=FileResponse)
def original_file(
    task_id: str,
    request: Request,
    session: SessionDependency,
) -> FileResponse:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    try:
        path = resolve_task_storage_path(
            storage_dir=request.app.state.settings.storage_dir,
            task=task,
        )
    except ValueError as error:
        raise HTTPException(status_code=410, detail="原文件存储引用无效。") from error
    if not path.is_file():
        raise HTTPException(status_code=410, detail="原文件已经不在存储位置。")
    return FileResponse(path, media_type=task.content_type, filename=task.filename)


@router.get("/{task_id}/preview", response_model=TextPreviewRead)
def text_preview(
    task_id: str,
    request: Request,
    session: SessionDependency,
) -> TextPreviewRead:
    """为不能由浏览器直接显示的文本/Office 原文件提供只读可读预览。"""
    task, path = _preview_task_path(task_id, request, session)
    if task.content_type not in _TEXT_PREVIEW_TYPES:
        raise HTTPException(status_code=415, detail="这个文件类型不使用文本预览。")
    try:
        text = extract_text(
            path,
            task.content_type,
            max_uncompressed_bytes=request.app.state.settings.max_import_uncompressed_bytes,
            max_rows=request.app.state.settings.max_import_rows,
            max_columns=request.app.state.settings.max_import_columns,
        )
        image_count = len(_docx_preview_image_names(path, request)) if task.content_type == _DOCX_TYPE else 0
    except (UnsupportedTextFileError, zipfile.BadZipFile, OSError) as error:
        raise HTTPException(
            status_code=422,
            detail=public_error_message(error, "原文件预览失败，请确认文件未损坏。"),
        ) from error
    truncated = len(text) > _MAX_PREVIEW_CHARS
    return TextPreviewRead(
        kind="markdown" if task.content_type == "text/markdown" else "text",
        text=text[:_MAX_PREVIEW_CHARS],
        truncated=truncated,
        image_count=image_count,
    )


@router.get("/{task_id}/preview/xlsx", response_model=XlsxPreviewRead)
def xlsx_preview(
    task_id: str,
    request: Request,
    session: SessionDependency,
) -> XlsxPreviewRead:
    """把 xlsx 原文件以结构化表格（工作表/列/单元格）返回，供只读表格预览。"""
    task, path = _preview_task_path(task_id, request, session)
    if task.content_type != _XLSX_TYPE:
        raise HTTPException(status_code=415, detail="这个文件类型不是 Excel 表格。")
    try:
        sheets = extract_xlsx_sheets(
            path,
            max_uncompressed_bytes=request.app.state.settings.max_import_uncompressed_bytes,
            max_rows=request.app.state.settings.max_import_rows,
            max_columns=request.app.state.settings.max_import_columns,
        )
    except UnsupportedTextFileError as error:
        raise HTTPException(
            status_code=422,
            detail=public_error_message(error, "原文件预览失败，请确认文件未损坏。"),
        ) from error
    return XlsxPreviewRead(sheets=sheets)


@router.get("/{task_id}/preview/docx", response_model=DocxPreviewRead)
def docx_preview(
    task_id: str,
    request: Request,
    session: SessionDependency,
) -> DocxPreviewRead:
    """把 docx 原文件按结构（标题/段落/列表/表格/按位图片）返回，供只读预览。"""
    task, path = _preview_task_path(task_id, request, session)
    if task.content_type != _DOCX_TYPE:
        raise HTTPException(status_code=415, detail="这个文件不是 Word 文档。")
    try:
        blocks = extract_docx_blocks(
            path,
            max_uncompressed_bytes=request.app.state.settings.max_import_uncompressed_bytes,
            max_images=_MAX_DOCX_PREVIEW_IMAGES,
            max_rows=request.app.state.settings.max_import_rows,
            max_columns=request.app.state.settings.max_import_columns,
        )
    except UnsupportedTextFileError as error:
        raise HTTPException(
            status_code=422,
            detail=public_error_message(error, "原文件预览失败，请确认文件未损坏。"),
        ) from error
    return DocxPreviewRead(blocks=blocks)


@router.get("/{task_id}/preview/images/{image_index}")
def docx_preview_image(
    task_id: str,
    image_index: Annotated[int, ApiPath(ge=1, le=_MAX_DOCX_PREVIEW_IMAGES)],
    request: Request,
    session: SessionDependency,
) -> Response:
    task, path = _preview_task_path(task_id, request, session)
    if task.content_type != _DOCX_TYPE:
        raise HTTPException(status_code=415, detail="这个文件不是 Word 文档。")
    try:
        images = _docx_preview_image_names(path, request)
        if image_index > len(images):
            raise HTTPException(status_code=404, detail="没有找到这张内嵌图片。")
        name = images[image_index - 1]
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(name)
            if info.file_size > _MAX_DOCX_PREVIEW_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail="这张内嵌图片超过预览上限。")
            content = archive.read(info)
    except zipfile.BadZipFile as error:
        raise HTTPException(status_code=422, detail="Word 文档损坏，无法读取内嵌图片。") from error
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="内嵌对象不是可预览图片。")
    return Response(content, media_type=media_type)


def _preview_task_path(
    task_id: str,
    request: Request,
    session: Session,
) -> tuple[TaskRecord, Path]:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    try:
        path = resolve_task_storage_path(request.app.state.settings.storage_dir, task)
    except ValueError as error:
        raise HTTPException(status_code=410, detail="原文件存储引用无效。") from error
    if not path.is_file():
        raise HTTPException(status_code=410, detail="原文件已经不在存储位置。")
    return task, path


def _docx_preview_image_names(path: Path, request: Request) -> list[str]:
    max_uncompressed = request.app.state.settings.max_import_uncompressed_bytes
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > max_uncompressed:
            raise UnsupportedTextFileError("Word 文档解压后超过预览上限。")
        image_infos = sorted(
            (info for info in infos if info.filename.startswith("word/media/") and not info.is_dir()),
            key=lambda info: info.filename,
        )[:_MAX_DOCX_PREVIEW_IMAGES]
        return [info.filename for info in image_infos]
