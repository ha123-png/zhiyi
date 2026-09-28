import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from document_pipeline_api.models import (
    TemplateRecord,
    TemplateRestorationRecord,
    TemplateVersionRecord,
)
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.templates import (
    TemplateBehavior,
    TemplateBody,
    TemplateRead,
    TemplateVersionSummary,
)


BUILTIN_TEMPLATES = (
    {
        "id": "builtin-invoice",
        "builtin_key": "invoice",
        "name": "发票",
        "description": "整理购销双方、票号、日期、金额、税额和商品明细。",
        "extra_instructions": "金额与税额按票面含义填写，不确定时留空。",
        "fields": [
            {"key": "seller_name", "label": "销售方", "example": "某某有限公司"},
            {"key": "buyer_name", "label": "购买方", "example": "某某商贸"},
            {"key": "document_number", "label": "发票号码", "example": "12345678"},
            {"key": "document_date", "label": "开票日期", "value_type": "date"},
            {"key": "amount_before_tax", "label": "不含税金额", "value_type": "number"},
            {"key": "tax_amount", "label": "总税额", "value_type": "number"},
            {"key": "total_amount", "label": "价税合计", "value_type": "number"},
            {"key": "name", "label": "商品名称", "section": "item"},
            {"key": "specification", "label": "规格型号", "section": "item"},
            {"key": "unit", "label": "单位", "section": "item"},
            {
                "key": "quantity",
                "label": "数量",
                "section": "item",
                "value_type": "number",
            },
            {
                "key": "unit_price",
                "label": "单价",
                "section": "item",
                "value_type": "number",
            },
            {
                "key": "amount",
                "label": "金额",
                "section": "item",
                "value_type": "number",
            },
            {"key": "tax_rate", "label": "税率", "section": "item"},
            {
                "key": "tax_amount",
                "label": "税额",
                "section": "item",
                "value_type": "number",
            },
        ],
        "validation_rules": [
            "数量乘以单价应等于明细金额",
            "各明细金额与税额之和应等于价税合计",
        ],
        "deterministic_rules": [
            *[
                {
                    "kind": "required",
                    "field": f"header.{key}",
                    "severity": "warning",
                }
                for key in (
                    "seller_name",
                    "buyer_name",
                    "document_number",
                    "document_date",
                    "total_amount",
                )
            ],
            {
                "kind": "equation",
                "field": "items[].amount",
                "left": {
                    "op": "multiply",
                    "left": {"op": "field", "path": "items[].quantity"},
                    "right": {"op": "field", "path": "items[].unit_price"},
                },
                "right": {"op": "field", "path": "items[].amount"},
            },
            {
                "kind": "equation", "field": "header.amount_before_tax",
                "left": {"op": "sum", "path": "items[].amount"},
                "right": {"op": "field", "path": "header.amount_before_tax"},
            },
            {
                "kind": "equation", "field": "header.total_amount",
                "left": {"op": "add", "left": {"op": "field", "path": "header.amount_before_tax"},
                         "right": {"op": "field", "path": "header.tax_amount"}},
                "right": {"op": "field", "path": "header.total_amount"},
            },
        ],
        "output_mapping": {},
    },
    {
        "id": "builtin-delivery",
        "builtin_key": "delivery",
        "name": "送货单",
        "description": "整理供货方、收货方、单号、日期、合计和货品明细。",
        "extra_instructions": "手写修改优先于印刷内容，不确定时留空。",
        "fields": [
            {"key": "seller_name", "label": "供货方", "example": "某某供应商"},
            {"key": "buyer_name", "label": "收货方", "example": "某某门店"},
            {"key": "document_number", "label": "送货单号", "example": "SH-001"},
            {"key": "document_date", "label": "送货日期", "value_type": "date"},
            {"key": "total_amount", "label": "合计金额", "value_type": "number"},
            {"key": "name", "label": "货品名称", "section": "item"},
            {"key": "specification", "label": "规格", "section": "item"},
            {"key": "unit", "label": "单位", "section": "item"},
            {
                "key": "quantity",
                "label": "数量",
                "section": "item",
                "value_type": "number",
            },
            {
                "key": "unit_price",
                "label": "单价",
                "section": "item",
                "value_type": "number",
            },
            {
                "key": "amount",
                "label": "金额",
                "section": "item",
                "value_type": "number",
            },
        ],
        "validation_rules": [],
        "deterministic_rules": [
            *[{"kind": "required", "field": f"header.{key}", "severity": "warning"}
              for key in ("seller_name", "buyer_name", "document_number", "document_date", "total_amount")],
            {
                "kind": "equation", "field": "items[].amount",
                "left": {"op": "multiply", "left": {"op": "field", "path": "items[].quantity"},
                         "right": {"op": "field", "path": "items[].unit_price"}},
                "right": {"op": "field", "path": "items[].amount"},
            },
            {
                "kind": "equation", "field": "header.total_amount",
                "left": {"op": "sum", "path": "items[].amount"},
                "right": {"op": "field", "path": "header.total_amount"},
            },
        ],
        "output_mapping": {},
    },
)


def _ordinary_starter_body(body: TemplateBody) -> TemplateBody:
    """Normalize legacy starter field identities once, preserving old versions."""
    aliases = {"amount": "item_amount", "tax_amount": "item_tax_amount", "remarks": "item_remarks"}
    def references(value):
        if isinstance(value, dict):
            return {key: references(item) for key, item in value.items()}
        if isinstance(value, list):
            return [references(item) for item in value]
        if isinstance(value, str):
            for prefix in ('items[].', 'item.'):
                if value.startswith(prefix) and value[len(prefix):] in aliases:
                    return prefix + aliases[value[len(prefix):]]
        return value
    values = references(body.model_dump(mode='json'))
    for field in values['fields']:
        if field['section'] == 'item':
            old_key = field['key']
            field['key'] = aliases.get(old_key, old_key)
            if old_key in values['output_mapping']:
                values['output_mapping'][field['key']] = values['output_mapping'][old_key]
    return _normalize_body(TemplateBody.model_validate(values))


def ensure_builtin_templates(session: Session) -> None:
    from document_pipeline_api.services.system_settings import get_bool_setting, set_bool_setting
    from document_pipeline_api.services.autonomous_extraction import ensure_autonomous_template
    ensure_autonomous_template(session)
    if get_bool_setting(session, 'starter_templates_initialized', False):
        return
    for definition in BUILTIN_TEMPLATES:
        if session.get(TemplateRecord, definition['id']) is not None:
            continue
        body = _ordinary_starter_body(TemplateBody.model_validate({
            key:value for key,value in definition.items() if key not in {'id','builtin_key'}
        }))
        now = utc_now()
        template = TemplateRecord(id=definition['id'], builtin_key=definition['builtin_key'],
            is_system=False, is_active=True, is_deleted=False, in_smart_pool=True,
            current_version=1, created_at=now, updated_at=now)
        session.add(template)
        session.add(_version_record(template.id, 1, body, now))
    set_bool_setting(session, 'starter_templates_initialized', True)
    session.commit()


def list_templates(
    session: Session,
    *,
    include_inactive: bool = False,
) -> list[TemplateRead]:
    statement = (
        select(TemplateRecord, TemplateVersionRecord)
        .join(
            TemplateVersionRecord,
            (TemplateVersionRecord.template_id == TemplateRecord.id)
            & (TemplateVersionRecord.version == TemplateRecord.current_version),
        )
        .order_by(
            TemplateRecord.is_active.desc(),
            TemplateRecord.is_system.desc(),
            TemplateRecord.created_at,
        )
    )
    statement = statement.where(TemplateRecord.is_deleted.is_(False))
    if not include_inactive:
        statement = statement.where(TemplateRecord.is_active.is_(True))
    records = session.execute(statement).all()
    return [_to_read(template, version) for template, version in records]


def get_template(session: Session, template_id: str) -> TemplateRead:
    template = session.get(TemplateRecord, template_id)
    if template is None or template.is_deleted:
        raise HTTPException(status_code=404, detail="没有找到这个模板。")
    version = session.scalar(
        select(TemplateVersionRecord).where(
            TemplateVersionRecord.template_id == template.id,
            TemplateVersionRecord.version == template.current_version,
        )
    )
    if version is None:
        raise RuntimeError("模板当前版本不存在。")
    return _to_read(template, version)


def get_active_template(session: Session, template_id: str) -> TemplateRead:
    template = get_template(session, template_id)
    if not template.is_active:
        raise HTTPException(status_code=409, detail="这个模板已停用，请先恢复后再使用。")
    return template


def get_template_version(
    session: Session,
    template_id: str,
    version_number: int,
) -> TemplateRead:
    template = session.get(TemplateRecord, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="没有找到这个模板。")
    version = session.scalar(
        select(TemplateVersionRecord).where(
            TemplateVersionRecord.template_id == template.id,
            TemplateVersionRecord.version == version_number,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="没有找到这个模板版本。")
    return _to_read(template, version)


def list_template_versions(session: Session, template_id: str, before: int | None = None) -> list[TemplateVersionSummary]:
    if session.get(TemplateRecord, template_id) is None:
        raise HTTPException(status_code=404, detail="没有找到这个模板。")
    statement = select(TemplateVersionRecord).where(TemplateVersionRecord.template_id == template_id)
    if before is not None:
        statement = statement.where(TemplateVersionRecord.version < before)
    return [TemplateVersionSummary(version=v.version, name=v.name, created_at=v.created_at,
                                   field_count=len(json.loads(v.fields_json)))
            for v in session.scalars(statement.order_by(TemplateVersionRecord.version.desc()).limit(50))]


def list_template_restorations(session: Session, template_id: str):
    get_template(session, template_id)
    return session.scalars(select(TemplateRestorationRecord).where(
        TemplateRestorationRecord.template_id == template_id,
    ).order_by(TemplateRestorationRecord.id.desc()).limit(20)).all()


def _check_template_timestamp(session: Session, template: TemplateRecord, expected: datetime | None) -> None:
    if expected is None:
        from document_pipeline_api.services.system_settings import get_setting
        if get_setting(session, "restored_template_write_fence"):
            raise HTTPException(status_code=409, detail="数据已从备份恢复，请刷新模板并携带最新 updated_at 后重试。")
        return  # Older API clients still use expected_version.
    def utc_naive(value: datetime) -> datetime:
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if utc_naive(template.updated_at) != utc_naive(expected):
        raise HTTPException(status_code=409, detail="模板已经更新，请刷新后重试。")


def _switch_current_version(session: Session, template: TemplateRecord, version: int, now: datetime) -> None:
    # Check again in SQL: two restores must not both succeed from the same state.
    result = session.execute(update(TemplateRecord).where(
        TemplateRecord.id == template.id,
        TemplateRecord.current_version == template.current_version,
        TemplateRecord.updated_at == template.updated_at,
    ).values(current_version=version, updated_at=now).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="模板已经更新，请刷新后重试。")
    session.refresh(template)


def restore_template_version(session: Session, template_id: str, version: int, expected_version: int,
                             expected_updated_at: datetime | None = None) -> TemplateRead:
    current = get_template(session, template_id)
    template = session.get(TemplateRecord, template_id)
    if current.is_system and current.engine_kind != "autonomous":
        raise HTTPException(status_code=409, detail="内置模板不能直接修改，请先复制。")
    if not current.is_active:
        raise HTTPException(status_code=409, detail="这个模板已停用，请先恢复后再编辑。")
    if current.version != expected_version:
        raise HTTPException(status_code=409, detail="模板已经更新，请关闭历史并刷新模板后重试。")
    _check_template_timestamp(session, template, expected_updated_at)
    source = get_template_version(session, template_id, version)
    if version == current.version:
        raise HTTPException(status_code=409, detail="所选版本已经是当前版本。")
    _ensure_unique_name(session, source.name, exclude_id=template_id)
    body = TemplateBody.model_validate(source.model_dump(include=set(TemplateBody.model_fields)))
    if source.engine_kind == 'legacy':
        return update_template(session,template_id,expected_version,_ordinary_starter_body(body),expected_updated_at)
    if current.engine_kind == "autonomous":
        from document_pipeline_api.services.autonomous_extraction import validate_settings
        validate_settings(body, current)
    else:
        body = _normalize_body(body)
        from document_pipeline_api.services.template_contract import sync_template_contract
        sync_template_contract(session,template_id,body)
    now = utc_now()
    _switch_current_version(session, template, version, now)
    session.add(TemplateRestorationRecord(template_id=template_id, from_version=current.version,
                                          to_version=version, created_at=now))
    _commit_or_conflict(session)
    return get_template(session, template_id)


def _active_name_exists(
    session: Session,
    name: str,
    exclude_id: str | None = None,
) -> bool:
    """启用中的模板是否已存在同名（停用模板不参与去重，内置模板名同样保护）。"""
    statement = (
        select(TemplateVersionRecord.id)
        .join(
            TemplateRecord,
            TemplateRecord.id == TemplateVersionRecord.template_id,
        )
        .where(
            TemplateRecord.is_active.is_(True),
            TemplateVersionRecord.version == TemplateRecord.current_version,
            TemplateVersionRecord.name == name,
        )
    )
    if exclude_id is not None:
        statement = statement.where(TemplateRecord.id != exclude_id)
    return session.scalar(statement) is not None


def _ensure_unique_name(session: Session, name: str, exclude_id: str | None = None) -> None:
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    if _active_name_exists(session, name, exclude_id):
        raise HTTPException(
            status_code=422,
            detail=f"已存在同名模板“{name}”，请换个名字。",
        )


def _unused_copy_name(session: Session, base_name: str) -> str:
    """复制时自动找不冲突的名字：xxx 副本 → xxx 副本2 → xxx 副本3…"""
    name = f"{base_name} 副本"
    suffix = 2
    while _active_name_exists(session, name):
        name = f"{base_name} 副本{suffix}"
        suffix += 1
    return name


def create_template(session: Session, body: TemplateBody) -> TemplateRead:
    _ensure_unique_name(session, body.name)
    now = utc_now()
    template = TemplateRecord(
        id=str(uuid4()),
        is_system=False,
        is_active=True,
        in_smart_pool=False,
        current_version=1,
        created_at=now,
        updated_at=now,
    )
    normalized = _normalize_body(body)
    session.add(template)
    session.add(_version_record(template.id, 1, normalized, now))
    _commit_or_conflict(session)
    return _to_read(template, _current_version(session, template))


def copy_template(session: Session, template_id: str) -> TemplateRead:
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    source = get_active_template(session, template_id)
    if source.engine_kind == "autonomous":
        raise HTTPException(409, "自主提取的设置可以直接修改，无需复制。")
    body = TemplateBody.model_validate(
        {
            **source.model_dump(
                include={
                    "description",
                    "extra_instructions",
                    "fields",
                    "validation_rules",
                    "deterministic_rules",
                    "output_mapping",
                    "behavior",
                }
            ),
            "name": _unused_copy_name(session, source.name),
        }
    )
    now = utc_now()
    template = TemplateRecord(
        id=str(uuid4()),
        is_system=False,
        is_active=True,
        in_smart_pool=False,
        current_version=1,
        source_template_id=template_id,
        created_at=now,
        updated_at=now,
    )
    session.add(template)
    version = _version_record(template.id, 1, _normalize_body(body), now)
    session.add(version)
    _commit_or_conflict(session)
    return _to_read(template, version)


def update_template(
    session: Session,
    template_id: str,
    expected_version: int,
    body: TemplateBody,
    expected_updated_at: datetime | None = None,
) -> TemplateRead:
    template = session.get(TemplateRecord, template_id)
    if template is None or template.is_deleted:
        raise HTTPException(status_code=404, detail="没有找到这个模板。")
    if template.is_system and _current_version(session, template).engine_kind != "autonomous":
        raise HTTPException(status_code=409, detail="内置模板不能直接修改，请先复制。")
    if not template.is_active:
        raise HTTPException(status_code=409, detail="这个模板已停用，请先恢复后再编辑。")
    if template.current_version != expected_version:
        raise HTTPException(
            status_code=409,
            detail="模板已经更新，请刷新后再保存。",
        )
    _check_template_timestamp(session, template, expected_updated_at)
    _ensure_unique_name(session, body.name, exclude_id=template_id)
    now = utc_now()
    next_version = (session.scalar(select(func.max(TemplateVersionRecord.version)).where(
        TemplateVersionRecord.template_id == template_id,
    )) or 0) + 1
    current = _to_read(template, _current_version(session, template))
    if current.engine_kind == "autonomous":
        from document_pipeline_api.services.autonomous_extraction import validate_settings
        normalized = validate_settings(body, current)
    else:
        normalized = _normalize_body(body)
        from document_pipeline_api.services.template_contract import sync_template_contract
        sync_template_contract(session,template_id,normalized)
    version = _version_record(
        template.id,
        next_version,
        normalized,
        now,
    )
    version.engine_kind = "autonomous" if current.engine_kind == "autonomous" else "template"
    _switch_current_version(session, template, next_version, now)
    session.add(version)
    _commit_or_conflict(session)
    return _to_read(template, version)


def archive_template(session: Session, template_id: str) -> TemplateRead:
    template = session.get(TemplateRecord, template_id)
    if template is None or template.is_deleted:
        raise HTTPException(status_code=404, detail="没有找到这个模板。")
    if template.is_system:
        raise HTTPException(status_code=409, detail="内置模板不能停用。")
    if not template.is_active:
        return get_template(session, template_id)
    template.is_active = False
    template.updated_at = utc_now()
    session.commit()
    return get_template(session, template_id)


def restore_template(session: Session, template_id: str) -> TemplateRead:
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    template = session.get(TemplateRecord, template_id)
    if template is None or template.is_deleted:
        raise HTTPException(status_code=404, detail="没有找到这个模板。")
    if template.is_active:
        return get_template(session, template_id)
    _ensure_unique_name(session, _current_version(session, template).name, exclude_id=template_id)
    template.is_active = True
    template.updated_at = utc_now()
    session.commit()
    return get_template(session, template_id)


def delete_template(session: Session, template_id: str) -> None:
    """Remove a rule from use; retain immutable snapshots for existing files."""
    template = session.get(TemplateRecord, template_id)
    if template is None or template.is_deleted:
        raise HTTPException(404, "没有找到这个模板。")
    if template.is_system:
        raise HTTPException(409, "自主提取是知意的固定入口，不能删除；可将它移出智能选择池。")
    template.is_deleted = True
    template.is_active = False
    template.in_smart_pool = False
    template.updated_at = utc_now()
    session.commit()


def _normalize_body(body: TemplateBody) -> TemplateBody:
    if not body.fields:
        raise HTTPException(422, "普通模板至少需要一个字段。")
    parts = [body.extra_instructions] if body.extra_instructions else []
    existing = {line.strip() for line in body.extra_instructions.splitlines()}
    for hint in body.validation_rules:
        if hint.strip() and hint.strip() not in existing and f"\n{hint.strip()}\n" not in f"\n{body.extra_instructions.strip()}\n":
            parts.append(hint)
            existing.add(hint.strip())
    fields = []
    # 表格最终是扁平列结构；无论抬头/明细，用户可见列名都必须唯一，避免建表、
    # 导出和合并时两个同名字段互相覆盖。内部 key 在整份模板中必须唯一。
    keys: set[str] = set()
    labels: set[str] = set()
    for field in body.fields:
        section = field.section or "header"
        key = field.key or f"field_{uuid4().hex[:8]}"
        label_identity = " ".join(field.label.split()).casefold()
        if label_identity in labels:
            raise HTTPException(status_code=422, detail=f"字段名“{field.label.strip()}”重复。")
        if key in keys:
            raise HTTPException(status_code=422, detail=f"字段“{field.label}”与其他字段使用了相同标识。文件字段和明细字段必须各有独立标识。")
        if key.startswith('__') or key in {'source_filename', 'item_index'} or any(char in key for char in '.[]\\"'):
            raise HTTPException(422, "字段标识包含保留名称或路径字符，请重新创建该字段。")
        keys.add(key)
        labels.add(label_identity)
        fields.append(field.model_copy(update={"key": key}))
    output_mapping = {
        key: value
        for key, value in body.output_mapping.items()
        if key in keys and value.strip()
    }
    display_names: set[str] = set()
    for field in fields:
        display_name = output_mapping.get(field.key, field.label)
        identity = " ".join(display_name.split()).casefold()
        if identity in display_names:
            raise HTTPException(422, f"输出列名“{display_name.strip()}”重复，请修改字段名称或输出映射。")
        display_names.add(identity)
    field_types = {
        (field.section or "header", field.key): field.value_type
        for field in fields
    }
    for rule in body.deterministic_rules:
        for path in _rule_paths(rule):
            section, key = path.split(".", 1)
            field_type = field_types.get(
                ("item" if section == "items[]" else "header", key)
            )
            if field_type is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"检查规则引用了不存在的字段“{path}”。",
                )
            if _path_requires_number(rule, path) and field_type != "number":
                raise HTTPException(
                    status_code=422,
                    detail=f"检查规则要求“{path}”是数字字段。",
                )
    presentation = body.behavior.presentation
    valid_references = {f"{field.section}.{field.key}" for field in fields}
    references = [
        *presentation.primary_fields,
        *presentation.collapsed_fields,
        *([presentation.title_field] if presentation.title_field else []),
    ]
    for reference in references:
        if reference not in valid_references:
            raise HTTPException(
                status_code=422,
                detail=f"卡片设置引用了不存在的字段“{reference}”，请重新选择。",
            )
    return body.model_copy(
        update={
            "fields": fields,
            "output_mapping": output_mapping,
            "extra_instructions": "\n".join(parts),
            "validation_rules": [],
        }
    )


def _version_record(
    template_id: str,
    version: int,
    body: TemplateBody,
    created_at,
) -> TemplateVersionRecord:
    return TemplateVersionRecord(
        template_id=template_id,
        version=version,
        engine_kind="template",
        name=body.name,
        description=body.description,
        extra_instructions=body.extra_instructions,
        fields_json=json.dumps(
            [field.model_dump() for field in body.fields],
            ensure_ascii=False,
        ),
        validation_rules_json=json.dumps(body.validation_rules, ensure_ascii=False),
        deterministic_rules_json=json.dumps(
            [rule.model_dump(mode="json") for rule in body.deterministic_rules],
            ensure_ascii=False,
        ),
        output_mapping_json=json.dumps(body.output_mapping, ensure_ascii=False),
        behavior_json=body.behavior.model_dump_json(),
        created_at=created_at,
    )


def _current_version(
    session: Session,
    template: TemplateRecord,
) -> TemplateVersionRecord:
    version = session.scalar(
        select(TemplateVersionRecord).where(
            TemplateVersionRecord.template_id == template.id,
            TemplateVersionRecord.version == template.current_version,
        )
    )
    if version is None:
        raise RuntimeError("模板当前版本不存在。")
    return version


def _version_matches(version: TemplateVersionRecord, body: TemplateBody) -> bool:
    return (
        version.name == body.name
        and version.description == body.description
        and version.extra_instructions == body.extra_instructions
        and json.loads(version.fields_json)
        == [field.model_dump() for field in body.fields]
        and json.loads(version.validation_rules_json) == body.validation_rules
        and json.loads(version.deterministic_rules_json)
        == [rule.model_dump(mode="json") for rule in body.deterministic_rules]
        and json.loads(version.output_mapping_json) == body.output_mapping
        and TemplateBehavior.model_validate_json(version.behavior_json) == body.behavior
    )


def _to_read(
    template: TemplateRecord,
    version: TemplateVersionRecord,
) -> TemplateRead:
    from document_pipeline_api.services.legacy_fields import normalize_template_payload
    payload = dict(fields=json.loads(version.fields_json),
                   deterministic_rules=json.loads(version.deterministic_rules_json),
                   output_mapping=json.loads(version.output_mapping_json),
                   behavior=json.loads(version.behavior_json))
    if version.engine_kind != "legacy":
        payload = normalize_template_payload(payload)
    return TemplateRead(
        id=template.id,
        version=version.version,
        engine_kind=version.engine_kind,
        is_deleted=template.is_deleted,
        is_system=template.is_system,
        is_active=template.is_active,
        in_smart_pool=template.in_smart_pool,
        builtin_key=template.builtin_key,
        source_template_id=template.source_template_id,
        name=version.name,
        description=version.description,
        extra_instructions=version.extra_instructions,
        **payload,
        validation_rules=json.loads(version.validation_rules_json),
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def set_smart_pool(session: Session, template_id: str, in_pool: bool) -> TemplateRead:
    """更新模板是否参与智能匹配预选池（停用/内置模板同样适用）。"""
    template = session.get(TemplateRecord, template_id)
    if template is None or template.is_deleted:
        raise HTTPException(status_code=404, detail="没有找到这个模板。")
    template.in_smart_pool = in_pool
    template.updated_at = utc_now()
    session.commit()
    return _to_read(template, _current_version(session, template))


def _rule_paths(rule) -> set[str]:
    from document_pipeline_api.schemas.rules import (
        BinaryExpression,
        EquationRule,
        FieldExpression,
        SumExpression,
    )

    paths = {rule.field}
    if not isinstance(rule, EquationRule):
        return paths

    def expression_paths(expression) -> set[str]:
        if isinstance(expression, (FieldExpression, SumExpression)):
            return {expression.path}
        if isinstance(expression, BinaryExpression):
            return expression_paths(expression.left) | expression_paths(expression.right)
        return set()

    return paths | expression_paths(rule.left) | expression_paths(rule.right)


def _path_requires_number(rule, path: str) -> bool:
    from document_pipeline_api.schemas.rules import EquationRule, RangeRule

    return isinstance(rule, (EquationRule, RangeRule))


def _commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="模板保存冲突，请刷新后重试。") from error
