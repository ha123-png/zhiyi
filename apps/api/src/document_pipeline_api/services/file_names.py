"""Validate same-call model naming advice, preserving original file identity."""
from pathlib import Path
from datetime import datetime, timezone
from typing import Annotated
from pydantic import BaseModel, ConfigDict, WrapValidator

from fastapi import HTTPException

from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.schemas.file_name import FileNameDecision, FileNameRead
from document_pipeline_api.services.file_copies import CopyExportError, validate_copy_name


class NameAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rename: bool
    name: str | None


def _optional_advice(value, handler):
    try:
        return handler(value)
    except ValueError:
        return None


OptionalNameAdvice = Annotated[NameAdvice | None, WrapValidator(_optional_advice)]


def model_file_name(original: str, decision: object) -> FileNameRead:
    """Validate a same-call semantic suggestion; malformed advice preserves the original."""
    fallback = FileNameRead(suggested_filename=original, explanation="保留原文件名。")
    if not isinstance(decision, dict):
        return FileNameRead(suggested_filename=original, explanation="AI 未提供有效的名称建议，已保留原名；你仍可手动修改。")
    if decision.get("rename") is not True:
        return fallback
    suggested = decision.get("name")
    if not isinstance(suggested, str) or not suggested.strip():
        return fallback
    suffix = Path(original).suffix
    proposed = suggested.strip()
    if Path(proposed).suffix.lower() != suffix.lower():
        proposed += suffix
    try:
        validate_copy_name(proposed)
    except CopyExportError:
        return FileNameRead(suggested_filename=original, explanation="名称建议不符合文件名要求，已保留原名。")
    return FileNameRead(suggested_filename=proposed,
        explanation="AI 根据本次提供的内容建议名称，请确认；原上传名始终保留。")


def confirm_file_name(task: TaskRecord, filename: str | None = None) -> None:
    state = task.file_name
    if state is None:
        if filename is not None:
            raise HTTPException(422, "此任务未开启名称建议。")
        return
    chosen = filename if filename is not None else state.confirmed_filename or task.filename
    try:
        validate_copy_name(chosen)
    except CopyExportError as error:
        raise HTTPException(422, str(error)) from error
    if Path(chosen).suffix.lower() != Path(task.filename).suffix.lower():
        raise HTTPException(422, "请保留原文件扩展名，只修改名称。")
    if state.status == "pending" or state.confirmed_filename != chosen:
        state.decisions.append(FileNameDecision(before=state.confirmed_filename or task.filename, after=chosen, confirmed_at=datetime.now(timezone.utc).isoformat()))
    state.confirmed_filename = chosen
    state.status = "confirmed"
    task.file_name_json = state.model_dump_json()
    export = task.file_export
    if export is not None and export.status not in {"completed", "skipped"}:
        export.confirmed_name = chosen
        task.export_state_json = export.model_dump_json()
