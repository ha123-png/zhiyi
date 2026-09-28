import json
from collections import Counter
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.services.current_data import row_values_expression
from document_pipeline_api.models import (
    DataRowRecord,
    DataTableRecord,
    DataViewRecord,
)
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.data_tables import DataViewDetail, DataViewRead
from document_pipeline_api.services.data_tables import table_columns, table_presentation


def _category_rows(session, table_id, field_key):
    """Aggregate in SQLite; no full business rows are loaded into Python."""
    from document_pipeline_api.services.data_groups import source_identity_expression, source_view_value
    values = row_values_expression()
    if field_key == "":
        identity = source_identity_expression()
        filename = func.json_extract(values, '$.source_filename')
        query = select(identity, func.min(filename), func.count(), func.min(DataRowRecord.id)).where(
            DataRowRecord.table_id == table_id).group_by(identity).order_by(func.min(DataRowRecord.id))
        categories = []
        for encoded, name, count, first_id in session.execute(query):
            value = source_view_value(encoded)
            label = name or (f"独立数据组 · {first_id}" if isinstance(value, dict) and value["kind"] == "group" else f"手工记录 · {first_id}")
            categories.append((_canonical_json(value), value, label, count))
        duplicates = Counter(entry[2] for entry in categories)
        ordinals = Counter()
        result = []
        for serialized, value, label, count in categories:
            if duplicates[label] > 1:
                ordinals[label] += 1
                label = f"{label[:110]} · {'文件' if isinstance(value, str) else '来源'} {ordinals[label]}"
            result.append((serialized, label[:128], count))
        return result
    path = '$."' + field_key.replace("\\", "\\\\").replace('"', '\\"') + '"'
    kind = func.coalesce(func.json_type(values, path), 'null')
    value = func.json_extract(values, path)
    query = select(kind, value, func.count()).where(DataRowRecord.table_id == table_id).group_by(kind, value)
    result = []
    for value_type, raw, count in session.execute(query):
        if value_type in {'array', 'object'}:
            raise HTTPException(422, "分组字段包含复杂内容，请选择文字、数字或真假值字段。")
        actual = True if value_type == 'true' else False if value_type == 'false' else raw
        result.append((_canonical_json(actual), "未填写" if actual is None else ("是" if actual is True else "否" if actual is False else str(actual))[:128], count))
    return result


def refresh_data_views(session: Session, table_id: str, *, commit=True):
    """Maintain a derived category directory, never a second copy of facts.

    Category IDs already referenced by a conversation stay valid when empty.
    A short metadata transaction freezes discovery; exports begin their own
    read snapshot after it, so spreadsheet rendering does not hold a write lock.
    """
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(404, "没有找到这个数据表。")
    rules = json.loads(table.grouping_json or '[]')
    counts = {}
    existing = {(view.field_key, view.field_value_json): view for view in session.scalars(
        select(DataViewRecord).where(DataViewRecord.table_id == table_id))}
    now = utc_now()
    for field_key in rules:
        for serialized, label, count in _category_rows(session, table_id, field_key):
            key = (field_key, serialized)
            counts[key] = count
            record = existing.get(key)
            if record is None:
                record = DataViewRecord(id=str(uuid4()), table_id=table_id, field_key=field_key,
                    field_value_json=serialized, name=label, created_at=now, updated_at=now)
                session.add(record)
            elif record.name != label:
                record.name, record.updated_at = label, now
    if commit:
        session.commit()
    else:
        session.flush()
    return counts


def create_split_views(session: Session, table_id: str, field_key: str, *, by_file: bool = False) -> list[DataViewRead]:
    from document_pipeline_api.services.file_collections import require_tabular
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(404, "没有找到这个数据表。")
    require_tabular(table)
    field_key = "" if by_file else field_key
    if not by_file and field_key not in {column.key for column in table_columns(session, table)} | {'source_filename', 'item_index'}:
        raise HTTPException(422, "数据表中不存在这个字段。")
    rules = json.loads(table.grouping_json or '[]')
    if field_key not in rules:
        table.grouping_json = json.dumps([*rules, field_key], ensure_ascii=False)
    session.flush()
    return list_data_views(session, table_id)


def list_data_views(session: Session, table_id: str, *, offset: int = 0, limit: int = 200) -> list[DataViewRead]:
    counts = refresh_data_views(session, table_id)
    results = []
    matched = 0
    for record in session.scalars(select(DataViewRecord).where(DataViewRecord.table_id == table_id)
            .order_by(DataViewRecord.created_at, DataViewRecord.name, DataViewRecord.id).execution_options(yield_per=500)):
        count = counts.get((record.field_key, record.field_value_json), 0)
        if not count:
            continue
        if offset <= matched < offset + limit:
            results.append(_data_view_read(record, row_count=count))
        matched += 1
        if matched >= offset + limit:
            break
    return results


def clear_data_views(session: Session, table_id: str) -> None:
    from sqlalchemy import delete
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(404, "没有找到这个数据表。")
    session.execute(delete(DataViewRecord).where(DataViewRecord.table_id == table_id))
    table.grouping_json = '[]'
    session.commit()


def get_data_view(
    session: Session,
    table_id: str,
    view_id: str,
    *,
    page: int = 1,
    page_size: int = 100,
    search: str | None = None,
) -> DataViewDetail:
    from document_pipeline_api.services.read_snapshot import begin_read_snapshot
    from document_pipeline_api.services.data_rows import data_rows_read
    begin_read_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    record = session.scalar(
        select(DataViewRecord).where(
            DataViewRecord.id == view_id,
            DataViewRecord.table_id == table_id,
        )
    )
    if table is None or record is None:
        raise HTTPException(status_code=404, detail="没有找到这个分 Sheet。")
    row_count = _view_row_count(session, record, search=search)
    rows = _rows_for_view(
        session,
        record,
        offset=(page - 1) * page_size,
        limit=page_size,
        search=search,
    )
    base = _data_view_read(record, row_count=row_count)
    return DataViewDetail(
        presentation=table_presentation(session, table),
        **base.model_dump(),
        source_table_name=table.name,
        columns=table_columns(session, table),
        page=page,
        page_size=page_size,
        rows=data_rows_read(session, table, rows),
    )


def _data_view_read(
    record: DataViewRecord,
    *,
    row_count: int,
) -> DataViewRead:
    return DataViewRead(
        id=record.id,
        table_id=record.table_id,
        name=record.name,
        field_key=record.field_key,
        field_value=json.loads(record.field_value_json),
        row_count=row_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _rows_for_view(
    session: Session,
    record: DataViewRecord,
    *,
    offset: int = 0,
    limit: int | None = None,
    search: str | None = None,
) -> list[DataRowRecord]:
    statement = (
        select(DataRowRecord)
        .where(
            DataRowRecord.table_id == record.table_id,
            _view_predicate(record),
        )
        .order_by(DataRowRecord.id)
        .offset(offset)
    )
    if search:
        from document_pipeline_api.services.current_data import row_search_predicate
        statement = statement.where(row_search_predicate(search))
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all())


def _view_row_count(session: Session, record: DataViewRecord, *, search: str | None = None) -> int:
    from document_pipeline_api.services.current_data import row_search_predicate
    return (
        session.scalar(
            select(func.count(DataRowRecord.id)).where(
                DataRowRecord.table_id == record.table_id,
                _view_predicate(record),
                row_search_predicate(search) if search else True,
            )
        )
        or 0
    )


def _view_predicate(record: DataViewRecord):
    if record.field_key == "":
        value = json.loads(record.field_value_json)
        if isinstance(value, dict):
            from document_pipeline_api.services.data_groups import source_identity_expression
            return source_identity_expression() == _canonical_json(value)
        return DataRowRecord.task_id == value
    if json.loads(record.field_value_json) is None:
        _, _, path = _view_filter(record)
        return func.coalesce(func.json_type(row_values_expression(), path), 'null') == 'null'
    json_type, comparison, path = _view_filter(record)
    return (func.json_type(row_values_expression(), path) == json_type) & (func.json_extract(row_values_expression(), path) == comparison)


def _view_filter(record: DataViewRecord) -> tuple[str, object, str]:
    value = json.loads(record.field_value_json)
    json_type, comparison = _sqlite_json_comparison(value)
    path = '$."' + record.field_key.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return json_type, comparison, path


def _sqlite_json_comparison(value: object) -> tuple[str, object]:
    if value is None:
        return 'null', None
    if isinstance(value, bool):
        return ("true" if value else "false"), int(value)
    if isinstance(value, int):
        return "integer", value
    if isinstance(value, float):
        return "real", value
    if isinstance(value, str):
        return "text", value
    raise RuntimeError("分 Sheet 中存在不受支持的筛选值。")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
