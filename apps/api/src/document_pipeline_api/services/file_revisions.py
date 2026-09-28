"""One bounded read contract for file history in the UI and assistant tools."""
import json

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import load_only

from document_pipeline_api.models import FileRevisionDetailRecord, FileRevisionRecord, TaskRecord
from document_pipeline_api.services.read_snapshot import begin_read_snapshot


def revision_summary(record: FileRevisionRecord) -> dict:
    return {"id": record.id, "version": record.version, "operation": record.operation,
            "editor": record.editor, "created_at": record.created_at, "sealed": record.sealed_at is not None,
            "modified_fields": record.modified_fields, "deleted_details": record.deleted_details,
            "added_details": record.added_details}


def _check_page(page: int, page_size: int) -> None:
    if page < 1 or not 1 <= page_size <= 50:
        raise HTTPException(422, "历史页码必须为正数，每页最多读取 50 条。")


def list_revisions(session, task_id: str, *, page: int = 1, page_size: int = 20) -> dict:
    _check_page(page, page_size)
    begin_read_snapshot(session)
    if session.get(TaskRecord, task_id) is None:
        raise HTTPException(404, "没有找到这份文件。")
    total = session.scalar(select(func.count()).select_from(FileRevisionRecord).where(FileRevisionRecord.task_id == task_id)) or 0
    records = session.scalars(select(FileRevisionRecord).options(load_only(
        FileRevisionRecord.id, FileRevisionRecord.version, FileRevisionRecord.operation,
        FileRevisionRecord.editor, FileRevisionRecord.created_at, FileRevisionRecord.sealed_at,
        FileRevisionRecord.modified_fields, FileRevisionRecord.deleted_details, FileRevisionRecord.added_details,
        raiseload=True,
    )).where(FileRevisionRecord.task_id == task_id)
        .order_by(FileRevisionRecord.version.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"page": page, "page_size": page_size, "total": total,
            "revisions": [revision_summary(record) for record in records]}


def revision_details(session, task_id: str, revision_id: str, *, page: int = 1, page_size: int = 20) -> dict:
    _check_page(page, page_size)
    begin_read_snapshot(session)
    record = session.get(FileRevisionRecord, revision_id)
    if record is None or record.task_id != task_id:
        raise HTTPException(404, "没有找到这个文件版本。")
    # Header changes are stored once; the detail index reads only this page.
    changes = json.loads(record.changes_json)
    items = session.scalars(select(FileRevisionDetailRecord.change_json).where(
        FileRevisionDetailRecord.revision_id == revision_id,
    ).order_by(FileRevisionDetailRecord.item_index, FileRevisionDetailRecord.row_id)
      .offset((page - 1) * page_size).limit(page_size)).all()
    return {**revision_summary(record), "schema": json.loads(record.schema_json),
            "header": changes["header"], "items": [json.loads(item) for item in items],
            "page": page, "page_size": page_size, "total_details": record.total_details}
