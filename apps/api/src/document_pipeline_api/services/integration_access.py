from fastapi import HTTPException
from sqlalchemy import select
from pydantic import BaseModel

from document_pipeline_api.model_diagnostics import safe_diagnostic
from document_pipeline_api.models import FileCurrentDataRecord
from document_pipeline_api.schemas.tasks import TaskRead


class IntegrationExportStatus(BaseModel):
    mode: str
    status: str
    version: int
    error_code: str | None = None


class IntegrationTaskRead(TaskRead):
    file_export: IntegrationExportStatus | None = None
    internal_storage: None = None


def integration_task(task, pending=None) -> IntegrationTaskRead:
    value = TaskRead.model_validate(task, context={"pending_details": pending or {}})
    public = value.model_dump(exclude={"file_export", "internal_storage"})
    if value.failure_message:
        public["failure_message"] = safe_diagnostic(value.failure_message)
    if value.file_export:
        public["file_export"] = value.file_export.model_dump(include={"mode", "status", "version", "error_code"})
    return IntegrationTaskRead.model_validate(public)


def restrict_file_to_tables(session, task_id: str, table_ids: tuple[str, ...] | None) -> None:
    if table_ids is None:
        return
    allowed = session.scalar(select(FileCurrentDataRecord.task_id).where(
        FileCurrentDataRecord.task_id == task_id,
        FileCurrentDataRecord.table_id.in_(table_ids),
    ))
    if allowed is None:
        raise HTTPException(404, "授权范围内没有这个文件。")
