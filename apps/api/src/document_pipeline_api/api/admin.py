from typing import Annotated
import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import get_session
from document_pipeline_api.services.system_settings import (
    get_bool_setting,
    set_bool_setting,
    get_setting,
    set_setting,
)
from document_pipeline_api.services.tasks import clear_history
from document_pipeline_api.services.clear_data import ClearDataError, clear_journal_path, clear_local_data


router = APIRouter(prefix="/system", tags=["system"])
SessionDependency = Annotated[Session, Depends(get_session)]


class SystemSettingsRead(BaseModel):
    image_convert: bool
    office_convert: bool
    # Word 是否连同内嵌图片一起识别：True=文字+图片一起，False=只提取文本（默认）
    word_include_images: bool
    allow_limited_input: bool = False
    upload_limit_mb: int = 50
    input_text_limit: int = 20000
    input_page_limit: int = 10
    input_row_limit: int = 500
    input_docx_image_limit: int = 10


class SystemSettingsUpdate(BaseModel):
    image_convert: bool | None = None
    office_convert: bool | None = None
    # 可选：旧的调用方（不传该字段）保持只控制 office_convert 的行为
    word_include_images: bool | None = None
    allow_limited_input: bool | None = None
    upload_limit_mb: int | None = Field(default=None, ge=1)
    input_text_limit: int | None = Field(default=None, ge=1)
    input_page_limit: int | None = Field(default=None, ge=1)
    input_row_limit: int | None = Field(default=None, ge=1)
    input_docx_image_limit: int | None = Field(default=None, ge=1)


INPUT_DEFAULTS = {"upload_limit_mb": 50, "input_text_limit": 20000, "input_page_limit": 10,
                  "input_row_limit": 500, "input_docx_image_limit": 10}


class ClearHistoryRequest(BaseModel):
    confirm_text: str = Field(min_length=1, max_length=32)


class ClearHistoryResult(BaseModel):
    cleared_count: int


@router.get("/settings", response_model=SystemSettingsRead)
def get_settings(session: SessionDependency) -> SystemSettingsRead:
    return SystemSettingsRead(
        image_convert=get_bool_setting(session, "image_convert", True),
        office_convert=get_bool_setting(session, "office_convert", True),
        word_include_images=get_bool_setting(session, "word_include_images", False),
        allow_limited_input=get_bool_setting(session, "allow_limited_input", False),
        **{key: int(get_setting(session, key, str(default))) for key, default in INPUT_DEFAULTS.items()},
    )


@router.put("/settings", response_model=SystemSettingsRead)
def update_settings(
    update: SystemSettingsUpdate,
    session: SessionDependency,
) -> SystemSettingsRead:
    for key in ("image_convert", "office_convert", "word_include_images"):
        value = getattr(update, key)
        if value is not None:
            set_bool_setting(session, key, value)
    if update.allow_limited_input is not None:
        set_bool_setting(session, "allow_limited_input", update.allow_limited_input)
    for key in INPUT_DEFAULTS:
        value = getattr(update, key)
        if value is not None:
            set_setting(session, key, str(value))
    session.commit()
    return SystemSettingsRead(
        image_convert=get_bool_setting(session, "image_convert", True),
        office_convert=get_bool_setting(session, "office_convert", True),
        word_include_images=get_bool_setting(session, "word_include_images", False),
        allow_limited_input=get_bool_setting(session, "allow_limited_input", False),
        **{key: int(get_setting(session, key, str(default))) for key, default in INPUT_DEFAULTS.items()},
    )


@router.post("/admin/clear-history", response_model=ClearHistoryResult)
def clear_history_endpoint(
    request: Request,
    body: ClearHistoryRequest,
    session: SessionDependency,
) -> ClearHistoryResult:
    if body.confirm_text != "清除历史":
        raise HTTPException(status_code=422, detail="确认文字不匹配，未执行任何清理。")
    settings: Settings = request.app.state.settings
    cleared = clear_history(session, settings)
    return ClearHistoryResult(cleared_count=cleared)


@router.get("/admin/clear-data")
def clear_data_status(request: Request) -> dict:
    settings = request.app.state.settings
    result = None
    path = settings.storage_dir.parent / "runtime" / "clear-data-result.json"
    try:
        if path.is_file() and path.stat().st_size < 8192:
            result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"data_directory": str(settings.storage_dir.parent.resolve()), "original_directory": str(settings.storage_dir.resolve()), "incomplete": clear_journal_path(settings).exists(), "result": result}


@router.post("/admin/clear-data")
def clear_all_data_endpoint(request: Request, body: ClearHistoryRequest) -> dict:
    if body.confirm_text != "清除全部本地数据":
        raise HTTPException(422, "确认文字不匹配，未执行任何清理。")
    settings = request.app.state.settings
    data_dir = settings.storage_dir.parent
    if os.getenv("DOCUMENT_PIPELINE_SUPERVISED") == "1" or (data_dir / "runtime" / "supervisor.json").is_file():
        from document_pipeline_api.supervisor import request_clear_data
        if not request_clear_data(data_dir):
            raise HTTPException(409, "无法安排安全清除，请重启知意后再试；尚未清除数据。")
        return {"scheduled": True, "restart_required": True}
    if settings.queue_enabled:
        raise HTTPException(409, "当前不是受监督的桌面实例，请先停止独立 Worker，再以无 Worker 模式清除。")
    condition = request.app.state.maintenance_condition
    with condition:
        if request.app.state.maintenance_active:
            raise HTTPException(409, "已有数据维护操作正在进行。")
        request.app.state.maintenance_active = True
        deadline = time.monotonic() + 5
        while request.app.state.active_mutations > 1:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                request.app.state.maintenance_active = False
                raise HTTPException(409, "其他写入尚未结束，请稍后重试清除。")
            condition.wait(remaining)
    try:
        from sqlalchemy import select
        from document_pipeline_api.models import TaskRecord
        with request.app.state.session_factory() as session:
            if session.scalar(select(TaskRecord.id).where(TaskRecord.status.in_(["processing", "validating"])).limit(1)):
                raise HTTPException(409, "仍有处理中的任务，请先停止 Worker 后重试清除。")
        result = clear_local_data(settings, request.app.state.model_secret_store)
        return {"scheduled": False, "restart_required": False, **result}
    except ClearDataError as error:
        raise HTTPException(409, str(error)) from error
    finally:
        with condition:
            request.app.state.maintenance_active = False
            condition.notify_all()
