from pathlib import Path
import json
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
    image_path: Path | None,
    templates: list[TemplateRead],
    model_client: ModelProvider,
    *,
    image_paths: list[Path] | None = None,
    text_input: str = "",
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
        '仅返回两个键的 JSON 对象，格式为 {"outcome":"none","template_ids":[]}。'
        "outcome 只能是 matched、ambiguous 或 none；template_ids 必须是互不重复的候选 ID 字符串数组，"
        "不要把模板名称当作 ID，不添加理由、置信度或其他字段。\n"
        f"候选模板：\n{candidate_text}"
    )
    prompt += "\n本次文件输入（仅据此判断）：\n" + text_input
    if image_paths is not None and len(image_paths) > 1:
        decision = model_client.extract_images(image_paths, prompt, TemplateMatchDecision)
    elif image_path is not None:
        decision = model_client.extract_image(image_path, prompt, TemplateMatchDecision)
    else:
        decision = model_client.complete_text(prompt, TemplateMatchDecision)
    candidate_ids = {template.id for template in templates}
    if len(set(decision.template_ids)) != len(decision.template_ids):
        raise HTTPException(status_code=502, detail="模型返回了重复的模板候选。")
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


TEMPLATE_PROMPT_VERSION = "template-extraction-v2"


def build_template_extraction_prompt(template: TemplateRead, *, include_filename: bool = False) -> str:
    header_shape = {field.key: None for field in template.fields if field.section == "header"}
    item_shape = {field.key: None for field in template.fields if field.section == "item"}
    shape = {"header": header_shape, "items": [item_shape] if item_shape else []}
    if include_filename:
        shape["file_name_advice"] = {"rename": False, "name": None}
    output_shape = json.dumps(shape, ensure_ascii=False)
    fields = "\n".join(
        f"- {field.key}: {field.label}；"
        f"{'每份文件一次' if field.section == 'header' else '每条明细重复'}；类型 {field.value_type}"
        + (f"；{field.instructions}" if field.instructions else "")
        + (f"；示例：{field.example}" if field.example else "")
        for field in template.fields
    )
    optional = []
    if template.description:
        optional.append(f"模板用途：{template.description}")
    if template.extra_instructions:
        optional.append(f"额外要求：{template.extra_instructions}")
    if template.validation_rules:
        optional.append(f"校验要求：{'；'.join(template.validation_rules)}")
    return (
        f"按“{template.name}”模板整理这份文件。\n"
        + ("\n".join(optional) + "\n" if optional else "")
        + f"字段要求：\n{fields}\n"
        + "header 保存每份文件一次的内容，items 保存逐条重复的明细；"
        "提供内容中没有的值使用 null，不猜测；实际值缺失且原件仅以“未填写”等字样占位时也使用 null，"
        "不要把占位说明当作人名、日期或金额。表示状态或备注的真实文字按字段要求保留。"
        "按原件顺序保留全部已提供明细，不合并相似行。\n"
        f"必须返回这个 JSON 结构，字段值以实际内容替换，不添加其他键：{output_shape}\n"
        + ("该模板未定义明细字段，items 必须是空数组，不要自行生成记录。" if not item_shape else "items 中每条记录都只使用上述明细字段。")
    )


def _model_field(field: TemplateFieldRead) -> tuple[UnionType, FieldInfo]:
    value_type = VALUE_TYPES[field.value_type]
    description = field.label
    if field.instructions:
        description += f"。{field.instructions}"
    if field.example:
        description += f"。示例：{field.example}"
    return value_type | None, Field(description=description)
