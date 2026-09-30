"""Bounded model-facing reads of current facts or explicitly requested evidence.

Row and field pages are separate: a wide document is not an invitation to fill a
model context. Long values have an exact, version-checked chunk reader. Initial
JSON is paged inside SQLite and never parsed as a whole Python document here.
"""
from __future__ import annotations

import json
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from document_pipeline_api.models import DataRowRecord, ExtractionRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.services.file_collections import file_details
from document_pipeline_api.services.read_snapshot import begin_read_snapshot
from document_pipeline_api.schemas.json_numbers import stringify_unsafe_integers

ResultView = Literal["current", "initial"]
FIELD_PAGE_SIZE = 12
VALUE_PREVIEW_CHARS = 256
RESULT_PREVIEW_CHARS = 6000


def _check_page(page: int, page_size: int, field_page: int = 1) -> None:
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 20:
        raise HTTPException(422, "page 必须是正整数，page_size 必须在 1 到 20 之间。")
    if type(field_page) is not int or field_page < 1:
        raise HTTPException(422, "field_page 必须是正整数。")


def _metadata(session: Session, task_id: str, expected_version: int | None, view: ResultView):
    begin_read_snapshot(session)
    record = session.execute(select(
        ExtractionRecord.document_kind, ExtractionRecord.template_id, ExtractionRecord.template_version,
        ExtractionRecord.model_name, ExtractionRecord.created_at,
    ).where(ExtractionRecord.task_id == task_id)).first()
    if record is None:
        raise HTTPException(404, "这个任务还没有提取结果。")
    current = session.get(FileCurrentDataRecord, task_id)
    if view == "current":
        if current is None:
            raise HTTPException(409, "这份文件尚未建立当前数据。")
        if expected_version is not None and expected_version != current.current_version:
            raise HTTPException(409, "文件数据已变化，请从第一页重新读取，避免混用不同版本。")
    elif view != "initial":
        raise HTTPException(422, "仅支持 current 或 initial。")
    return record, current


def _initial_header_expression(kind: str):
    if kind == "custom":
        return func.json_extract(ExtractionRecord.result_json, "$.header")
    return func.json_remove(ExtractionRecord.result_json, "$.items")


def _initial_page(session, task_id, kind, page, page_size):
    header = session.scalar(select(_initial_header_expression(kind)).where(ExtractionRecord.task_id == task_id))
    total = session.scalar(select(func.json_array_length(ExtractionRecord.result_json, "$.items"))
                           .where(ExtractionRecord.task_id == task_id)) or 0
    entries = func.json_each(ExtractionRecord.result_json, "$.items").table_valued("key", "value")
    rows = session.execute(select(entries.c.key, entries.c.value).select_from(ExtractionRecord)
        .join(entries, text("1=1")).where(ExtractionRecord.task_id == task_id)
        .order_by(entries.c.key).offset((page - 1) * page_size).limit(page_size)).all()
    return json.loads(header or "{}"), total, [(index, json.loads(value)) for index, value in rows]


def _initial_fields(session, task_id, kind):
    # json_tree's leaf parents identify header and detail field names, without
    # returning all the initial values to Python. Initial evidence is immutable.
    nodes = func.json_tree(ExtractionRecord.result_json).table_valued("key", "path", "type")
    section = case((nodes.c.path == ("$.header" if kind == "custom" else "$"), "header"), else_="item")
    statement = select(nodes.c.key, section).select_from(ExtractionRecord).join(nodes, text("1=1")).where(
        ExtractionRecord.task_id == task_id, nodes.c.type.not_in(["object", "array"]),
        (nodes.c.path == ("$.header" if kind == "custom" else "$")) | nodes.c.path.like("$.items[%]"),
    ).distinct()
    fields = {}
    for key, section in session.execute(statement):
        fields[(section, key)] = {"key": key, "label": key, "section": section}
    return list(fields.values())


def read_task_result_page(
    session: Session, task_id: str, *, view: ResultView = "current", page: int = 1,
    page_size: int = 3, field_page: int = 1, expected_version: int | None = None,
) -> dict:
    _check_page(page, page_size, field_page)
    if view == "current" and (page > 1 or field_page > 1) and expected_version is None:
        raise HTTPException(422, "继续读取当前数据需提供 expected_version，请先读取第一页并使用返回的 review_version。")
    record, current = _metadata(session, task_id, expected_version, view)
    task = session.get(TaskRecord, task_id)
    if view == "current":
        details = file_details(session, task_id, page=page, page_size=page_size)
        header, total, schema = details["header"], details["total"], details["schema"]
        rows = [(page_size * (page - 1) + index, item["values"]) for index, item in enumerate(details["items"])]
        identities = [{"id": item["id"], "item_index": item["item_index"], "version": item["version"]}
                      for item in details["items"]]
    else:
        header, total, rows = _initial_page(session, task_id, record.document_kind, page, page_size)
        schema = _initial_fields(session, task_id, record.document_kind)
        identities = [{"item_index": index + 1} for index, _ in rows]
    selected_fields = schema[(field_page - 1) * FIELD_PAGE_SIZE:field_page * FIELD_PAGE_SIZE]
    allowed = {section: {field["key"] for field in selected_fields if field.get("section") == section}
               for section in ("header", "item")}
    remaining = RESULT_PREVIEW_CHARS
    truncated = []

    def preview(values, section, path):
        nonlocal remaining
        result = {}
        for key, value in values.items():
            if key not in allowed[section]:
                continue
            if isinstance(value, str):
                limit = min(VALUE_PREVIEW_CHARS, max(remaining, 0))
                if len(value) > limit:
                    truncated.append([*path, key])
                value = value[:limit]
                remaining -= len(value)
            result[key] = value
        return result

    result = {"header": preview(header, "header", ["header"]),
              "items": [preview(values, "item", ["items", index]) for index, values in rows]}
    # Validation is metadata, not an unbounded second copy of model output.
    issue_column = FileCurrentDataRecord.validation_json if view == "current" else ExtractionRecord.validation_json
    owner = FileCurrentDataRecord.task_id if view == "current" else ExtractionRecord.task_id
    issue_count = session.scalar(select(func.json_array_length(issue_column)).where(owner == task_id)) or 0
    issue_rows = func.json_each(issue_column).table_valued("key", "value")
    issues = []
    issue_records = session.execute(select(issue_rows.c.key, issue_rows.c.value).select_from(owner.table)
        .join(issue_rows, text("1=1")).where(owner == task_id).order_by(issue_rows.c.key)
        .offset((page - 1) * page_size).limit(page_size))
    for index, encoded in issue_records:
        issue = json.loads(encoded)
        for key, value in issue.items():
            if isinstance(value, str):
                length = min(VALUE_PREVIEW_CHARS, max(remaining, 0))
                if len(value) > length:
                    truncated.append(["validation_issues", index, key])
                issue[key] = value[:length]
                remaining -= len(issue[key])
        issues.append(issue)
    return stringify_unsafe_integers({
        "task_id": task_id, "filename": task.display_filename, "view": view,
        "document_kind": record.document_kind, "template_id": record.template_id,
        "template_version": record.template_version, "model_name": record.model_name,
        "review_version": current.current_version if view == "current" else None,
        "has_current_data": current.has_data if current is not None else False,
        "result": result, "details": identities, "detail_ids": [item["id"] for item in identities if "id" in item],
        "schema": [{key: field[key] for key in ("key", "label", "section", "value_type") if key in field}
                   for field in selected_fields],
        "page": page, "page_size": page_size, "total": total,
        "has_more": page * page_size < total, "next_page": page + 1 if page * page_size < total else None,
        "field_page": field_page, "field_page_size": FIELD_PAGE_SIZE, "field_count": len(schema),
        "next_field_page": field_page + 1 if field_page * FIELD_PAGE_SIZE < len(schema) else None,
        "issue_count": issue_count, "validation_issues": issues,
        "next_issue_page": page + 1 if page * page_size < issue_count else None,
        "truncated_paths": truncated,
        "note": ("当前事实。按页和字段页读取；后续读取带 review_version 作为 expected_version。"
                 if view == "current" else "初次提取证据，非当前数据；用户修订和删除不会改变这份证据。")
                + "truncated_paths 中的值不是完整值，用 get_task_result_value 按 offset 读取；items 路径的数字是从 0 开始的全文件位置。",
    })


def read_task_result_value(
    session: Session, task_id: str, path: list[str | int], *, view: ResultView = "current",
    offset: int = 0, limit: int = 2000, expected_version: int | None = None,
) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 2000:
        raise HTTPException(422, "offset 不能小于 0，limit 必须在 1 到 2000 之间。")
    valid_header = len(path) == 2 and path[0] == "header"
    valid_item = len(path) == 3 and path[0] == "items" and type(path[1]) is int and path[1] >= 0
    valid_issue = len(path) == 3 and path[0] == "validation_issues" and type(path[1]) is int and path[1] >= 0
    if not (valid_header or valid_item or valid_issue) or not isinstance(path[-1], str) or not 1 <= len(path[-1]) <= 128:
        raise HTTPException(422, "path 应为 [header,字段key]、[items,从0开始的明细位置,字段key] 或 [validation_issues,从0开始的位置,字段key]。")
    if view == "current" and expected_version is None:
        raise HTTPException(422, "读取完整当前值需提供 expected_version，请先读取文件当前数据页。")
    record, current = _metadata(session, task_id, expected_version, view)
    if valid_issue:
        column = FileCurrentDataRecord.validation_json if view == "current" else ExtractionRecord.validation_json
        owner = FileCurrentDataRecord.task_id if view == "current" else ExtractionRecord.task_id
        source = select(func.json_extract(column, f"$[{path[1]}]")).where(owner == task_id).scalar_subquery()
    elif view == "initial":
        parent = _initial_header_expression(record.document_kind) if valid_header else func.json_extract(
            ExtractionRecord.result_json, f"$.items[{path[1]}]")
        source = select(parent).where(ExtractionRecord.task_id == task_id).scalar_subquery()
    elif valid_header:
        source = select(FileCurrentDataRecord.header_json).where(FileCurrentDataRecord.task_id == task_id).scalar_subquery()
    else:
        source = select(DataRowRecord.row_json).where(DataRowRecord.task_id == task_id, DataRowRecord.is_header_only.is_(False))\
            .order_by(DataRowRecord.item_index, DataRowRecord.id).offset(path[1]).limit(1).scalar_subquery()
    # Match the key as a bound value, not interpolated JSONPath or executable SQL.
    fields = func.json_each(source).table_valued("key", "value", "type")
    selected = session.execute(select(fields.c.type, func.length(fields.c.value),
        case((fields.c.type == "text", func.substr(fields.c.value, offset + 1, limit)), else_=fields.c.value))
        .where(fields.c.key == path[-1])).first()
    if selected is None:
        raise HTTPException(404, "该位置没有这个字段，请核对字段页和明细位置。")
    kind, length, value = selected
    if kind != "text":
        if offset:
            raise HTTPException(422, "只有文本字段支持 offset 分块读取。")
        value = None if kind == "null" else kind == "true" if kind in {"true", "false"} else value
    length = length or 0
    more = kind == "text" and offset + limit < length
    return stringify_unsafe_integers({"task_id": task_id, "view": view, "path": path, "value": value, "value_type": kind,
            "version": current.current_version if view == "current" else None,
            "offset": offset, "total_characters": length, "next_offset": offset + limit if more else None,
            "complete": not more and offset == 0})
