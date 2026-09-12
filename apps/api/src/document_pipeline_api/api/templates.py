import tempfile
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from document_pipeline_api.domain.pattern_matching import matches_pattern
from document_pipeline_api.schemas.rules import PatternRule
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import get_session
from document_pipeline_api.public_errors import public_error_message
from document_pipeline_api.schemas.file_export import LocalExportRead, LocalExportUpdate
from document_pipeline_api.services.local_exports import read_local_export, update_local_export
from document_pipeline_api.schemas.templates import (
    TemplateCreate,
    TemplateDraft,
    TemplateRead,
    TemplateUpdate,
    TemplateVersionSummary,
    TemplateRestorationRead,
    TemplateVersionRestore,
)
from document_pipeline_api.services.pdf_rendering import render_pdf_pages
from document_pipeline_api.services.file_formats import (
    RASTER_IMAGE_TYPES,
    UnsupportedImageError,
    render_image_frames,
)
from document_pipeline_api.services.template_ai import (
    SUPPORTED_FILE_SUFFIXES,
    generate_template_draft,
)
from document_pipeline_api.services.templates import (
    archive_template,
    copy_template,
    create_template,
    delete_template,
    get_template,
    list_templates,
    restore_template,
    set_smart_pool,
    update_template,
    get_template_version,
    list_template_versions,
    list_template_restorations,
    restore_template_version,
)


router = APIRouter(prefix="/templates", tags=["templates"])
SessionDependency = Annotated[Session, Depends(get_session)]
_CONTENT_TYPE_BY_SUFFIX = {suffix: mime for mime, suffix in RASTER_IMAGE_TYPES.items()}


class PatternPreview(BaseModel):
    pattern: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=512)


@router.post("/rules/preview-pattern")
def preview_pattern(body: PatternPreview) -> dict:
    try:
        rule = PatternRule(kind="pattern", field="header.preview", pattern=body.pattern)
    except ValueError:
        return {"valid": False, "matches": False, "message": "表达式无效；请检查语法，不支持分组、分支或回溯引用。"}
    try:
        matched = matches_pattern(rule.pattern, body.text)
    except TimeoutError:
        return {"valid": False, "matches": False, "message": "表达式计算过久，请简化后重试。"}
    return {"valid": True, "matches": matched, "message": "整段匹配通过。" if matched else "整段匹配不通过。"}


@router.get("/{template_id}/local-export", response_model=LocalExportRead)
def local_export(template_id: str, session: SessionDependency) -> LocalExportRead:
    return read_local_export(session, template_id)


@router.put("/{template_id}/local-export", response_model=LocalExportRead)
def save_local_export(template_id: str, body: LocalExportUpdate, request: Request, session: SessionDependency) -> LocalExportRead:
    return update_local_export(session, request.app.state.settings, template_id, body)


@router.get("", response_model=list[TemplateRead])
def templates(
    session: SessionDependency,
    include_inactive: bool = Query(default=False),
) -> list[TemplateRead]:
    return list_templates(session, include_inactive=include_inactive)


@router.get("/{template_id}", response_model=TemplateRead)
def template(template_id: str, session: SessionDependency) -> TemplateRead:
    return get_template(session, template_id)


@router.post("", response_model=TemplateRead, status_code=201)
def create(body: TemplateCreate, session: SessionDependency) -> TemplateRead:
    return create_template(session, body)


@router.get("/{template_id}/restorations", response_model=list[TemplateRestorationRead])
def restorations(template_id: str, session: SessionDependency):
    return list_template_restorations(session, template_id)


@router.get("/{template_id}/versions", response_model=list[TemplateVersionSummary])
def versions(template_id: str, session: SessionDependency, before: int | None = Query(default=None, ge=1)):
    return list_template_versions(session, template_id, before)


@router.get("/{template_id}/versions/{version}", response_model=TemplateRead)
def version_detail(template_id: str, version: int, session: SessionDependency):
    return get_template_version(session, template_id, version)


@router.post("/{template_id}/versions/{version}/restore", response_model=TemplateRead)
def version_restore(template_id: str, version: int, body: TemplateVersionRestore, session: SessionDependency):
    return restore_template_version(session, template_id, version, body.expected_version, body.expected_updated_at)


@router.post("/generate", response_model=TemplateDraft)
def generate_draft(
    request: Request,
    requirement: Annotated[str, Form(...)],
    session: SessionDependency,
    files: Annotated[list[UploadFile] | None, File()] = None,
    profile_id: Annotated[str | None, Form()] = None,
    with_examples: Annotated[bool | None, Form()] = None,
    with_rules: Annotated[bool | None, Form()] = None,
) -> TemplateDraft:
    """AI 生成模板草稿：样例文件（可选）+ 需求描述 → 字段结构（不落库，人工确认后保存）。
    profile_id 可选：指定全局模型方案；缺省用任务激活方案。
    with_examples 可选：是否让 AI 生成示例值（默认 true）；关闭后示例一律留空。"""
    settings: Settings = request.app.state.settings
    if len(files or []) > settings.max_template_sample_files:
        raise HTTPException(
            status_code=413,
            detail=f"样例文件最多上传 {settings.max_template_sample_files} 个。",
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        saved: list[Path] = []
        total_bytes = 0
        for file in files or []:
            filename = file.filename or ""
            if not filename:
                raise HTTPException(status_code=400, detail="样例文件名不能为空。")
            if len(filename) > settings.max_filename_chars:
                raise HTTPException(
                    status_code=422,
                    detail=f"样例文件名不能超过 {settings.max_filename_chars} 个字符。",
                )
            suffix = Path(filename).suffix.lower()
            if suffix not in SUPPORTED_FILE_SUFFIXES:
                raise HTTPException(
                    status_code=422,
                    detail=f"不支持的样例文件类型：{filename or '(无文件名)'}。",
                )
            content = file.file.read(settings.max_upload_bytes + 1)
            if len(content) > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"样例文件超过当前允许的大小：{filename}。",
                )
            total_bytes += len(content)
            if total_bytes > settings.max_template_sample_total_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="样例文件总大小超过当前允许的上限。",
                )
            dest = tmp_dir / f"{uuid4().hex}{suffix}"
            dest.write_bytes(content)
            saved.append(dest)
        image_paths: list[Path] = []
        for path in saved:
            if path.suffix.lower() == ".pdf":
                try:
                    image_paths.extend(
                        render_pdf_pages(
                            path,
                            tmp_dir / "rendered",
                            max_pages=1,
                        )
                    )
                except Exception as error:
                    raise HTTPException(
                        status_code=422,
                        detail=public_error_message(error, "PDF 无法读取，请确认文件未损坏或未加密。"),
                    ) from error
            else:
                try:
                    image_paths.extend(
                        render_image_frames(
                            path,
                            tmp_dir / "rendered",
                            path.stem,
                            expected_content_type=_CONTENT_TYPE_BY_SUFFIX[path.suffix.lower()],
                            max_frames=settings.max_pdf_pages,
                            max_total_pixels=settings.max_image_total_pixels,
                        )
                    )
                except UnsupportedImageError as error:
                    raise HTTPException(
                        status_code=422,
                        detail=public_error_message(error, "图片无法读取，请换一张清晰、未损坏的图片。"),
                    ) from error
        if saved and not image_paths:
            raise HTTPException(status_code=422, detail="没有可分析的样例图像。")
        return generate_template_draft(
            settings,
            session,
            image_paths=image_paths,
            requirement=requirement,
            with_examples=with_examples if with_examples is not None else True,
            with_rules=with_rules if with_rules is not None else False,
            model_profile_id=profile_id,
        )


class SmartPoolUpdate(BaseModel):
    in_smart_pool: bool


@router.put("/{template_id}/smart-pool", response_model=TemplateRead)
def update_smart_pool(
    template_id: str,
    body: SmartPoolUpdate,
    session: SessionDependency,
) -> TemplateRead:
    """设置模板是否参与智能匹配预选池。"""
    return set_smart_pool(session, template_id, body.in_smart_pool)


@router.post("/{template_id}/copy", response_model=TemplateRead, status_code=201)
def copy(template_id: str, session: SessionDependency) -> TemplateRead:
    return copy_template(session, template_id)


@router.put("/{template_id}", response_model=TemplateRead)
def update(
    template_id: str,
    body: TemplateUpdate,
    session: SessionDependency,
) -> TemplateRead:
    return update_template(
        session,
        template_id,
        body.expected_version,
        TemplateCreate.model_validate(body.model_dump(exclude={"expected_version", "expected_updated_at"})),
        body.expected_updated_at,
    )


@router.post("/{template_id}/archive", response_model=TemplateRead)
def archive(template_id: str, session: SessionDependency) -> TemplateRead:
    return archive_template(session, template_id)


@router.post("/{template_id}/restore", response_model=TemplateRead)
def restore(template_id: str, session: SessionDependency) -> TemplateRead:
    return restore_template(session, template_id)


@router.delete("/{template_id}", status_code=204)
def remove_template(template_id: str, session: SessionDependency) -> None:
    delete_template(session, template_id)
