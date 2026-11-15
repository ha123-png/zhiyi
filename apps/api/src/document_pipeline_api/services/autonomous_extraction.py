"""One optional template; each file keeps its own schema and current data."""
from datetime import date
import json
import math
import re
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, WrapValidator, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.orm import Session

from document_pipeline_api.models import TemplateRecord
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.extraction import TemplateExtraction, TemplateScalar, ValidationIssue
from document_pipeline_api.schemas.json_numbers import JSONNumberToken, NumericPrecisionError, number_from_token
from document_pipeline_api.schemas.templates import TemplateBody, TemplateFieldRead, TemplateRead


AUTONOMOUS_TEMPLATE_ID = "builtin-autonomous"
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
# Sentence separators and spaces carry no date information. Match the whole
# value so ranges, timestamps and text surrounding the date remain reviewable.
_MODEL_DATE = re.compile(r"[\s,，.。;；:：、!?！？]*([0-9]{4}-[0-9]{2}-[0-9]{2})[\s,，.。;；:：、!?！？]*")
MODEL_FIELD_TYPE_ISSUE = "autonomous_field_type"
_DETAIL_PATH = re.compile(r"items\[(\d+)]\.(.+)")


def _model_number(value):
    # Only the model's declared number fields use this adapter. User edits keep
    # the strict current-data contract, and text identifiers never pass here.
    if not isinstance(value, str) or _JSON_NUMBER.fullmatch(value) is None:
        return value
    number = float(number_from_token(value))
    mantissa = re.split('[eE]', value, maxsplit=1)[0]
    if (not math.isfinite(number) or abs(number) > 2**53 - 1
            or (number == 0 and any(digit in mantissa for digit in '123456789'))):
        # Keep the float-based contract without accepting rounded large integers,
        # overflow or nonzero underflow through this additional string path.
        return value
    return number


def ensure_autonomous_template(session: Session) -> None:
    if session.get(TemplateRecord, AUTONOMOUS_TEMPLATE_ID) is not None:
        return
    from document_pipeline_api.services.templates import _version_record

    now = utc_now()
    body = TemplateBody(
        name="自主提取",
        description="由 AI 根据每份文件决定重要字段，适合临时或内容各异的文件；按文件查看和导出。",
        fields=[],
        behavior={"presentation": {"mode": "card"}},
    )
    session.add(TemplateRecord(
        id=AUTONOMOUS_TEMPLATE_ID, builtin_key="autonomous", is_system=True,
        is_active=True, in_smart_pool=False, current_version=1,
        created_at=now, updated_at=now,
    ))
    version = _version_record(AUTONOMOUS_TEMPLATE_ID, 1, body, now)
    version.engine_kind = "autonomous"
    session.add(version)
    session.commit()


def validate_settings(body: TemplateBody, current: TemplateRead) -> TemplateBody:
    if (body.fields or body.deterministic_rules or body.validation_rules or body.output_mapping
            or body.name != current.name or body.description != current.description
            or body.behavior.presentation != current.behavior.presentation):
        raise HTTPException(422, "自主提取的字段由每份文件决定，不能预设字段或固定规则；可修改额外要求、命名和归档设置。")
    return body


class AutonomousExtraction(TemplateExtraction):
    fields: list[TemplateFieldRead] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def check_contract(self):
        from document_pipeline_api.services.current_data import _check_values
        keys, labels = set(), set()
        for field in self.fields:
            label = " ".join(field.label.split()).casefold()
            scoped_label = (field.section, label)
            key = field.key
            if (not key or len(key) > 128 or key in keys or key.startswith('__')
                    or key in {'source_filename', 'item_index'} or any(char in key for char in '.[]\\"')):
                raise ValueError("每个字段必须有独立、有效的标识。")
            if not label or scoped_label in labels:
                raise PydanticCustomError("autonomous_field_label", "同一分区内的字段名称不能重复或为空。")
            keys.add(key)
            labels.add(scoped_label)
        for field in self.fields:
            if field.value_type != 'number':
                continue
            rows = [self.header] if field.section == 'header' else self.items
            for row in rows:
                if field.key in row:
                    row[field.key] = _model_number(row[field.key])
                    # Unsafe-integer strings are accepted at the user edit/API
                    # boundary to round-trip browser values losslessly. Model
                    # output has a different contract: a numeric string that
                    # cannot be safely adapted must not be reinterpreted as an
                    # exact Python int by the shared current-data validator.
                    if isinstance(row[field.key], str) and row[field.key] != '':
                        raise PydanticCustomError("autonomous_number_value", "数值字段必须填写数字。")
        schema = self.columns()
        try:
            _check_values(self.header, schema, 'header')
            for item in self.items:
                _check_values(item, schema, 'item')
        except HTTPException as error:
            raise ValueError(error.detail) from error
        return self

    def columns(self) -> list[dict]:
        return [{"key": field.key, "label": field.label, "section": field.section,
                 "value_type": field.value_type, "user_defined": False} for field in self.fields]


class AutonomousField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=128)
    value_type: Literal["text", "number", "date", "boolean"]


def _keep_model_number_token(value, validate):
    return value if isinstance(value, JSONNumberToken) else validate(value)


AutonomousScalar = Annotated[TemplateScalar, WrapValidator(_keep_model_number_token)]


class AutonomousHeaderField(AutonomousField):
    value: AutonomousScalar


class AutonomousModelResult(BaseModel):
    """The model describes columns and gives values in order. Only Zhiyi assigns keys."""

    model_config = ConfigDict(extra="forbid")

    header: list[AutonomousHeaderField] = Field(max_length=100)
    item_fields: list[AutonomousField] = Field(max_length=100)
    items: list[list[AutonomousScalar]]
    _extraction: AutonomousExtraction = PrivateAttr()
    _issues: list[ValidationIssue] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def build_extraction(self):
        if any(len(row) != len(self.item_fields) for row in self.items) or (self.items and not self.item_fields):
            raise PydanticCustomError("autonomous_row_width", "有明细的值数量与明细字段数量不一致。")
        header_keys = [f"h{index}" for index in range(1, len(self.header) + 1)]
        item_keys = [f"i{index}" for index in range(1, len(self.item_fields) + 1)]
        self._extraction = AutonomousExtraction(
            fields=[
                TemplateFieldRead(key=key, label=field.label, value_type=field.value_type, section=section)
                for section, keys, definitions in (("header", header_keys, self.header), ("item", item_keys, self.item_fields))
                for key, field in zip(keys, definitions, strict=True)
            ],
            header={
                key: _model_value(field, field.value, f"header.{key}", self._issues)
                for key, field in zip(header_keys, self.header, strict=True)
            },
            items=[
                {key: _model_value(field, value, f"items[{index}].{key}", self._issues)
                 for key, field, value in zip(item_keys, self.item_fields, row, strict=True)}
                for index, row in enumerate(self.items)
            ],
        )
        return self

    def extraction(self) -> AutonomousExtraction:
        return self._extraction

    def type_issues(self) -> list[ValidationIssue]:
        return self._issues


def _model_value(
    field: AutonomousField, value: TemplateScalar, path: str, issues: list[ValidationIssue],
) -> TemplateScalar:
    if value is None or value == "":
        return value
    kind = field.value_type
    exact_token = isinstance(value, JSONNumberToken)
    if kind == "date" and isinstance(value, str) and not exact_token:
        match = _MODEL_DATE.fullmatch(value)
        try:
            if match:
                return date.fromisoformat(match[1]).isoformat()
        except ValueError:
            pass
    elif kind == "number" and not exact_token:
        try:
            number = _model_number(value.strip() if isinstance(value, str) else value)
            if type(number) in (int, float) and math.isfinite(number):
                return number
        except (NumericPrecisionError, OverflowError, ValueError):
            pass
    elif kind == "text":
        if isinstance(value, str) or type(value) is int or (type(value) is float and math.isfinite(value)):
            return str(value)
    elif kind == "boolean" and isinstance(value, bool):
        return value
    labels = {"date": "日期", "number": "数字", "boolean": "是非值", "text": "文本"}
    message = ("模型返回的数字超出可保留的精度。请对照原件核对。" if exact_token
               else f"模型返回的内容无法确认为{labels[kind]}。请对照原件核对。")
    issues.append(ValidationIssue(
        code=MODEL_FIELD_TYPE_ISSUE, field=path, severity="warning",
        message=message,
        raw_value=str(value) if exact_token else value,
    ))
    return None


def carry_type_issues(
    prior: list[ValidationIssue], previous_ids: list[int], current_ids: list[int | None],
    result: TemplateExtraction,
) -> list[ValidationIssue]:
    """Keep unresolved fields with the same detail even if rows were deleted."""
    positions = {identity: index for index, identity in enumerate(current_ids) if identity is not None}
    remaining = []
    for issue in prior:
        if issue.code != MODEL_FIELD_TYPE_ISSUE:
            continue
        detail = _DETAIL_PATH.fullmatch(issue.field)
        if detail:
            previous, key = int(detail[1]), detail[2]
            index = positions.get(previous_ids[previous]) if previous < len(previous_ids) else None
            if index is not None and key in result.items[index] and result.items[index][key] is None:
                remaining.append(issue.model_copy(update={"field": f"items[{index}].{key}"}))
        elif issue.field.startswith("header."):
            key = issue.field.removeprefix("header.")
            if key in result.header and result.header[key] is None:
                remaining.append(issue)
    return remaining


def extraction_prompt(template: TemplateRead) -> str:
    return (
        "阅读本次提供的文件，选择对理解和使用这份文件有意义的信息，并整理成结构化数据。\n"
        "header 列出整份文件的字段，包括原文明确给出的汇总或结论，每个字段有 label、value_type 和 value。"
        "label 是字段名称，value 只填写原文中与该字段对应的完整值。\n"
        "item_fields 按列列出明细的 label 和 value_type。items 中每一行按相同列顺序填写值，"
        "与 item_fields 等长，按原件顺序保留全部已提供明细。没有明细时 item_fields 和 items 都用空数组。"
        "只把整份文件共用的信息放在 header，只在单条明细出现的值放在 items。"
        "每项事实只提取一次。同一分区内每个字段使用不同名称。"
        "跨分区名称相同且含义不同时，优先用名称体现各自的范围。"
        "value_type 只能是 text、number、date 或 boolean。"
        "只提取实际内容，缺失值保留为 null；number 字段使用不带引号的 JSON 数字，"
        "不带单位或千分位分隔符；text 字段保持原文（包括编号中的前导零），日期为 YYYY-MM-DD，真假值使用 true/false。"
        "内容含有区间、单位或不确定表述且无法无损规范时，按原文作为文本保留。"
        "不要把整份原文塞进单个字段，不为解释增加虚构事实。\n"
        + (f"用户额外要求：{template.extra_instructions}\n" if template.extra_instructions else "")
        + "只返回包含 header、item_fields 和 items 三个数组的 JSON 对象。\n以下是本次需要处理的文件内容："
    )


def frozen_pool(session: Session, task) -> list[TemplateRead]:
    from document_pipeline_api.services.templates import get_template_version, list_templates

    if task.template_pool_json is None:
        # Only legacy queued jobs lack a snapshot. Freeze before the first call;
        # retries use this same list, including its versions and optional fallback.
        task.template_pool_json = json.dumps([
            {"id": template.id, "version": template.version}
            for template in list_templates(session) if template.in_smart_pool
        ])
        session.flush()
    return [get_template_version(session, item['id'], item['version'])
            for item in json.loads(task.template_pool_json)]
