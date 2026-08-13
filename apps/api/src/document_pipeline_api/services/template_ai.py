"""AI 生成模板草稿：样例文件 + 需求描述 → 激活模型方案 → 字段结构草稿。

草稿不落库；人工确认后走正常创建模板流程。
安全边界：AI 只生成字段结构，不生成校验规则；输出经清洗（白名单、上限、纠正）。
"""
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.model_providers import (
    ModelProvider,
    ModelServiceError,
    build_model_provider,
)
from document_pipeline_api.schemas.templates import TemplateDraft, TemplateDraftField
from document_pipeline_api.services.model_runtime import (
    settings_for_active_profile,
    settings_for_model_profile,
)


SUPPORTED_FILE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_FIELDS = 20
MAX_REQUIREMENT_LENGTH = 2000
ALLOWED_SECTIONS = {"header", "item"}
ALLOWED_VALUE_TYPES = {"text", "number", "date", "boolean"}

# 固定系统提示词：与用户输入无关，保证模型始终按同一套规则输出字段结构。
SYSTEM_PROMPT = (
    "你是文档结构分析专家。你的任务：分析用户上传的样例文件（或根据用户需求描述）"
    "确定需要从这类文件中提取哪些字段，并只输出字段结构 JSON。\n"
    "规则：\n"
    "1. 只输出字段结构，不提取具体数值，不编造样例中没有的信息。\n"
    "2. 字段必须来自样例文件的真实内容，或用户需求明确要求的内容。\n"
    "3. 字段数量控制在 3 到 20 个，只列真正需要提取的字段。\n"
    "4. 每个字段：label=中文名；section=header（每份文件一次）或 item（每条明细重复）；"
    "example=典型示例值（没有就留空）；value_type=text|number|date|boolean。\n"
    "5. description=用一句话说明这份模板适用于哪些文件、要提取什么（30 字以内）。\n"
    "6. 严格输出 JSON，不要任何解释、客套或代码围栏。"
)

# 示范格式：刻意只用结构骨架与占位说明，不出现任何具体业务词
# （如供货方、单据号），避免污染模型对样例内容的理解。
OUTPUT_FORMAT_HINT = (
    "输出格式骨架（<> 内是占位说明，不是要提取的字段）：\n"
    '{\n'
    '  "name": "对这类文件的一个简短名称",\n'
    '  "description": "<一句话说明这份模板适用什么文件、提取什么>",\n'
    '  "fields": [\n'
    '    {"label": "<字段的中文名称>", "section": "<header 或 item>", '
    '"example": "<该字段的典型示例值，没有就留空>", "value_type": "<text 或 number 或 date 或 boolean>"}\n'
    "  ]\n"
    "}\n"
    "骨架只是格式说明：所有 label 必须依据样例文件实际内容确定，禁止照抄骨架。"
)


class AiGeneratedField(BaseModel):
    """模型输出约束（宽松）：容忍模型偏差，随后统一清洗。"""

    model_config = ConfigDict(extra="ignore")

    label: str | None = None
    section: str | None = None
    example: str | None = None
    value_type: str | None = None


class AiGeneratedTemplate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    description: str | None = None
    fields: list[AiGeneratedField] = Field(default_factory=list)


def _clean_field(field: AiGeneratedField, *, with_examples: bool) -> TemplateDraftField | None:
    label = (field.label or "").strip()
    if not label or len(label) > 128:
        return None
    section = field.section if field.section in ALLOWED_SECTIONS else "header"
    value_type = field.value_type if field.value_type in ALLOWED_VALUE_TYPES else "text"
    # 用户可选择不生成示例：示例会进入模板字段并在后续提取提示中引用，
    # 基于样例文件生成的示例不是中性的，关闭后一律留空
    example = (field.example or "").strip()[:256] if with_examples else ""
    return TemplateDraftField(
        label=label,
        section=section,
        example=example,
        value_type=value_type,
    )


def generate_template_draft(
    settings: Settings,
    session: Session,
    *,
    image_paths: list[Path],
    requirement: str,
    with_examples: bool = True,
    model_client: ModelProvider | None = None,
    model_profile_id: str | None = None,
) -> TemplateDraft:
    requirement = requirement.strip()
    if not requirement:
        raise HTTPException(status_code=422, detail="请先填写需求描述。")
    if len(requirement) > MAX_REQUIREMENT_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"需求描述过长（上限 {MAX_REQUIREMENT_LENGTH} 字）。",
        )

    # AI 生成可独立选择模型方案（全局通用）；未指定时回退到任务激活方案
    if model_profile_id:
        active_settings = settings_for_model_profile(session, settings, model_profile_id)
    else:
        active_settings = settings_for_active_profile(session, settings)
    owns_client = model_client is None
    client = model_client or build_model_provider(
        active_settings,
        timeout_seconds=active_settings.model_timeout_seconds,
    )
    # 关闭示例时给模型一句明确提示（服务端仍会兜底清空，双保险）
    example_hint = "" if with_examples else "\n本模板不需要示例值：所有 example 一律输出空字符串。"
    try:
        if image_paths:
            prompt = (
                "请分析这些样例文件，结合下面的用户需求，列出需要从这类文件中提取的所有字段。\n"
                f"{OUTPUT_FORMAT_HINT}\n\n"
                f"用户需求：{requirement}"
                f"{example_hint}"
            )
            raw = client.extract_images(
                image_paths,
                prompt,
                AiGeneratedTemplate,
                system_prompt=SYSTEM_PROMPT,
            )
        else:
            # 未上传样例文件：仅凭需求描述设计字段结构，不编造需求未提到的内容
            prompt = (
                "没有提供样例文件。请仅根据用户需求描述，设计需要从这类文件中提取的字段结构，"
                "只列需求明确提到的字段，不要臆造。\n"
                f"{OUTPUT_FORMAT_HINT}\n\n"
                f"用户需求：{requirement}"
                f"{example_hint}"
            )
            raw = client.complete_text(
                prompt,
                AiGeneratedTemplate,
                system_prompt=SYSTEM_PROMPT,
            )
    except ModelServiceError as error:
        raise HTTPException(
            status_code=502,
            detail=f"AI 生成失败，请检查模型服务后重试。{error}",
        ) from error
    finally:
        if owns_client:
            client.close()

    fields = [
        cleaned
        for field in raw.fields
        if (cleaned := _clean_field(field, with_examples=with_examples)) is not None
    ]
    if not fields:
        raise HTTPException(
            status_code=422,
            detail="没有生成有效字段。请补充需求描述或上传样例文件后重试。",
        )
    fields = fields[:MAX_FIELDS]
    name = (raw.name or "").strip()[:128]
    description = (raw.description or "").strip()[:512]
    return TemplateDraft(name=name, description=description, fields=fields)
