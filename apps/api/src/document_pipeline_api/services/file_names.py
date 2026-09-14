"""Validate same-call model naming advice, preserving original file identity."""
from pathlib import Path
from datetime import datetime, timezone
import re
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
    chosen = filename if filename is not None else state.confirmed_filename or state.suggested_filename or task.filename
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


def automatic_file_name(original: str, decision: object, values: dict) -> FileNameRead:
    state = model_file_name(original, decision)
    stem = Path(original).stem
    opaque = bool(re.search(r"[a-f0-9]{16,}", stem, re.I) or re.fullmatch(r"(?:IMG|DSC|SCAN|PDF|FILE)?[_ -]?\d{4,}", stem, re.I))
    if opaque and state.suggested_filename == original:
        header = values.get("header") if isinstance(values.get("header"), dict) else values
        candidates = [(key, value.strip()) for key, value in header.items()
                      if isinstance(value, str) and 2 <= len(value.strip()) <= 80
                      and not re.fullmatch(r"[\d\W_]+", value.strip())
                      and not re.search(r"[a-f0-9]{16,}", value, re.I)]
        if candidates:
            selected = candidates[:2]
            name = " · ".join(re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value) for _, value in selected)[:100].strip(" .")
            try:
                validate_copy_name(name + Path(original).suffix)
                state.suggested_filename = name + Path(original).suffix
                state.source_fields = [key for key, _ in selected]
                state.explanation = "根据已提取内容生成名称，上传原名始终保留。"
            except CopyExportError:
                pass
    state.confirmed_filename = state.suggested_filename
    state.status = "confirmed"
    if state.confirmed_filename != original:
        state.decisions.append(FileNameDecision(before=original, after=state.confirmed_filename, confirmed_at=datetime.now(timezone.utc).isoformat()))
        state.explanation = "已自动采用内容名称，可直接修改；上传原名始终保留。"
    return state


def adopt_pending_file_names(session) -> int:
    """Upgrade unaccepted name advice, preserving reviewed names and file bytes."""
    import json
    from sqlalchemy import func, select
    from document_pipeline_api.models import ExtractionRecord

    query = select(TaskRecord, ExtractionRecord.result_json).join(
        ExtractionRecord, ExtractionRecord.task_id == TaskRecord.id
    ).where(func.json_extract(TaskRecord.file_name_json, "$.status") == "pending")
    count = 0
    for task, saved_result in session.execute(query.execution_options(yield_per=500)):
        old = task.file_name
        state = automatic_file_name(task.filename, {
            "rename": old.suggested_filename != task.filename,
            "name": old.suggested_filename,
        }, json.loads(saved_result))
        task.file_name_json = state.model_dump_json()
        confirm_file_name(task)
        count += 1
    if count:
        session.commit()
    return count
