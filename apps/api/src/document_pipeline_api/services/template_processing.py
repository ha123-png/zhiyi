from pathlib import Path
from types import UnionType

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from document_pipeline_api.model_providers import ModelProvider
from document_pipeline_api.schemas.templates import (
    TemplateFieldRead,
    TemplateMatchDecision,
    TemplateRead,
)


VALUE_TYPES: dict[str, type[str] | type[float] | type[bool]] = {
    "text": str,
    "number": float,
    "date": str,
    "boolean": bool,
}


def match_template(
    image_path: Path,
    templates: list[TemplateRead],
    model_client: ModelProvider,
) -> TemplateMatchDecision:
    if not templates:
        return TemplateMatchDecision(outcome="none", template_ids=[])
    candidate_text = "\n".join(
        f"- {template.id}: {template.name}｜{template.description or '没有补充说明'}"
        for template in templates
    )
    prompt = (
        "为这份文件选择用途最匹配的模板。只能依据下列模板名称和用途说明判断，"
        "不要提取字段，不要猜测。\n"
        "若只有一个明确候选，outcome=matched 且返回一个模板 ID；"
        "若有多个合理候选，outcome=ambiguous 且按匹配程度返回 2-3 个模板 ID；"
        "若都不合理，outcome=none 且返回空数组。\n"
        f"候选模板：\n{candidate_text}"
    )
    decision = model_client.extract_image(
        image_path,
        prompt,
        TemplateMatchDecision,
    )
    candidate_ids = {template.id for template in templates}
    if any(template_id not in candidate_ids for template_id in decision.template_ids):
        raise HTTPException(status_code=502, detail="模型返回了不存在的模板。")
    if decision.outcome == "matched" and len(decision.template_ids) != 1:
        raise HTTPException(status_code=502, detail="模型的模板匹配结果不完整。")
    if decision.outcome == "ambiguous" and len(decision.template_ids) < 2:
        raise HTTPException(status_code=502, detail="模型的模板候选不足。")
    if decision.outcome == "none" and decision.template_ids:
        raise HTTPException(status_code=502, detail="模型的模板匹配结果互相矛盾。")
    return decision


def build_template_extraction_model(template: TemplateRead) -> type[BaseModel]:
    header_fields = {
        field.key: _model_field(field)
        for field in template.fields
        if field.section == "header"
    }
    item_fields = {
        field.key: _model_field(field)
        for field in template.fields
        if field.section == "item"
    }
    header_model = create_model(
        "TemplateHeader",
        __config__=ConfigDict(extra="forbid"),
        **header_fields,
    )
    item_model = create_model(
        "TemplateItem",
        __config__=ConfigDict(extra="forbid"),
        **item_fields,
    )
    return create_model(
        "TemplateExtraction",
        __config__=ConfigDict(extra="forbid"),
        header=(header_model, ...),
        items=(list[item_model], ...),
    )


def build_template_extraction_prompt(template: TemplateRead) -> str:
    fields = "\n".join(
        (
            f"- {field.key}: {field.label}；"
            f"{'每份文件一次' if field.section == 'header' else '每条明细重复'}；"
            f"{field.instructions or '按文件中的通常含义理解'}；"
            f"示例：{field.example or '无'}"
        )
        for field in template.fields
    )
    return (
        f"按“{template.name}”模板整理这份文件。\n"
        f"模板用途：{template.description or '未补充'}\n"
        f"字段要求：\n{fields}\n"
        f"额外要求：{template.extra_instructions or '无'}\n"
        f"校验要求：{'；'.join(template.validation_rules) or '无'}\n"
        "header 保存每份文件一次的内容，items 保存逐条重复的明细；"
        "图片中没有的值使用 null，不猜测。"
    )


def _model_field(field: TemplateFieldRead) -> tuple[UnionType, FieldInfo]:
    value_type = VALUE_TYPES[field.value_type]
    description = field.label
    if field.instructions:
        description += f"。{field.instructions}"
    if field.example:
        description += f"。示例：{field.example}"
    return value_type | None, Field(description=description)
