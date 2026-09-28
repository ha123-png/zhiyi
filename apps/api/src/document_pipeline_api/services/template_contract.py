"""Template versions change instructions, not the destination table identity."""

import json
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from document_pipeline_api.models import (
    DataRowRecord,
    DataTableRecord,
    FileCurrentDataRecord,
    TaskRecord,
    TemplateRecord,
    TemplateTableBindingRecord,
)
from document_pipeline_api.services.current_data import encode, row_values_expression


def columns_for_template(template):
    aliases = {"amount": "item_amount", "tax_amount": "item_tax_amount", "remarks": "item_remarks"}
    return [
        {
            "key": aliases.get(f.key, f.key)
            if template.engine_kind == "legacy" and f.section == "item"
            else f.key,
            "label": template.output_mapping.get(f.key, f.label),
            "section": f.section,
            "value_type": f.value_type,
            "user_defined": False,
        }
        for f in template.fields
    ]


def column_has_value(session, table_id, key):
    path = '$."' + key.replace('"', '\\"') + '"'
    expression = row_values_expression()
    value = func.json_extract(expression, path)
    return (
        session.scalar(
            select(1)
            .select_from(DataRowRecord)
            .where(
                DataRowRecord.table_id == table_id,
                value.is_not(None),
                or_(func.json_type(expression, path) != "text", func.length(func.trim(value)) > 0),
            )
            .limit(1)
        )
        is not None
    )


def _update_file_schemas(session, table_id, columns):
    for current in session.scalars(
        select(FileCurrentDataRecord).where(FileCurrentDataRecord.table_id == table_id)
    ):
        previous = json.loads(current.schema_json)
        keys = {column["key"] for column in columns}
        schema = [*columns, *[column for column in previous if column["key"] not in keys]]
        if schema != previous:
            from document_pipeline_api.services.current_data import update_current_schema
            update_current_schema(session, current, schema)


def sync_template_contract(session, template_id, body):
    """Validate and update display/schema in the template revision transaction."""
    from document_pipeline_api.services.data_tables import table_columns
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)

    record = session.get(TemplateRecord, template_id)
    keys = [template_id] + (
        [record.builtin_key] if record.builtin_key in {"invoice", "delivery"} else []
    )
    tables = list(
        session.scalars(select(DataTableRecord).where(DataTableRecord.template_key.in_(keys)))
    )
    active = [
        {
            "key": f.key,
            "label": body.output_mapping.get(f.key, f.label),
            "section": f.section,
            "value_type": f.value_type,
            "user_defined": False,
        }
        for f in body.fields
    ]
    active_by_key = {column["key"]: column for column in active}
    # A queued extraction can still introduce values using its frozen version,
    # even when this template has never produced a table yet.
    from document_pipeline_api.services.templates import get_template_version

    pending_versions = session.scalars(
        select(TaskRecord.template_version)
        .where(
            TaskRecord.template_id == template_id,
            TaskRecord.status.in_(
                ["queued", "processing", "validating", "paused", "waiting_for_template"]
            ),
            TaskRecord.template_version.is_not(None),
        )
        .distinct()
    ).all()
    for version in pending_versions:
        for old in columns_for_template(get_template_version(session, template_id, version)):
            new = active_by_key.get(old["key"])
            if new and (new["section"], new["value_type"]) != (old["section"], old["value_type"]):
                raise HTTPException(
                    409, f"字段“{old['label']}”仍有待处理文件，不能改变类型或文件/明细归属。"
                )
    for table in tables:
        previous = [column.model_dump() for column in table_columns(session, table)]
        for old in previous:
            new = active_by_key.get(old["key"])
            if new and (new["section"], new["value_type"]) != (old["section"], old["value_type"]):
                inflight = session.scalar(
                    select(TaskRecord.id)
                    .where(
                        TaskRecord.template_id == template_id,
                        TaskRecord.status.in_(["queued", "processing", "validating", "paused"]),
                    )
                    .limit(1)
                )
                if column_has_value(session, table.id, old["key"]) or inflight:
                    raise HTTPException(
                        409,
                        f"字段“{old['label']}”仍有当前数据或待处理文件，不能改变类型或文件/明细归属。",
                    )
        retained = [
            {**column, "user_defined": True}
            for column in previous
            if column["key"] not in active_by_key
        ]
        columns = [*active, *retained]
        names = [" ".join(column["label"].split()).casefold() for column in columns]
        if len(names) != len(set(names)):
            raise HTTPException(409, "新的字段名称与数据表保留的旧字段同名，请换一个名称。")
        table.columns_json = encode(columns)
        table.presentation_json = body.behavior.presentation.model_dump_json()
        _update_file_schemas(session, table.id, columns)


def destination_for_extraction(session, extraction, target_table_id=None):
    from document_pipeline_api.services.templates import get_template_version

    template_id = extraction.template_id or f"builtin-{extraction.document_kind}"
    record = session.get(TemplateRecord, template_id)
    if record is None:
        return None
    template = get_template_version(
        session, template_id, extraction.template_version or record.current_version
    )
    binding = session.get(TemplateTableBindingRecord, template_id)
    table = session.get(DataTableRecord, binding.table_id) if binding else None
    if table is None:
        candidates = list(
            session.scalars(
                select(DataTableRecord)
                .where(
                    DataTableRecord.template_key.in_(
                        [template_id, record.builtin_key or template_id]
                    )
                )
                .order_by(DataTableRecord.created_at.desc(), DataTableRecord.id)
            )
        )
        table = next(
            (
                candidate
                for candidate in candidates
                if candidate.template_version == str(template.version)
            ),
            candidates[0] if candidates else None,
        )
        if table is None:
            latest = get_template_version(session, template_id, record.current_version)
            table = DataTableRecord(
                collection_kind="files" if latest.engine_kind == "autonomous" else "table",
                id=str(uuid4()),
                name=latest.name,
                template_key=template_id,
                template_version=str(latest.version),
                document_kind=extraction.document_kind,
                columns_json=encode(columns_for_template(latest)),
                presentation_json=latest.behavior.presentation.model_dump_json(),
            )
            session.add(table)
            session.flush()
        if binding:
            binding.table_id = table.id
        else:
            session.add(TemplateTableBindingRecord(template_id=template_id, table_id=table.id))
    if target_table_id and target_table_id != table.id:
        raise HTTPException(409, "该模板的文件写入其绑定数据表；不能把同一份当前数据复制到其他表。")
    from document_pipeline_api.services.data_tables import table_columns

    columns = [column.model_dump() for column in table_columns(session, table)]
    known = {column["key"]: column for column in columns}
    changed = False
    for column in columns_for_template(template):
        old = known.get(column["key"])
        if old is None:
            columns.append(column)
            changed = True
        elif (old["section"], old["value_type"]) != (column["section"], column["value_type"]):
            raise HTTPException(
                409, f"字段“{column['label']}”与这份文件冻结的模板定义不兼容，请先处理字段冲突。"
            )
    if changed:
        table.columns_json = encode(columns)
        _update_file_schemas(session, table.id, columns)
    session.flush()
    return table
