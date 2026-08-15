"""AI 生成模板草稿：样例文件 + 需求描述 → 激活模型方案 → 字段结构草稿。

草稿不落库；人工确认后走正常创建模板流程。
安全边界：AI 只能建议受控规则 DSL；后端逐条映射、校验，非法建议明确拒绝。
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
from document_pipeline_api.public_errors import public_error_message
from document_pipeline_api.schemas.rules import (
    BinaryExpression,
    EquationRule,
    EnumRule,
    FieldExpression,
    RangeRule,
    RequiredRule,
    SumExpression,
)
from document_pipeline_api.schemas.templates import (
    TemplateDraft,
    TemplateDraftField,
    TemplateDraftRuleSuggestion,
)
from document_pipeline_api.services.model_runtime import (
    settings_for_active_profile,
    settings_for_model_profile,
)


SUPPORTED_FILE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_FIELDS = 20
MAX_REQUIREMENT_LENGTH = 2000
ALLOWED_SECTIONS = {"header", "item"}
ALLOWED_VALUE_TYPES = {"text", "number", "date", "boolean"}
ALLOWED_RULE_KINDS = {"required", "range", "enum", "equation"}

# 固定系统提示词：与用户输入无关，保证模型始终按同一套规则输出字段结构。
SYSTEM_PROMPT = (
    "你是文档结构分析专家。你的任务：分析用户上传的样例文件（或根据用户需求描述）"
    "确定需要从这类文件中提取哪些字段，并只输出约定的 JSON。\n"
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
    "  ],\n"
    '  "rules": []\n'
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
    rules: list["AiGeneratedRule"] = Field(default_factory=list)


class AiGeneratedRule(BaseModel):
    """模型可输出的窄规则语言；所有字段均用可见中文字段名引用。"""

    model_config = ConfigDict(extra="ignore")

    kind: str | None = None
    field: str | None = None
    section: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    values: list[str | float | bool] = Field(default_factory=list)
    operation: str | None = None
    inputs: list[str] = Field(default_factory=list)
    input_section: str | None = None
    result_field: str | None = None
    result_section: str | None = None
    severity: str | None = None


def _clean_field(field: AiGeneratedField, *, with_examples: bool, index: int) -> TemplateDraftField | None:
    label = (field.label or "").strip()
    if not label or len(label) > 128:
        return None
    section = field.section if field.section in ALLOWED_SECTIONS else "header"
    value_type = field.value_type if field.value_type in ALLOWED_VALUE_TYPES else "text"
    # 用户可选择不生成示例：示例会进入模板字段并在后续提取提示中引用，
    # 基于样例文件生成的示例不是中性的，关闭后一律留空
    example = (field.example or "").strip()[:256] if with_examples else ""
    return TemplateDraftField(
        key=f"field_{index + 1}",
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
    with_rules: bool = False,
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
    rule_hint = _rule_generation_hint() if with_rules else "\nrules 必须输出空数组。"
    try:
        if image_paths:
            prompt = (
                "请分析这些样例文件，结合下面的用户需求，列出需要从这类文件中提取的所有字段。\n"
                f"{OUTPUT_FORMAT_HINT}\n\n"
                f"用户需求：{requirement}"
                f"{example_hint}{rule_hint}"
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
                f"{example_hint}{rule_hint}"
            )
            raw = client.complete_text(
                prompt,
                AiGeneratedTemplate,
                system_prompt=SYSTEM_PROMPT,
            )
    except ModelServiceError as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "AI 生成失败："
                + public_error_message(error, "请检查模型服务是否已连接后重试。")
            ),
        ) from error
    finally:
        if owns_client:
            client.close()

    fields = [
        cleaned
        for index, field in enumerate(raw.fields)
        if (cleaned := _clean_field(field, with_examples=with_examples, index=index)) is not None
    ]
    if not fields:
        raise HTTPException(
            status_code=422,
            detail="没有生成有效字段。请补充需求描述或上传样例文件后重试。",
        )
    fields = fields[:MAX_FIELDS]
    name = (raw.name or "").strip()[:128]
    description = (raw.description or "").strip()[:512]
    suggestions = _clean_rule_suggestions(raw.rules, fields) if with_rules else []
    return TemplateDraft(
        name=name,
        description=description,
        fields=fields,
        rule_suggestions=suggestions,
    )


def _rule_generation_hint() -> str:
    return (
        "\n请同时建议少量确定性校验规则，放入 rules。不要为了凑数生成规则。"
        "规则只能使用字段列表中完全相同的中文 label：\n"
        "- 必填：{kind:'required', field:'字段名', section:'header|item'}\n"
        "- 范围：{kind:'range', field:'字段名', section:'header|item', minimum:0, maximum:100}\n"
        "- 枚举：{kind:'enum', field:'字段名', section:'header|item', values:['值1','值2']}\n"
        "- 计算：{kind:'equation', operation:'multiply|add|sum_items', inputs:['字段1','字段2'], "
        "input_section:'item', result_field:'结果字段', result_section:'item|header'}。"
        "multiply/add 需要两个输入字段；sum_items 需要一个明细输入字段且结果必须是表头字段。"
        "禁止输出代码、正则、脚本、SQL 或自由表达式。"
    )


def _clean_rule_suggestions(
    raw_rules: list[AiGeneratedRule],
    fields: list[TemplateDraftField],
) -> list[TemplateDraftRuleSuggestion]:
    field_map = {(field.section, field.label.strip()): field for field in fields}
    suggestions: list[TemplateDraftRuleSuggestion] = []
    for raw in raw_rules[:20]:
        summary = _rule_summary(raw)
        try:
            rule = _convert_rule(raw, field_map)
            suggestions.append(
                TemplateDraftRuleSuggestion(
                    status="accepted",
                    summary=summary,
                    explanation=_rule_explanation(raw),
                    rule=rule,
                )
            )
        except ValueError as error:
            suggestions.append(
                TemplateDraftRuleSuggestion(
                    status="rejected",
                    summary=summary,
                    explanation="这条建议无法可靠执行，系统已阻止它进入模板。",
                    reason=str(error),
                )
            )
    return suggestions


def _field_path(
    field_map: dict[tuple[str, str], TemplateDraftField],
    section: str | None,
    label: str | None,
) -> tuple[str, TemplateDraftField]:
    normalized_section = "item" if section == "item" else "header"
    normalized_label = (label or "").strip()
    field = field_map.get((normalized_section, normalized_label))
    if field is None:
        raise ValueError(f"引用字段“{normalized_label or '（空）'}”不存在或出现方式不一致。")
    prefix = "items[]" if normalized_section == "item" else "header"
    return f"{prefix}.{field.key}", field


def _convert_rule(raw: AiGeneratedRule, field_map):
    if raw.kind not in ALLOWED_RULE_KINDS:
        raise ValueError("规则类型不受支持。")
    severity = raw.severity if raw.severity in {"warning", "error"} else "warning"
    if raw.kind in {"required", "range", "enum"}:
        path, field = _field_path(field_map, raw.section, raw.field)
        if raw.kind == "required":
            return RequiredRule(kind="required", field=path, severity=severity)
        if raw.kind == "range":
            if field.value_type != "number":
                raise ValueError(f"范围规则只能用于数字字段“{field.label}”。")
            if raw.minimum is None and raw.maximum is None:
                raise ValueError("范围规则缺少最小值和最大值。")
            return RangeRule(kind="range", field=path, minimum=raw.minimum, maximum=raw.maximum, severity=severity)
        if not raw.values:
            raise ValueError("枚举规则没有提供允许值。")
        return EnumRule(kind="enum", field=path, values=raw.values[:100], severity=severity)

    operation = raw.operation or ""
    if operation not in {"multiply", "add", "sum_items"}:
        raise ValueError("计算规则只支持相乘、相加或明细求和。")
    result_path, result = _field_path(field_map, raw.result_section, raw.result_field)
    if result.value_type != "number":
        raise ValueError(f"计算结果字段“{result.label}”必须是数字。")
    if operation == "sum_items":
        if len(raw.inputs) != 1:
            raise ValueError("明细求和规则必须且只能提供一个输入字段。")
        input_path, source = _field_path(field_map, "item", raw.inputs[0])
        if source.value_type != "number" or not result_path.startswith("header."):
            raise ValueError("明细求和必须从数字明细字段汇总到数字表头字段。")
        left = SumExpression(op="sum", path=input_path)
    else:
        if len(raw.inputs) != 2:
            raise ValueError("相乘或相加规则必须提供两个输入字段。")
        input_section = "item" if raw.input_section == "item" else "header"
        result_is_item = result_path.startswith("items[].")
        if (input_section == "item") != result_is_item:
            raise ValueError("逐行计算的输入字段和结果字段必须都属于每条明细。")
        first_path, first = _field_path(field_map, input_section, raw.inputs[0])
        second_path, second = _field_path(field_map, input_section, raw.inputs[1])
        if first.value_type != "number" or second.value_type != "number":
            raise ValueError("计算规则引用的输入字段必须都是数字。")
        left = BinaryExpression(
            op="multiply" if operation == "multiply" else "add",
            left=FieldExpression(op="field", path=first_path),
            right=FieldExpression(op="field", path=second_path),
        )
    return EquationRule(
        kind="equation",
        field=result_path,
        left=left,
        right=FieldExpression(op="field", path=result_path),
        severity=severity,
    )


def _rule_summary(raw: AiGeneratedRule) -> str:
    location = "每条明细的" if raw.section == "item" else "表头的"
    if raw.kind == "required":
        return f"{location}{raw.field or '未知字段'}不能为空"
    if raw.kind == "range":
        bounds = []
        if raw.minimum is not None:
            bounds.append(str(raw.minimum))
        if raw.maximum is not None:
            bounds.append(str(raw.maximum))
        interval = " ～ ".join(bounds) if bounds else "指定范围"
        return f"{location}{raw.field or '未知字段'}必须在 {interval} 内"
    if raw.kind == "enum":
        values = "、".join(str(value) for value in raw.values[:5]) or "指定值"
        return f"{location}{raw.field or '未知字段'}只能是：{values}"
    if raw.kind == "equation":
        if raw.operation == "sum_items":
            source = raw.inputs[0] if raw.inputs else "未知字段"
            return f"所有明细的{source}之和 = 表头的{raw.result_field or '计算结果'}"
        symbol = "×" if raw.operation == "multiply" else "+"
        left = f" {symbol} ".join(raw.inputs[:2]) or "未知计算"
        prefix = "每条明细：" if raw.input_section == "item" else "表头："
        return f"{prefix}{left} = {raw.result_field or '计算结果'}"
    return "无法识别的规则建议"


def _rule_explanation(raw: AiGeneratedRule) -> str:
    if raw.kind == "required":
        return "避免关键字段为空，导致后续查询、匹配或导出缺少依据。"
    if raw.kind == "range":
        return "发现超出合理范围的数字，交给用户确认，而不是直接相信模型结果。"
    if raw.kind == "enum":
        return "发现不在允许选项内的内容，减少名称漂移和分类错误。"
    if raw.kind == "equation" and raw.operation == "sum_items":
        return "核对明细汇总与表头合计是否一致，发现漏行或金额识别错误。"
    if raw.kind == "equation":
        return "核对字段之间的计算关系，发现数字识别或计算不一致。"
    return "系统无法确认这条建议的用途。"


AiGeneratedTemplate.model_rebuild()
