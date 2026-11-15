"""Read live merges from their source rows and current column contracts."""
import hashlib
import json
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import case, select

from document_pipeline_api.models import DataRowRecord, DataTableRecord
from document_pipeline_api.services.current_data import row_values


def require_writable(table: DataTableRecord) -> None:
    if table.merge_sources_json is not None:
        raise HTTPException(409, "合并表为只读，请回到原表修改。")


def table_membership(table: DataTableRecord):
    sources = table.merge_sources
    if sources is None:
        return DataRowRecord.table_id == table.id
    return DataRowRecord.table_id.in_(sources)


def merge_order(table: DataTableRecord) -> list:
    sources = table.merge_sources
    return [case({key: index for index, key in enumerate(sources)}, value=DataRowRecord.table_id)] if sources else []


def merge_columns(session, source_ids, *, validate=False):
    from document_pipeline_api.services.data_tables import table_columns

    groups = {}
    key_maps = {}
    for source_id in source_ids:
        source = session.get(DataTableRecord, source_id)
        if source is None:
            continue
        key_maps[source_id] = mapping = {}
        seen = set()
        for column in table_columns(session, source):
            label = " ".join(column.label.split()).casefold()
            variants = groups.setdefault(label, {})
            signature = (column.value_type, column.section)
            if validate and label in seen:
                raise HTTPException(409, f"数据表“{source.name}”存在重复列名“{column.label}”，请先重命名后再合并。")
            if validate and variants and signature not in variants:
                raise HTTPException(409, f"同名字段“{column.label}”的类型或文件/明细归属不同，请先重命名，再合并。")
            # Later source edits may introduce conflicts. Keep such columns
            # separate, including duplicate labels within one source.
            variant = (*signature, source_id, column.key) if label in seen else signature
            seen.add(label)
            if variant not in variants:
                key = "merged_" + hashlib.sha256(json.dumps([label, variant]).encode()).hexdigest()[:24]
                variants[variant] = column.model_copy(update={"key": key, "user_defined": False})
            mapping[column.key] = variants[variant].key
    columns = []
    for variants in groups.values():
        for index, column in enumerate(variants.values(), 1):
            if len(variants) > 1:
                column = column.model_copy(update={"label": f"{column.label}（{index}）"})
            columns.append(column)
    return columns, key_maps


def merged_values(row, key_maps):
    values = row_values(row)
    projected = {target: values[key] for key, target in key_maps[row.table_id].items() if key in values}
    if row.task_id or "__row_group" in values:
        projected["__row_group"] = json.dumps([row.table_id, row.task_id or values["__row_group"]])
    if "source_filename" in values:
        projected["source_filename"] = values["source_filename"]
    return projected


def export_records(session, table, rows, *, include_table_name=False):
    sources = table.merge_sources
    if sources is None:
        yield from rows
        return
    _, key_maps = merge_columns(session, sources)
    names = dict(session.execute(select(DataTableRecord.id, DataTableRecord.name).where(DataTableRecord.id.in_(sources))).all()) if include_table_name else {}
    for row in rows:
        values = merged_values(row, key_maps)
        if include_table_name:
            values["__zhiyi_source_table"] = names[row.table_id]
        yield SimpleNamespace(id=row.id, task_id=None, row_json=json.dumps(values),
                              input_scope_json=row.input_scope_json, review_pending=row.review_pending)
