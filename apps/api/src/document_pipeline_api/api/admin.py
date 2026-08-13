from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import get_session
from document_pipeline_api.services.system_settings import (
    get_bool_setting,
    set_bool_setting,
)
from document_pipeline_api.services.tasks import clear_history


router = APIRouter(prefix="/system", tags=["system"])
SessionDependency = Annotated[Session, Depends(get_session)]


class SystemSettingsRead(BaseModel):
    image_convert: bool
    office_convert: bool
    # Word 是否连同内嵌图片一起识别：True=文字+图片一起，False=只提取文本（默认）
    word_include_images: bool


class SystemSettingsUpdate(BaseModel):
    image_convert: bool
    office_convert: bool
    # 可选：旧的调用方（不传该字段）保持只控制 office_convert 的行为
    word_include_images: bool = False


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
    )


@router.put("/settings", response_model=SystemSettingsRead)
def update_settings(
    update: SystemSettingsUpdate,
    session: SessionDependency,
) -> SystemSettingsRead:
    set_bool_setting(session, "image_convert", update.image_convert)
    set_bool_setting(session, "office_convert", update.office_convert)
    set_bool_setting(session, "word_include_images", update.word_include_images)
    session.commit()
    return SystemSettingsRead(
        image_convert=get_bool_setting(session, "image_convert", True),
        office_convert=get_bool_setting(session, "office_convert", True),
        word_include_images=get_bool_setting(session, "word_include_images", False),
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
