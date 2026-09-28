"""Bounded file views shared by ordinary and autonomous extraction collections."""
import json

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.models import DataRowRecord, DataTableRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.services.current_data import get_current, row_search_predicate
from document_pipeline_api.services.read_snapshot import begin_read_snapshot


def require_tabular(table: DataTableRecord) -> None:
    if table.collection_kind == "files":
        raise HTTPException(409, "自主提取的字段按文件保存，请按文件查看或导出，不能合并成统一字段的数据表。")


def detail_read(row: DataRowRecord) -> dict:
    return {"id": row.id, "item_index": row.item_index, "version": row.row_version,
            "values": json.loads(row.row_json)}


def list_files(session: Session, table_id: str, *, page: int = 1, page_size: int = 12, search: str = "") -> dict:
    begin_read_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(404, "没有找到这个数据集合。")
    predicate = [FileCurrentDataRecord.table_id == table_id, FileCurrentDataRecord.has_data.is_(True)]
    if search.strip():
        predicate.append(select(1).select_from(DataRowRecord).where(
            DataRowRecord.task_id == FileCurrentDataRecord.task_id, row_search_predicate(search.strip()),
        ).correlate(FileCurrentDataRecord).exists())
    total = session.scalar(select(func.count()).select_from(FileCurrentDataRecord).where(*predicate)) or 0
    records = session.scalars(select(FileCurrentDataRecord).join(TaskRecord).where(*predicate)
        .order_by(TaskRecord.created_at.desc(), TaskRecord.id).offset((page - 1) * page_size).limit(page_size)).all()
    ids = [record.task_id for record in records]
    counts = dict(session.execute(select(DataRowRecord.task_id, func.count()).where(
        DataRowRecord.task_id.in_(ids), DataRowRecord.is_header_only.is_(False),
    ).group_by(DataRowRecord.task_id)).all()) if ids else {}
    previews = {key: [] for key in ids}
    if ids:
        ranked = select(DataRowRecord.id, func.row_number().over(
            partition_by=DataRowRecord.task_id, order_by=(DataRowRecord.item_index, DataRowRecord.id),
        ).label('position')).where(DataRowRecord.task_id.in_(ids), DataRowRecord.is_header_only.is_(False)).subquery()
        for row in session.scalars(select(DataRowRecord).join(ranked, ranked.c.id == DataRowRecord.id)
            .where(ranked.c.position <= 3).order_by(DataRowRecord.item_index, DataRowRecord.id)):
            previews[row.task_id].append(detail_read(row))
    files = [{"task_id": record.task_id, "filename": record.source.display_filename,
              "version": record.current_version, "header": json.loads(record.header_json),
              "schema": json.loads(record.schema_json), "item_count": counts.get(record.task_id, 0),
              "items": previews[record.task_id], "updated_at": record.updated_at} for record in records]
    return {"table_id": table_id, "page": page, "page_size": page_size, "total": total, "files": files}


def file_details(session: Session, task_id: str, *, page: int = 1, page_size: int = 50, row_ids=None) -> dict:
    begin_read_snapshot(session)
    current = get_current(session, task_id)
    predicate = [DataRowRecord.task_id == task_id, DataRowRecord.is_header_only.is_(False)]
    if row_ids is not None:
        predicate.append(DataRowRecord.id.in_(row_ids))
    total = session.scalar(select(func.count()).select_from(DataRowRecord).where(*predicate)) or 0
    rows = session.scalars(select(DataRowRecord).where(*predicate).order_by(DataRowRecord.item_index, DataRowRecord.id)
        .offset((page - 1) * page_size).limit(page_size)).all()
    return {"task_id": task_id, "filename": current.source.display_filename,
            "header": json.loads(current.header_json), "schema": json.loads(current.schema_json),
            "version": current.current_version, "page": page,
            "page_size": page_size, "total": total, "items": [detail_read(row) for row in rows]}
