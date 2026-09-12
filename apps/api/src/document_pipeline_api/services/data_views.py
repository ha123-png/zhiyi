import json
from collections import Counter
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.models import (
    DataRowRecord,
    DataTableRecord,
    DataViewRecord,
)
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.data_tables import DataViewDetail, DataViewRead
from document_pipeline_api.services.data_rows import data_row_read
from document_pipeline_api.services.data_tables import table_columns, table_presentation


def create_split_views(
    session: Session,
    table_id: str,
    field_key: str,
    *,
    by_file: bool = False,
) -> list[DataViewRead]:
    # Empty is not a valid user field key. Persist it as the source-identity
    # discriminator, preserving existing field-view records without migration.
    if by_file:
        field_key = ""
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    rows = session.scalars(
        select(DataRowRecord)
        .where(DataRowRecord.table_id == table_id)
        .order_by(DataRowRecord.id)
    ).all()
    if not rows:
        raise HTTPException(status_code=422, detail="空表不能创建分 Sheet。")

    grouped: dict[str, tuple[object, int]] = {}
    names: dict[str, str] = {}
    field_exists = False
    for row in rows:
        values = json.loads(row.row_json)
        if not by_file and field_key not in values:
            continue
        field_exists = True
        value = row.task_id if by_file else values[field_key]
        if value is None and not by_file:
            continue
        if isinstance(value, (dict, list)):
            raise HTTPException(
                status_code=422,
                detail="分 Sheet 字段只能是文字、数字或真假值。",
            )
        serialized = _canonical_json(value)
        if by_file:
            names[serialized] = str(values.get("source_filename") or "来源文件") if value else "无来源文件"
        previous = grouped.get(serialized)
        grouped[serialized] = (value, (previous[1] if previous else 0) + 1)
    if not field_exists:
        raise HTTPException(status_code=422, detail="数据表中不存在这个字段。")
    if not grouped:
        raise HTTPException(status_code=422, detail="这个字段没有可用于分组的值。")

    existing = {
        view.field_value_json: view
        for view in session.scalars(
            select(DataViewRecord).where(
                DataViewRecord.table_id == table_id,
                DataViewRecord.field_key == field_key,
            )
        ).all()
    }
    created_at = utc_now()
    duplicate_names = Counter(names.values())
    name_ordinals: Counter[str] = Counter()
    records: list[DataViewRecord] = []
    for serialized, (value, _) in grouped.items():
        label = names.get(serialized, str(value))
        if by_file and duplicate_names[label] > 1:
            name_ordinals[label] += 1
            label = f"{label[:116]} · 文件 {name_ordinals[label]}"
        record = existing.get(serialized)
        if record is None:
            record = DataViewRecord(
                id=str(uuid4()),
                table_id=table_id,
                name=label[:128],
                field_key=field_key,
                field_value_json=serialized,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(record)
        records.append(record)
    session.commit()
    return [
        _data_view_read(record, row_count=grouped[record.field_value_json][1])
        for record in records
    ]


def list_data_views(session: Session, table_id: str) -> list[DataViewRead]:
    if session.get(DataTableRecord, table_id) is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    records = session.scalars(
        select(DataViewRecord)
        .where(DataViewRecord.table_id == table_id)
        .order_by(DataViewRecord.created_at, DataViewRecord.name)
    ).all()
    return [
        _data_view_read(record, row_count=_view_row_count(session, record))
        for record in records
    ]


def get_data_view(
    session: Session,
    table_id: str,
    view_id: str,
    *,
    page: int = 1,
    page_size: int = 100,
    search: str | None = None,
) -> DataViewDetail:
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
        rows=[data_row_read(row) for row in rows],
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
        statement = statement.where(DataRowRecord.row_json.ilike(f"%{search}%"))
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all())


def _view_row_count(session: Session, record: DataViewRecord, *, search: str | None = None) -> int:
    return (
        session.scalar(
            select(func.count(DataRowRecord.id)).where(
                DataRowRecord.table_id == record.table_id,
                _view_predicate(record),
                DataRowRecord.row_json.ilike(f"%{search}%") if search else True,
            )
        )
        or 0
    )


def _view_predicate(record: DataViewRecord):
    if record.field_key == "":
        return DataRowRecord.task_id == json.loads(record.field_value_json)
    json_type, comparison, path = _view_filter(record)
    return (func.json_type(DataRowRecord.row_json, path) == json_type) & (func.json_extract(DataRowRecord.row_json, path) == comparison)


def _view_filter(record: DataViewRecord) -> tuple[str, object, str]:
    value = json.loads(record.field_value_json)
    json_type, comparison = _sqlite_json_comparison(value)
    path = '$."' + record.field_key.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return json_type, comparison, path


def _sqlite_json_comparison(value: object) -> tuple[str, object]:
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
