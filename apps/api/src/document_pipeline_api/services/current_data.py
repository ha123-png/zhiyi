"""The single current-data boundary. Callers commit the entire unit of work.

File headers are stored once; stable detail rows own only detail values. Current
mutations and a durable revision delta are written in the same transaction.
"""

from __future__ import annotations

import json
import math
from datetime import date
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Text, case, cast, func, select, update
from sqlalchemy.orm import Session

from document_pipeline_api.models import (
    DataEditSessionRecord, DataRowRecord, FileCurrentDataRecord, FileRevisionRecord, FileRevisionDetailRecord,
)
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.services.revision_changes import detail_counts


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def row_values(row: DataRowRecord) -> dict:
    details = json.loads(row.row_json)
    if row.task_id is None:
        return details
    current = row.file_current
    if current is None:
        raise HTTPException(409, "这份文件的当前数据不完整，已停止读取，请检查文件记录。")
    return {**json.loads(current.header_json), **details,
            "source_filename": current.source.display_filename, "item_index": row.item_index}


def row_values_expression():
    """SQL projection used for searches and filters; never a second stored copy."""
    header = select(FileCurrentDataRecord.header_json).where(
        FileCurrentDataRecord.task_id == DataRowRecord.task_id,
    ).correlate(DataRowRecord).scalar_subquery()
    from document_pipeline_api.models import TaskRecord
    filename = select(func.coalesce(
        func.nullif(func.json_extract(TaskRecord.file_name_json, '$.confirmed_filename'), ''),
        func.json_extract(TaskRecord.file_name_json, '$.suggested_filename'), TaskRecord.filename,
    )).where(TaskRecord.id == DataRowRecord.task_id).correlate(DataRowRecord).scalar_subquery()
    projection = func.json_patch(func.coalesce(header, "{}"), DataRowRecord.row_json)
    return case((DataRowRecord.task_id.is_(None), DataRowRecord.row_json), else_=func.json_set(
        projection, '$.source_filename', filename, '$.item_index', DataRowRecord.item_index))


def row_search_predicate(search: str):
    # Search is literal text in every entry point; % and _ are not wildcards.
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    values = func.json_each(row_values_expression()).table_valued("key", "value").alias("search_values")
    # Decode JSON strings first: escaped Unicode and literal Unicode describe
    # the same business text. Raw JSON LIKE would miss older/imported records.
    return select(1).select_from(values).where(
        cast(values.c.value, Text).ilike(f"%{escaped}%", escape="\\"),
    ).correlate(DataRowRecord).exists()


def get_current(session: Session, task_id: str) -> FileCurrentDataRecord:
    current = session.get(FileCurrentDataRecord, task_id)
    if current is None:
        raise HTTPException(409, "这份文件尚未建立当前数据，请先完成提取。")
    return current


def current_snapshot(session: Session, task_id: str) -> dict:
    current = get_current(session, task_id)
    rows = session.scalars(select(DataRowRecord).where(DataRowRecord.task_id == task_id)
                           .order_by(DataRowRecord.item_index, DataRowRecord.id)).all()
    return {
        "header": json.loads(current.header_json),
        "items": [{"id": row.id, "item_index": row.item_index, "values": json.loads(row.row_json)}
                  for row in rows if not row.is_header_only],
        "has_data": current.has_data, "version": current.current_version,
    }


def _check_values(values: dict, schema: list[dict], section: str) -> None:
    fields = {column["key"]: column for column in schema if column.get("section") == section}
    unknown = values.keys() - fields.keys()
    if unknown:
        raise HTTPException(422, f"不存在的{('文件' if section == 'header' else '明细')}字段：{', '.join(sorted(unknown))}。")
    check_column_values(values, list(fields.values()))


def check_column_values(values: dict, schema: list[dict]) -> None:
    """Validate the same scalar contract for file, independent and external writes."""
    fields = {column["key"]: column for column in schema}
    unknown = values.keys() - fields.keys()
    if unknown:
        raise HTTPException(422, f"不存在的字段：{', '.join(sorted(unknown))}。")
    for key, value in values.items():
        if value is None or value == "":
            continue
        kind = fields[key].get("value_type", "text")
        valid = not isinstance(value, (dict, list))
        if kind in {"number", "integer"}:
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
            if kind == "integer" and valid:
                valid = int(value) == value
        elif kind == "boolean":
            valid = isinstance(value, bool)
        elif kind == "date":
            try:
                date.fromisoformat(value)
            except (ValueError, TypeError):
                valid = False
        elif kind == "text":
            valid = isinstance(value, str)
        if not valid:
            raise HTTPException(422, f"字段“{fields[key].get('label', key)}”的值不符合类型要求。")


def create_edit_session(session: Session, *, owner: str, editor: str = "user") -> DataEditSessionRecord:
    record = DataEditSessionRecord(id=str(uuid4()), owner=owner, editor=editor)
    session.add(record)
    session.flush()
    return record


def _merge_change(changes: dict, key: str, before: object, after: object) -> None:
    initial = changes[key]["before"] if key in changes else before
    if initial == after:
        changes.pop(key, None)
    else:
        changes[key] = {"before": initial, "after": after}


def _record_change(
    session: Session, current: FileCurrentDataRecord, before: dict, after: dict,
    *, editor: str, operation: str, edit_session_id: str | None,
) -> None:
    pending = session.scalar(select(FileRevisionRecord).where(
        FileRevisionRecord.task_id == current.task_id, FileRevisionRecord.sealed_at.is_(None),
    ))
    if pending is not None and pending.session_id != edit_session_id:
        _seal_revision(session, pending)
        session.flush()
        pending = None
    new_revision = pending is None
    if pending is None:
        version = session.scalar(select(func.max(FileRevisionRecord.version)).where(
            FileRevisionRecord.task_id == current.task_id,
        )) or 0
        pending = FileRevisionRecord(
            id=str(uuid4()), task_id=current.task_id, session_id=edit_session_id,
            version=version + 1, operation=operation, editor=editor,
            schema_json=current.schema_json, changes_json='{"header":{},"items":{}}',
            modified_fields=0, deleted_details=0, added_details=0, total_details=0,
        )
        session.add(pending)
        session.flush()
    changes = json.loads(pending.changes_json)
    previous_headers = len(changes["header"])
    for key in before["header"].keys() | after["header"].keys():
        _merge_change(changes["header"], key, before["header"].get(key), after["header"].get(key))
    pending.modified_fields += len(changes["header"]) - previous_headers
    before_items = {str(item["id"]): item for item in before["items"]}
    after_items = {str(item["id"]): item for item in after["items"]}
    affected = before_items.keys() | after_items.keys()
    existing = {}
    if not new_revision:
        affected_ids = [int(key) for key in affected]
        for offset in range(0, len(affected_ids), 500):
            existing.update({str(item.row_id): item for item in session.scalars(select(FileRevisionDetailRecord).where(
                FileRevisionDetailRecord.revision_id == pending.id,
                FileRevisionDetailRecord.row_id.in_(affected_ids[offset:offset + 500]),
            ))})
    for key in affected:
        stored = existing.get(key)
        previous = json.loads(stored.change_json) if stored else None
        detail = {key: previous} if previous is not None else {}
        deleted_before = before_items.get(key) if key not in after_items else None
        _merge_change(detail, key, before_items.get(key), after_items.get(key))
        if deleted_before is not None and previous is not None and previous["before"] != deleted_before:
            # Keep both the round's initial value and the complete value that
            # actually existed when deleted (including edits in this round).
            detail[key] = {"before": previous["before"], "after": None,
                           "deleted_before": deleted_before}
        updated = detail.get(key)
        old_counts, new_counts = detail_counts(previous), detail_counts(updated)
        pending.modified_fields += new_counts[0] - old_counts[0]
        pending.deleted_details += new_counts[1] - old_counts[1]
        pending.added_details += new_counts[2] - old_counts[2]
        pending.total_details += int(updated is not None) - int(previous is not None)
        if updated is None:
            if stored is not None:
                session.delete(stored)
        else:
            item = updated["after"] or updated.get("deleted_before") or updated["before"]
            if stored is None:
                session.add(FileRevisionDetailRecord(revision_id=pending.id, row_id=int(key),
                    item_index=item["item_index"], change_json=encode(updated)))
            else:
                stored.item_index, stored.change_json = item["item_index"], encode(updated)
    if before.get("has_data", False) != after.get("has_data", False):
        _merge_change(changes, "presence", before.get("has_data", False), after.get("has_data", False))
    pending.changes_json = encode(changes)
    if edit_session_id is None:
        _seal_revision(session, pending)


def _seal_revision(session: Session, revision: FileRevisionRecord) -> None:
    changes = json.loads(revision.changes_json)
    if not changes["header"] and not revision.total_details and "presence" not in changes:
        # An open draft fully reverted before sealing never becomes a version.
        if revision in session.new:
            session.expunge(revision)
        else:
            session.delete(revision)
    else:
        revision.sealed_at = utc_now()


def seal_edit_session(session: Session, session_id: str) -> None:
    record = session.get(DataEditSessionRecord, session_id)
    if record is None or record.sealed_at is not None:
        return
    for revision in session.scalars(select(FileRevisionRecord).where(
        FileRevisionRecord.session_id == session_id, FileRevisionRecord.sealed_at.is_(None),
    )):
        _seal_revision(session, revision)
    record.sealed_at = utc_now()
    session.flush()


def recover_edit_sessions(session: Session, *, owner: str) -> int:
    records = session.scalars(select(DataEditSessionRecord).where(
        DataEditSessionRecord.sealed_at.is_(None), DataEditSessionRecord.owner != owner,
    )).all()
    for record in records:
        seal_edit_session(session, record.id)
    return len(records)


def update_current_schema(session: Session, current: FileCurrentDataRecord, schema: list[dict]) -> None:
    if json.loads(current.schema_json) == schema:
        return
    pending = session.scalar(select(FileRevisionRecord).where(
        FileRevisionRecord.task_id == current.task_id, FileRevisionRecord.sealed_at.is_(None),
    ))
    if pending is not None:
        _seal_revision(session, pending)
    expected = current.current_version
    changed = session.execute(update(FileCurrentDataRecord).where(
        FileCurrentDataRecord.task_id == current.task_id, FileCurrentDataRecord.current_version == expected,
    ).values(current_version=expected + 1))
    if changed.rowcount != 1:
        raise HTTPException(409, "文件结构已经更新，请刷新后重试。")
    current.schema_json = encode(schema)
    current.updated_at = utc_now()
    session.execute(update(DataRowRecord).where(DataRowRecord.task_id == current.task_id)
                    .values(row_version=DataRowRecord.row_version + 1, updated_at=current.updated_at))


def initialize_current(
    session: Session, *, task_id: str, table_id: str, header: dict, items: list[dict],
    schema: list[dict], validation_json: str = "[]", editor: str = "extraction",
) -> FileCurrentDataRecord:
    if session.get(FileCurrentDataRecord, task_id) is not None:
        raise HTTPException(409, "这份文件已有当前数据，不能用提取快照重复覆盖。")
    if session.scalar(select(DataRowRecord.id).where(DataRowRecord.task_id == task_id).limit(1)) is not None:
        raise HTTPException(409, "这份文件已有关联数据，不能重复初始化并覆盖现有内容。")
    header_keys = {column["key"] for column in schema if column.get("section") == "header"}
    detail_keys = {column["key"] for column in schema if column.get("section") == "item"}
    if header_keys & detail_keys:
        raise HTTPException(422, "文件字段与明细字段不能使用同一个字段标识。")
    _check_values(header, schema, "header")
    for item in items:
        _check_values(item, schema, "item")
    current = FileCurrentDataRecord(
        task_id=task_id, table_id=table_id, header_json=encode(header), schema_json=encode(schema),
        validation_json=validation_json, current_version=0, has_data=True,
        next_item_index=len(items) + 1,
    )
    session.add(current)
    session.flush()
    for index, item in enumerate(items or [{}], 1):
        session.add(DataRowRecord(task_id=task_id, table_id=table_id, item_index=index,
                                  row_json=encode(item), is_header_only=not bool(items)))
    session.flush()
    _record_change(session, current, {"header": {}, "items": []}, current_snapshot(session, task_id),
                   editor=editor, operation="extraction", edit_session_id=None)
    session.flush()
    return current


def revise_current(
    session: Session, task_id: str, *, expected_version: int,
    header_changes: dict | None = None, detail_changes: dict[int, dict] | None = None,
    deleted_ids: list[int] | None = None, new_items: list[dict] | None = None,
    editor: str = "user", edit_session_id: str | None = None, operation: str = "edit",
    validation_json: str | None = None,
) -> FileCurrentDataRecord:
    current = get_current(session, task_id)
    if current.current_version != expected_version:
        raise HTTPException(409, "这份文件的数据已经更新，请刷新后再修改。")
    if edit_session_id is not None:
        editing = session.get(DataEditSessionRecord, edit_session_id)
        if editing is None or editing.sealed_at is not None:
            raise HTTPException(409, "本轮编辑已经结束，请刷新后继续。")
    schema = json.loads(current.schema_json)
    header_changes, detail_changes = header_changes or {}, detail_changes or {}
    deleted = set(deleted_ids or [])
    affected = detail_changes.keys() | deleted
    rows = {row.id: row for row in session.scalars(select(DataRowRecord).where(
        DataRowRecord.task_id == task_id, DataRowRecord.id.in_(affected),
    ).order_by(DataRowRecord.item_index, DataRowRecord.id))} if affected else {}
    if (detail_changes.keys() | deleted) - rows.keys() or detail_changes.keys() & deleted:
        raise HTTPException(409, "修改范围包含不存在、已删除或同时被修改和删除的明细。")
    if any(rows[row_id].is_header_only for row_id in detail_changes):
        raise HTTPException(409, "这份文件只有文件级信息，不能把占位行当作明细编辑。")
    _check_values(header_changes, schema, "header")
    for values in list(detail_changes.values()) + (new_items or []):
        _check_values(values, schema, "item")
    before = {"header": json.loads(current.header_json), "has_data": current.has_data,
              "items": [{"id": row.id, "item_index": row.item_index, "values": json.loads(row.row_json)}
                        for row in rows.values() if not row.is_header_only]}
    header = {**before["header"], **header_changes}
    # Existence, rather than a full file snapshot, is enough to decide whether
    # deleting these details clears the file. Revision deltas load only edits.
    has_survivors = current.has_data
    if deleted:
        has_survivors = session.scalar(select(DataRowRecord.id).where(
            DataRowRecord.task_id == task_id, DataRowRecord.id.not_in(deleted),
        ).limit(1)) is not None
    if not has_survivors and not new_items:
        header = {}
    if not current.has_data and (header_changes or new_items):
        raise HTTPException(409, "当前数据已清空，不能从旧编辑草稿恢复。")
    prospective = [
        {"id": row.id, "item_index": row.item_index,
         "values": {**json.loads(row.row_json), **detail_changes.get(row.id, {})}}
        for row in rows.values() if row.id not in deleted and not row.is_header_only
    ]
    if header == before["header"] and prospective == before["items"] and not new_items and not deleted:
        if validation_json is not None:
            current.validation_json = validation_json
        return current
    # Acquire the write fence before changing rows. SQLite serializes writers;
    # the conditional update also rejects stale sessions on other SQL engines.
    result = session.execute(update(FileCurrentDataRecord).where(
        FileCurrentDataRecord.task_id == task_id,
        FileCurrentDataRecord.current_version == expected_version,
    ).values(current_version=expected_version + 1))
    if result.rowcount != 1:
        raise HTTPException(409, "这份文件的数据已经更新，请刷新后再修改。")
    current.header_json = encode(header)
    current.updated_at = utc_now()
    current.has_data = bool(has_survivors or new_items)
    if validation_json is not None:
        current.validation_json = validation_json
    for row_id, row in rows.items():
        if row_id in deleted:
            session.delete(row)
        elif row_id in detail_changes:
            row.row_json = encode({**json.loads(row.row_json), **detail_changes[row_id]})
            row.row_version += 1
            row.updated_at = current.updated_at
    added_rows = []
    if new_items:
        # A header-only carrier is an internal projection, not an extra detail.
        for placeholder in session.scalars(select(DataRowRecord).where(
            DataRowRecord.task_id == task_id, DataRowRecord.is_header_only.is_(True),
        )):
            if placeholder.id not in deleted:
                session.delete(placeholder)
        # The first real detail may reuse the carrier's ordinal, never its ID.
        session.flush()
    for values in new_items or []:
        if current.table_id is None:
            raise HTTPException(409, "该文件的数据表已删除，不能追加明细。")
        row = DataRowRecord(task_id=task_id, table_id=current.table_id,
                            item_index=current.next_item_index, row_json=encode(values))
        session.add(row)
        added_rows.append(row)
        current.next_item_index += 1
    session.flush()
    session.execute(update(DataRowRecord).where(DataRowRecord.task_id == task_id)
                    .values(row_version=current.current_version + 1, updated_at=current.updated_at))
    if validation_json is None:
        refresh_validation(session, current)
    after = {"header": header, "has_data": current.has_data, "items": prospective + [
        {"id": row.id, "item_index": row.item_index, "values": json.loads(row.row_json)} for row in added_rows]}
    _record_change(session, current, before, after,
                   editor=editor, operation=operation, edit_session_id=edit_session_id)
    session.flush()
    return current


def refresh_validation(session: Session, current: FileCurrentDataRecord) -> None:
    from document_pipeline_api.models import ExtractionRecord, TaskRecord
    from document_pipeline_api.services.rule_evaluation import evaluate_rules
    from document_pipeline_api.services.processing_input import input_scope_issues
    from document_pipeline_api.schemas.extraction import DocumentKind

    extraction = session.get(ExtractionRecord, current.task_id)
    if extraction is None:
        return
    issues = []
    if current.has_data:
        from document_pipeline_api.services.templates import get_template_version
        needs_rules = extraction.document_kind != "custom" or bool(get_template_version(
            session, extraction.template_id, extraction.template_version,
        ).deterministic_rules)
        if needs_rules:
            evaluation = evaluate_rules(session, document_kind=DocumentKind(extraction.document_kind),
                                        result=validation_result(extraction, extraction_result(session, extraction)),
                                        template_id=extraction.template_id, template_version=extraction.template_version)
            issues = evaluation.issues
        issues += input_scope_issues(extraction.input_scope_json)
    # An earlier ignore was attached to earlier values. Changed facts receive a
    # fresh evaluation under the pinned rules, from either editing entry point.
    current.validation_json = encode([issue.model_dump() for issue in issues])
    task = session.get(TaskRecord, current.task_id)
    if task is not None and task.status in {"completed", "needs_review"}:
        task.status = "needs_review" if issues else "completed"
        task.updated_at = current.updated_at
    session.execute(update(DataRowRecord).where(DataRowRecord.task_id == current.task_id)
                    .values(review_pending=bool(issues)))


def extraction_result(session: Session, extraction):
    """Project current facts to the existing extraction response contract."""
    from document_pipeline_api.schemas.extraction import TemplateExtraction

    snapshot = current_snapshot(session, extraction.task_id)
    items = [item["values"] for item in snapshot["items"]]
    return TemplateExtraction(header=snapshot["header"], items=items)


def validation_result(extraction, payload):
    """Pinned legacy rules keep their old field names, not their own current data."""
    from document_pipeline_api.schemas.extraction import DocumentExtraction, LineItem
    from document_pipeline_api.services.data_baseline_audit import ITEM_ALIASES
    if extraction.document_kind == "custom":
        return payload
    aliases = {value: key for key, value in ITEM_ALIASES.items()}
    return DocumentExtraction.model_validate({**{key: None for key in DocumentExtraction.model_fields if key != "items"},
                                              **{key: value for key, value in payload.header.items() if key in DocumentExtraction.model_fields}, "items": [
        {**{key: None for key in LineItem.model_fields}, **{aliases.get(key, key): value for key, value in item.items() if aliases.get(key, key) in LineItem.model_fields}} for item in payload.items
    ]})
