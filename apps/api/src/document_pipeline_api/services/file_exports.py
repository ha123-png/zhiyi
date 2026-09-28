"""Per-file exports. Shared field keys never imply a cross-file schema."""
import json
from tempfile import SpooledTemporaryFile
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from sqlalchemy import select
from starlette.background import BackgroundTask

from document_pipeline_api.models import DataRowRecord, ExtractionRecord
from document_pipeline_api.services.table_scope import prepare_export_selection

FILES_PER_WORKBOOK = 100


def _response(output, name, media_type, selection):
    output.seek(0)
    return StreamingResponse(output, media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", **selection.headers()},
        background=BackgroundTask(output.close))


def _cell(sheet, value, *, bold=False):
    if isinstance(value, str) and (len(value) > 32767 or ILLEGAL_CHARACTERS_RE.search(value)):
        raise HTTPException(422, "有字段内容超出 Excel 单元格的支持范围，请导出 JSON 以保留完整内容。")
    cell = WriteOnlyCell(sheet, value=value)
    if isinstance(value, str):
        cell.data_type = "s"  # Untrusted text is never an Excel formula.
    if bold:
        cell.font = Font(bold=True)
    return cell


def _workbook(session, files, output):
    from document_pipeline_api.services.data_tables import _unique_sheet_name

    workbook = Workbook(write_only=True)
    workbook.properties.subject = "zhiyi:per-file-export:v1"
    directory = workbook.create_sheet("目录")
    directory.append([_cell(directory, text, bold=True) for text in ("文件", "工作表", "明细数", "文件标识")])
    used = {"目录"}
    try:
        for current in files:
            schema = json.loads(current.schema_json)
            header = json.loads(current.header_json)
            filename = current.source.display_filename
            title = _unique_sheet_name(filename, used)
            sheet = workbook.create_sheet(title)
            sheet.column_dimensions["A"].width = 28
            sheet.column_dimensions["B"].width = 55
            sheet.append([_cell(sheet, "文件", bold=True), _cell(sheet, filename)])
            sheet.append(["文件标识", current.task_id])
            sheet.append(["上传原名", _cell(sheet, current.source.filename)])
            sheet.append(["当前数据版本", current.current_version])
            extraction = session.get(ExtractionRecord, current.task_id)
            if extraction and extraction.input_scope_json:
                sheet.append(["提取读取范围", _cell(sheet, extraction.input_scope_json)])
            if json.loads(current.validation_json):
                sheet.append(["校验状态", "仍有待处理问题，请在文件历史中查看"])
            sheet.append([])
            sheet.append([_cell(sheet, "文件级字段", bold=True), _cell(sheet, "内容", bold=True)])
            for column in schema:
                if column.get("section") == "header":
                    sheet.append([_cell(sheet, column["label"]), _cell(sheet, header.get(column["key"]))])
            sheet.append([])
            details = [column for column in schema if column.get("section") == "item"]
            sheet.append([_cell(sheet, "明细序号", bold=True), *[_cell(sheet, column["label"], bold=True) for column in details]])
            count = 0
            for row in session.scalars(select(DataRowRecord).where(
                DataRowRecord.task_id == current.task_id, DataRowRecord.is_header_only.is_(False),
            ).order_by(DataRowRecord.item_index, DataRowRecord.id).execution_options(yield_per=500)):
                if count + len(schema) + 20 >= 1048576:
                    raise HTTPException(422, "单份文件的明细超出 Excel 工作表上限，请导出 JSON。")
                values = json.loads(row.row_json)
                sheet.append([row.item_index, *[_cell(sheet, values.get(column["key"])) for column in details]])
                count += 1
            link = _cell(directory, title)
            link.hyperlink = "#'" + title.replace("'", "''") + "'!A1"
            directory.append([_cell(directory, filename), link, count, current.task_id])
        workbook.save(output)
    finally:
        workbook.close()


def export_files_excel(session, table, *, pack=False, selection=None):
    selection = selection or prepare_export_selection(session, table.id)
    count = selection.file_count
    if count > FILES_PER_WORKBOOK and not pack:
        raise HTTPException(409, f"共 {count} 份文件，请使用 Excel 分包导出，每包最多 {FILES_PER_WORKBOOK} 份。")
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        if pack:
            with ZipFile(output, "w", ZIP_DEFLATED) as archive:
                manifest = []
                for start in range(0, max(count, 1), FILES_PER_WORKBOOK):
                    files = session.scalars(selection.files.offset(start).limit(FILES_PER_WORKBOOK)).all()
                    name = f"文件-{start + 1:05d}-{start + len(files):05d}.xlsx"
                    with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as part:
                        _workbook(session, files, part)
                        part.seek(0)
                        with archive.open(name, "w", force_zip64=True) as target:
                            import shutil
                            shutil.copyfileobj(part, target, length=1024 * 1024)
                    manifest.append({"workbook": name, "files": [{"id": file.task_id, "filename": file.source.display_filename} for file in files]})
                archive.writestr("目录.json", json.dumps({"total_files": count, "parts": manifest}, ensure_ascii=False))
            return _response(output, table.name + selection.suffix + "-Excel分包.zip", "application/zip", selection)
        _workbook(session, session.scalars(selection.files).all(), output)
        return _response(output, table.name + selection.suffix + ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", selection)
    except BaseException:
        output.close()
        raise


def build_files_json(session, table, *, selection=None):
    selection = selection or prepare_export_selection(session, table.id)
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    def write(value):
        output.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    try:
        output.write(b'{"format":"zhiyi-file-data-v1","files":[')
        first = True
        for current in session.scalars(selection.files.execution_options(yield_per=100)):
            if not first:
                output.write(b",")
            first = False
            extraction = session.get(ExtractionRecord, current.task_id)
            metadata = {"file_id": current.task_id, "filename": current.source.display_filename,
                        "original_filename": current.source.filename, "current_version": current.current_version,
                        "schema": json.loads(current.schema_json), "header": json.loads(current.header_json),
                        "validation": json.loads(current.validation_json),
                        "input_scope": json.loads(extraction.input_scope_json) if extraction and extraction.input_scope_json else None}
            output.write(json.dumps(metadata, ensure_ascii=False).encode("utf-8")[:-1])
            output.write(b',"items":[')
            first_item = True
            for row in session.scalars(select(DataRowRecord).where(
                DataRowRecord.task_id == current.task_id, DataRowRecord.is_header_only.is_(False),
            ).order_by(DataRowRecord.item_index, DataRowRecord.id).execution_options(yield_per=500)):
                if not first_item:
                    output.write(b",")
                first_item = False
                write({"id": row.id, "item_index": row.item_index, "values": json.loads(row.row_json)})
            output.write(b"]}")
        output.write(b"]}")
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise


def export_files_json(session, table, *, selection=None):
    selection = selection or prepare_export_selection(session, table.id)
    return _response(build_files_json(session, table, selection=selection),
                     table.name + selection.suffix + ".json", "application/json", selection)
