import json
from datetime import timezone
from itertools import chain, groupby
from tempfile import SpooledTemporaryFile
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from sqlalchemy import select
from starlette.background import BackgroundTask

from document_pipeline_api.models import (
    DataRowRecord, DataRowRevisionRecord, DataTableRecord, ExtractionRecord,
    FileCurrentDataRecord, FileRevisionDetailRecord, FileRevisionRecord, TaskRecord,
)
from document_pipeline_api.schemas.input_scope import InputScope
from document_pipeline_api.services.excel_values import safe_excel_value
from document_pipeline_api.services.read_snapshot import begin_read_snapshot


def _append_rows(sheet, rows, *, widths=(), version_sections=False):
    fill = PatternFill("solid", fgColor="F0FDF4")
    font = Font(bold=True, color="065F46")
    alignment = Alignment(vertical="top", wrap_text=True)
    for column, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for row_number, values in enumerate(rows, 1):
        if row_number > 1_048_576:
            raise HTTPException(422, "附加资料超过 Excel 工作表行数上限。")
        if not values:
            continue
        heading = values[0] in ("版本", "位置") if version_sections else row_number == 1
        for column, value in enumerate(values, 1):
            if value is None:
                continue
            cell = sheet.cell(row_number, column, safe_excel_value(str(value) if type(value) in (int, float) else value))
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = alignment
        if heading:
            for column in range(1, len(widths) + 1):
                cell = sheet.cell(row_number, column)
                cell.fill = fill
                cell.font = font


def _version_heading(revision):
    editor = {"extraction": "自动提取", "user": "用户"}.get(revision.editor, revision.editor)
    action = {"extraction": "初次提取", "migration": "升级时的数据基线", "edit": "修订",
              "update": "修改", "delete": "删除", "add": "新增", "clear": "清空"}.get(revision.operation)
    version = f"V{revision.version}" + (f" · {action}" if action else "")
    time = revision.created_at
    if time.tzinfo is not None:
        time = time.astimezone(timezone.utc)
    return ("版本", version, time.strftime("%Y-%m-%d %H:%M:%S UTC"), editor)


def _task_history(session, task, table, revisions):
    yield ("文件", task.display_filename)
    yield ("原表", table)
    has_revision = False
    for revision, _, _ in revisions:
        if revision is None:
            continue
        has_revision = True
        yield ()
        yield _version_heading(revision)
        yield ("位置", "字段", "修改前", "修改后")
        yield from _file_revision_rows(session, revision)
    if not has_revision:
        yield ("暂无修订记录",)


def _file_revision_rows(session, revision):
    schema = {field["key"]: field["label"] for field in json.loads(revision.schema_json)}
    changes = json.loads(revision.changes_json)
    for key, change in changes.get("header", {}).items():
        yield ("文件级", schema.get(key, key), change["before"], change["after"])
    for detail in session.scalars(select(FileRevisionDetailRecord.change_json)
            .where(FileRevisionDetailRecord.revision_id == revision.id)
            .order_by(FileRevisionDetailRecord.item_index, FileRevisionDetailRecord.row_id)):
        change = json.loads(detail)
        previous, after = change.get("before"), change.get("after")
        deleted = change.get("deleted_before")
        item = after or deleted or previous
        if deleted is not None and previous is not None:
            yield from _field_deltas(f"明细 {item['item_index']} · 删除前修改", schema, previous["values"], deleted["values"])
        action = " · 删除" if after is None else " · 新增" if previous is None else ""
        yield from _field_deltas(f"明细 {item['item_index']}{action}", schema,
                      (deleted or previous or {}).get("values", {}), (after or {}).get("values", {}))
    if validation := changes.get("validation"):
        def messages(issues):
            return "\n".join(issue.get("message", "") for issue in issues or [])
        yield ("文件级", "校验状态", messages(validation["before"]), messages(validation["after"]))


def _independent_history(session, table_ids):
    records = session.execute(
        select(DataRowRevisionRecord, DataTableRecord.name, DataTableRecord.columns_json)
        .join(DataTableRecord, DataTableRecord.id == DataRowRevisionRecord.table_id)
        .where(DataRowRevisionRecord.table_id.in_(table_ids), DataRowRevisionRecord.task_id.is_(None))
        .order_by(DataRowRevisionRecord.table_id, DataRowRevisionRecord.row_id, DataRowRevisionRecord.version,
                  DataRowRevisionRecord.id)
    )
    for index, (_, revisions) in enumerate(groupby(records, key=lambda row: (row[0].table_id, row[0].row_id)), 1):
        for revision, table, schema_json in revisions:
            schema = {field["key"]: field["label"] for field in json.loads(schema_json or "[]")}
            previous = json.loads(revision.before_json or "{}")
            after = json.loads(revision.after_json or "{}")
            source = (after or previous).get("source_filename") or "手工记录"
            yield ()
            yield _version_heading(revision)
            yield ("原表 / 来源", table, source, f"独立记录 {index}")
            yield ("位置", "字段", "修改前", "修改后")
            yield from _field_deltas("记录", schema, previous, after)


def _field_deltas(position, schema, previous, after):
    for key in dict.fromkeys([*previous, *after]):
        if key.startswith("__") or key in {"source_filename", "item_index"}:
            continue
        before_value, after_value = previous.get(key), after.get(key)
        if type(before_value) is not type(after_value) or before_value != after_value:
            yield (position, schema.get(key, key), before_value, after_value)


def _add_history_sheets(workbook, session, table_ids):
    from document_pipeline_api.services.data_tables import _unique_sheet_name

    directory = workbook.create_sheet("修订目录")
    directory.freeze_panes = "A2"
    used = {"修订目录", "提取范围", "独立记录"}
    files = (select(FileRevisionRecord, TaskRecord, DataTableRecord.name)
             .select_from(FileCurrentDataRecord)
             .join(TaskRecord, TaskRecord.id == FileCurrentDataRecord.task_id)
             .join(DataTableRecord, DataTableRecord.id == FileCurrentDataRecord.table_id)
             .outerjoin(FileRevisionRecord, FileRevisionRecord.task_id == FileCurrentDataRecord.task_id)
             .where(FileCurrentDataRecord.table_id.in_(table_ids))
             .order_by(DataTableRecord.name, TaskRecord.filename, TaskRecord.id, FileRevisionRecord.version))
    widths = (25, 30, 43, 43)

    def entries():
        yield ("原表", "文件 / 来源", "工作表")
        for _, rows in groupby(session.execute(files), key=lambda row: row[1].id):
            first = next(rows)
            _, task, original = first
            title = _unique_sheet_name(task.display_filename.strip(" '"), used)
            sheet = workbook.create_sheet(title)
            sheet.freeze_panes = "A3"
            _append_rows(sheet, _task_history(session, task, original, chain((first,), rows)), widths=widths, version_sections=True)
            yield (original, task.display_filename, title)
        independent = _independent_history(session, table_ids)
        if (first := next(independent, None)) is not None:
            sheet = workbook.create_sheet("独立记录")
            _append_rows(sheet, chain((first,), independent), widths=widths, version_sections=True)
            yield (None, "独立数据修订", sheet.title)

    _append_rows(directory, entries(), widths=(26, 52, 35))
    for row in directory.iter_rows(min_row=2, min_col=3, max_col=3):
        cell = row[0]
        title = cell.value.replace("'", "''")
        cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{title}'!A1", display=cell.value)
        cell.font = Font(color="065F46", underline="single")


def _scope_rows(table, filename, encoded):
    if not encoded:
        yield (table, filename, "未记录提取范围", None)
        return
    scope = InputScope.model_validate_json(encoded)
    if scope.coverage == "complete":
        single_image = len(scope.selected) == 1 and scope.selected[0].kind == "frame" and scope.total_units == 1
        yield (table, filename, "整张图片" if single_image else "完整读取可读取内容", None)
    else:
        reasons = {"input_budget": "达到读取上限", "images_disabled": "未开启图片读取",
                   "unsupported_image": "暂不支持的图片", "unsupported_structure": "暂不支持的文档部分"}
        for part in scope.selected:
            yield (table, filename, "已读取 " + part.label(), None)
        for part in scope.omitted:
            yield (table, filename, "未读取 " + part.location.label(), reasons[part.reason])
    for note in scope.notes:
        for offset in range(0, len(note), 30_000):
            yield (table, filename, None, note[offset:offset + 30_000])


def _scopes(session, table_ids):
    yield ("原表", "文件 / 来源", "提取范围", "说明")
    files = (select(DataTableRecord.name, TaskRecord, ExtractionRecord.input_scope_json)
             .select_from(FileCurrentDataRecord)
             .join(DataTableRecord, DataTableRecord.id == FileCurrentDataRecord.table_id)
             .join(TaskRecord, TaskRecord.id == FileCurrentDataRecord.task_id)
             .outerjoin(ExtractionRecord, ExtractionRecord.task_id == FileCurrentDataRecord.task_id)
             .where(FileCurrentDataRecord.table_id.in_(table_ids)))
    for table, task, scope in session.execute(files.order_by(DataTableRecord.name, TaskRecord.filename, TaskRecord.id)):
        yield from _scope_rows(table, task.display_filename, scope)
    imported = (select(DataTableRecord.name, DataRowRecord.row_json, DataRowRecord.input_scope_json)
                .join(DataTableRecord, DataTableRecord.id == DataRowRecord.table_id)
                .where(DataRowRecord.table_id.in_(table_ids), DataRowRecord.task_id.is_(None),
                       DataRowRecord.input_scope_json.is_not(None)).order_by(DataRowRecord.id))
    seen = set()
    for table, row, scope in session.execute(imported):
        values = json.loads(row)
        name = values.get("source_filename") or "独立导入"
        key = (table, values.get("__row_group"), name, scope)
        if key not in seen:
            seen.add(key)
            yield from _scope_rows(table, name, scope)


def export_table_notes(session, table_id, *, revisions, input_scope):
    if not (revisions or input_scope):
        raise HTTPException(422, "请选择要导出的附加资料。")
    begin_read_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(404, "没有找到这个数据表。")
    table_ids = table.merge_sources or [table.id]
    workbook = Workbook()
    workbook.remove(workbook.active)
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        if revisions:
            _add_history_sheets(workbook, session, table_ids)
        if input_scope:
            sheet = workbook.create_sheet("提取范围")
            sheet.freeze_panes = "A2"
            _append_rows(sheet, _scopes(session, table_ids), widths=(26, 46, 42, 55))
        workbook.save(output)
        output.seek(0)
    except BaseException:
        output.close()
        raise
    finally:
        workbook.close()
    name = quote(f"{table.name}-附加资料.xlsx")
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
                             background=BackgroundTask(output.close))
