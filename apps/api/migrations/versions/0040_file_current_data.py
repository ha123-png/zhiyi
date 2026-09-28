"""Normalize current file headers and add durable file revision sessions."""

import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0040_file_current_data"
down_revision = "0039_original_archive"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    from document_pipeline_api.services.data_baseline_audit import audit_connection, split_legacy_result
    from document_pipeline_api.services.legacy_migration_choices import validate_choices
    from document_pipeline_api.services.legacy_fields import normalize_fields

    raw = connection.connection.driver_connection
    # Freeze legacy writers before comparing snapshots; migration must use the
    # same values that passed preflight, not a later unverified write.
    if not raw.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    previous_factory = raw.row_factory
    try:
        report = audit_connection(raw)
        choices = validate_choices(raw, report, op.get_context().config.attributes.get('v050_current_choices'))
    finally:
        raw.row_factory = previous_factory
    blockers = sum(issue["severity"] == "blocker" and issue.get('file_id') not in choices for issue in report["issues"])
    if blockers:
        raise RuntimeError(
            f"迁移预检发现 {blockers} 项旧数据歧义，升级已停止，未自动覆盖任何一份数据。"
            "请保留旧工作区与备份，在独立的干净工作区重新导入原文件；新版不提供旧事实冲突选择或双向同步。"
        )
    op.create_table(
        "file_current_data",
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("table_id", sa.String(36), sa.ForeignKey("data_tables.id"), nullable=True),
        sa.Column("header_json", sa.Text, nullable=False),
        sa.Column("schema_json", sa.Text, nullable=False),
        sa.Column("validation_json", sa.Text, nullable=False),
        sa.Column("has_data", sa.Boolean, nullable=False),
        sa.Column("current_version", sa.Integer, nullable=False),
        sa.Column("next_item_index", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_file_current_data_table_id", "file_current_data", ["table_id"])
    op.create_table(
        "data_edit_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("editor", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_data_edit_sessions_owner", "data_edit_sessions", ["owner"])
    op.create_table(
        "file_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("data_edit_sessions.id"), nullable=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("editor", sa.String(64), nullable=False),
        sa.Column("schema_json", sa.Text, nullable=False),
        sa.Column("changes_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_id", "version", name="uq_file_revision_version"),
    )
    op.create_index("ix_file_revisions_task_id", "file_revisions", ["task_id"])
    op.create_index("ix_file_revisions_session_id", "file_revisions", ["session_id"])
    op.create_table(
        "pending_original_deletions",
        sa.Column("relative_path", sa.String(1024), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("failure_code", sa.String(32), nullable=True),
    )
    op.create_index("ix_pending_original_deletions_task_id", "pending_original_deletions", ["task_id"])
    op.add_column("data_rows", sa.Column("is_header_only", sa.Boolean, nullable=False, server_default=sa.false()))
    # Deleted detail identities must never be reassigned to a newly added row.
    with op.batch_alter_table("data_rows", recreate="always", table_kwargs={"sqlite_autoincrement": True}):
        pass

    # Values were proven equal or explicitly selected against their fingerprint.
    # Keep the existing stable row IDs;
    # move each shared header to one parent without replaying old extractions.
    table_contracts = {}
    for extraction in connection.execute(sa.text("SELECT * FROM extractions")).mappings():
        task_id = extraction["task_id"]
        latest = connection.execute(sa.text(
            "SELECT * FROM review_revisions WHERE task_id=:task ORDER BY version DESC LIMIT 1"
        ), {"task": task_id}).mappings().first()
        source = latest or extraction
        header, items = split_legacy_result(source["result_json"], custom=extraction["document_kind"] == "custom")
        rows = connection.execute(sa.text(
            "SELECT * FROM data_rows WHERE task_id=:task ORDER BY item_index, id"
        ), {"task": task_id}).mappings().all()
        choice = choices.get(task_id)
        if not rows:
            if choice is None or choice['has_data'] or choice['header'] or choice['items']:
                raise RuntimeError('缺少旧数据行的文件必须明确选择保持为空；不能自动复活提取快照。')
            table_id, schema, aliases = None, [], {}
        else:
            table_id = rows[0]["table_id"]
            table = connection.execute(sa.text("SELECT * FROM data_tables WHERE id=:id"), {"id": table_id}).mappings().one()
            if table_id not in table_contracts:
                table_contracts[table_id] = normalize_fields(_legacy_schema(connection, table, header, items))
                if table_contracts[table_id][1] and connection.execute(sa.text(
                    'SELECT id FROM data_rows WHERE table_id=:id AND task_id IS NULL LIMIT 1'), {'id': table_id}).first():
                    raise RuntimeError('旧独立数据包含文件/明细同名字段，缺少可验证的原结构；请先核对其字段归属，未猜测拆分。')
                connection.execute(sa.text('UPDATE data_tables SET columns_json=:schema WHERE id=:id'),
                                   {'schema': json.dumps(table_contracts[table_id][0], ensure_ascii=False), 'id': table_id})
            schema, aliases = table_contracts[table_id]
        details = [{"id": row['id'], "item_index": row['item_index'], "values": {
            aliases.get(key, key): value for key, value in json.loads(row['row_json']).items()
            if (key not in header or key in aliases) and key not in {'source_filename', 'item_index', '__row_group'}
        } if items else {}} for row in rows]
        if choice is not None:
            _validate_selected_current(choice, rows, schema, header, details)
            header, details = choice['header'], choice['items']
        _validate_schema_coverage(header, details, schema)
        has_data = bool(rows)
        header_only = has_data and not items
        current_version = max([row['row_version'] for row in rows] + [latest['version'] if latest else 0]) + 1
        validation_json = source['validation_json']
        if choice is not None:
            validation_json = _selected_validation(connection, extraction, header, details, has_data, header_only)
            pending = any(not issue.get('ignored', False) for issue in json.loads(validation_json))
            connection.execute(sa.text('UPDATE data_rows SET review_pending=:pending WHERE task_id=:task'),
                               {'pending': pending, 'task': task_id})
            connection.execute(sa.text("UPDATE tasks SET status=:status WHERE id=:task AND status IN ('completed','needs_review')"),
                               {'status': 'needs_review' if pending else 'completed', 'task': task_id})
        schema_json = json.dumps(schema, ensure_ascii=False)
        changes = {"header": {key: {"before": None, "after": value} for key, value in header.items()}, "items": {}}
        for row in details:
            detail = row['values']
            connection.execute(sa.text("UPDATE data_rows SET row_json=:value, is_header_only=:header_only, row_version=:version WHERE id=:id"),
                               {"value": json.dumps(detail, ensure_ascii=False), "header_only": header_only, "version": current_version, "id": row["id"]})
            if not header_only:
                changes["items"][str(row["id"])] = {"before": None, "after": {
                    "id": row["id"], "item_index": row["item_index"], "values": detail,
                }}
        changes["presence"] = {"before": False, "after": has_data}
        if choice is not None:
            changes['migration_choice'] = {'fingerprint': choice['fingerprint'], 'note': '升级时经核对采用的当前数据；旧证据与旧修订原样保留。'}
        connection.execute(sa.text("""
            INSERT INTO file_current_data (task_id, table_id, header_json, schema_json, validation_json,
                has_data, current_version, next_item_index, updated_at)
            VALUES (:task, :table, :header, :schema, :validation, :has_data, :version, :next_item, CURRENT_TIMESTAMP)
        """), {"task": task_id, "table": table_id, "header": json.dumps(header, ensure_ascii=False),
               "schema": schema_json, "validation": validation_json,
               "has_data": has_data,
               "version": current_version,
               "next_item": max((row["item_index"] for row in rows), default=0) + 1})
        connection.execute(sa.text("""
            INSERT INTO file_revisions (id, task_id, session_id, version, operation, editor,
                schema_json, changes_json, created_at, sealed_at)
            VALUES (:id, :task, NULL, 1, 'migration', 'migration', :schema, :changes, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"id": str(uuid4()), "task": task_id, "schema": schema_json, "changes": json.dumps(changes, ensure_ascii=False)})

def _validate_schema_coverage(header, details, schema):
    keys = [column['key'] for column in schema]
    if len(keys) != len(set(keys)):
        raise RuntimeError('旧模板含有无法区分的重复字段，迁移已停止。')
    header_keys = {column['key'] for column in schema if column.get('section') == 'header'}
    item_keys = {column['key'] for column in schema if column.get('section') == 'item'}
    if header.keys() - header_keys or any(item['values'].keys() - item_keys for item in details):
        raise RuntimeError('旧模板结构不能完整表达现有文件数据，请先核对字段归属，未隐藏或丢弃字段。')


def _validate_selected_current(choice, rows, schema, legacy_header, legacy_details):
    from document_pipeline_api.services.legacy_migration_choices import validate_values
    if choice['has_data'] != bool(rows):
        raise RuntimeError('迁移选择不能隐式添加或删除旧数据行。')
    old_ids = {(row['id'], row['item_index']) for row in rows}
    selected = choice['items']
    if any(not isinstance(item, dict) or not isinstance(item.get('values'), dict) for item in selected):
        raise RuntimeError('迁移明细格式无效。')
    if len(selected) != len(rows) or {(item.get('id'), item.get('item_index')) for item in selected} != old_ids:
        raise RuntimeError('迁移选择必须逐一保留已有明细标识。')
    header_keys = {column['key'] for column in schema if column.get('section') == 'header'}
    item_keys = {column['key'] for column in schema if column.get('section') == 'item'}
    if choice['header'].keys() - header_keys or any(item['values'].keys() - item_keys for item in selected):
        raise RuntimeError('迁移选择含有未声明或文件/明细归属错误的字段。')
    validate_values(choice['header'], schema, unchanged=legacy_header)
    previous = {item['id']: item['values'] for item in legacy_details}
    for item in selected:
        validate_values(item['values'], schema, unchanged=previous.get(item['id']))
    # Reject non-JSON values before writing a durable current fact.
    json.dumps(choice, allow_nan=False)


def _selected_validation(connection, extraction, header, details, has_data, header_only):
    """Recheck the chosen facts against the saved rules, not today's template."""
    if not has_data:
        return '[]'
    from pydantic import TypeAdapter
    from document_pipeline_api.schemas.extraction import DocumentExtraction, DocumentKind, LineItem, TemplateExtraction
    from document_pipeline_api.schemas.templates import TemplateFieldRead
    from document_pipeline_api.schemas.rules import ValidationRule
    from document_pipeline_api.domain.validation import validate_extraction
    from document_pipeline_api.domain.template_rules import validate_template_rules
    from document_pipeline_api.services.legacy_fields import normalize_template_payload
    from document_pipeline_api.services.processing_input import input_scope_issues
    items = [] if header_only else [detail['values'] for detail in details]
    if extraction['document_kind'] == 'custom':
        version = connection.execute(sa.text('SELECT fields_json,deterministic_rules_json FROM template_versions '
            'WHERE template_id=:id AND version=:version'),
            {'id': extraction['template_id'], 'version': extraction['template_version']}).mappings().one()
        payload = normalize_template_payload({'fields': json.loads(version['fields_json']),
                                              'deterministic_rules': json.loads(version['deterministic_rules_json'])})
        issues = validate_template_rules(TemplateExtraction(header=header, items=items),
            TypeAdapter(list[ValidationRule]).validate_python(payload['deterministic_rules']),
            [TemplateFieldRead(**field) for field in payload['fields']])
    else:
        reverse = {'item_amount': 'amount', 'item_tax_amount': 'tax_amount', 'item_remarks': 'remarks'}
        result = {key: None for key in DocumentExtraction.model_fields if key != 'items'}
        result.update({key: value for key, value in header.items() if key in result})
        result['items'] = [{**{key: None for key in LineItem.model_fields},
                           **{reverse.get(key, key): value for key, value in item.items() if reverse.get(key, key) in LineItem.model_fields}}
                          for item in items]
        issues = validate_extraction(DocumentExtraction.model_validate(result), DocumentKind(extraction['document_kind']))
    issues += input_scope_issues(extraction['input_scope_json'])
    return json.dumps([issue.model_dump() for issue in issues], ensure_ascii=False)


def _legacy_schema(connection, table, header, items):
    """Frozen 0039 contract: migrations must not query future ORM columns."""
    aliases = {"amount": "item_amount", "tax_amount": "item_tax_amount", "remarks": "item_remarks"}
    builtin = table["document_kind"] != "custom"
    if table["columns_json"]:
        columns = json.loads(table["columns_json"])
    else:
        template_id = f"builtin-{table['document_kind']}" if builtin else table["template_key"]
        clause = "v.version=t.current_version" if builtin else "v.version=:version"
        version = connection.execute(sa.text(
            "SELECT v.fields_json, v.output_mapping_json FROM template_versions v JOIN templates t ON t.id=v.template_id "
            "WHERE t.id=:id AND " + clause), {"id": template_id, "version": table["template_version"]}).mappings().first()
        fields = json.loads(version["fields_json"]) if version else []
        mapping = json.loads(version["output_mapping_json"]) if version else {}
        columns = [{"key": aliases.get(field["key"], field["key"]) if builtin and field.get("section") == "item" else field["key"],
                    "label": mapping.get(field["key"], field["label"]), "section": field.get("section", "header"),
                    "value_type": field.get("value_type", "text")} for field in fields]
    if builtin:
        labels = {"document_type":"单据类型", "seller_name":"销售方", "buyer_name":"购买方", "document_number":"单号", "document_date":"日期",
                  "amount_before_tax":"不含税金额", "tax_amount":"整单税额", "total_amount":"价税合计", "total_quantity":"总数量",
                  "remarks":"备注", "name":"商品名称", "specification":"规格型号", "unit":"单位", "quantity":"数量", "unit_price":"单价",
                  "item_amount":"明细金额", "item_tax_amount":"明细税额", "item_remarks":"明细备注", "tax_rate":"税率"}
        numeric = {"amount_before_tax", "tax_amount", "total_amount", "total_quantity", "quantity", "unit_price", "item_amount", "item_tax_amount"}
        known = {column["key"] for column in columns}
        for section, keys in (("header", set(header)), ("item", {key for item in items for key in item})):
            for key in sorted(keys - known):
                columns.append({"key":key, "label":labels.get(key,key), "section":section, "value_type":"text"})
        for column in columns:
            if column["key"] in numeric:
                column["value_type"] = "number"
            elif column["key"] == "document_date":
                column["value_type"] = "date"
    return columns


def downgrade():
    # A schema downgrade cannot express edits made with the new file semantics.
    # Restore the automatically verified pre-upgrade backup instead.
    raise RuntimeError("请使用升级前完整备份恢复 v0.4.0，不能把新修订历史降级为旧的双事实源。")
