"""One optional template; each file keeps its own schema and current data."""
import json
import math
import re

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from document_pipeline_api.models import TemplateRecord
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.extraction import TemplateExtraction
from document_pipeline_api.schemas.templates import TemplateBody, TemplateFieldRead, TemplateRead


AUTONOMOUS_TEMPLATE_ID = "builtin-autonomous"
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _model_number(value):
    # Only the model's declared number fields use this adapter. User edits keep
    # the strict current-data contract, and text identifiers never pass here.
    if not isinstance(value, str) or _JSON_NUMBER.fullmatch(value) is None:
        return value
    number = json.loads(value, parse_int=float)
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
            key = field.key
            if (not key or len(key) > 128 or key in keys or key.startswith('__')
                    or key in {'source_filename', 'item_index'} or any(char in key for char in '.[]\\"')):
                raise ValueError("每个字段必须有独立、有效的标识。")
            if not label or label in labels:
                raise ValueError("同一文件的字段名称不能重复或为空。")
            keys.add(key)
            labels.add(label)
        for field in self.fields:
            if field.value_type != 'number':
                continue
            rows = [self.header] if field.section == 'header' else self.items
            for row in rows:
                if field.key in row:
                    row[field.key] = _model_number(row[field.key])
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


def extraction_prompt(template: TemplateRead) -> str:
    shape = {
        "fields": [{"key": "f1", "label": "字段名称", "section": "header", "value_type": "text"}],
        "header": {"f1": "文件中实际内容"}, "items": [],
    }
    return (
        "阅读本次提供的文件，选择对理解和使用这份文件有意义的信息，并整理成结构化数据。\n"
        "fields 描述本文件的字段，使用 f1、f2 等简短且互不重复的 key，label 使用清楚的名称；"
        "section 为 header（整份文件一次）或 item（逐条明细），value_type 为 text、number、date 或 boolean。"
        "同一份文件内的字段名称和 key 必须各自唯一。\n"
        "header 仅使用文件字段的 key；items 每条仅使用明细字段的 key，按原件顺序保留全部已提供明细。"
        "没有明细时 items=[]。不编造信息，缺失值为 null；number 字段使用不带引号的 JSON 数字，"
        "不带单位或千分位分隔符；text 字段保持原文（包括编号中的前导零），日期为 YYYY-MM-DD，真假值使用 true/false。"
        "不要把整份原文塞进单个字段，不为解释增加虚构事实。\n"
        + (f"用户额外要求：{template.extra_instructions}\n" if template.extra_instructions else "")
        + "返回以下结构的 JSON，示例仅说明格式，按本文件内容决定实际字段："
        + json.dumps(shape, ensure_ascii=False)
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
