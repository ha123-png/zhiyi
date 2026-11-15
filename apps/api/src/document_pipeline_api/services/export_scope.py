"""Keep model input coverage visible without changing spreadsheet facts."""
import json
import re

from document_pipeline_api.services.current_data import row_values
from document_pipeline_api.schemas.input_scope import InputScope

SCOPE_KEY = "__zhiyi_input_scope"
SCOPE_LABEL = "知意输入范围（非业务字段）"
REVIEW_KEY = "__zhiyi_review_pending"
REVIEW_LABEL = "知意来源状态（非业务字段）"


def table_has_pending_reviews(session, table_id, *, predicates=(), include_sources=False):
    from sqlalchemy import select
    from document_pipeline_api.models import DataRowRecord
    membership = () if include_sources else (DataRowRecord.table_id == table_id,)
    return session.scalar(select(DataRowRecord.id).where(*membership, DataRowRecord.review_pending.is_(True), *predicates).limit(1)) is not None


def imported_pending_review(value):
    if value in (None, "", False):
        return None
    if value is True or value in ("true", "True", "待核对"):
        return True
    raise ValueError("知意来源状态标记损坏，未导入。")


def table_has_input_scopes(session, table_id, *, predicates=(), include_sources=False):
    from sqlalchemy import select
    from document_pipeline_api.models import DataRowRecord
    membership = () if include_sources else (DataRowRecord.table_id == table_id,)
    return session.scalar(select(DataRowRecord.id).where(*membership, DataRowRecord.input_scope_json.is_not(None), *predicates).limit(1)) is not None


def export_row_values(record):
    values = row_values(record)
    if record.review_pending:
        if REVIEW_KEY in values:
            raise ValueError("业务字段与知意来源状态保留字段冲突，未导出。")
        values[REVIEW_KEY] = True
    if record.input_scope_json:
        if SCOPE_KEY in values:
            raise ValueError("业务字段与知意输入范围保留字段冲突，未导出。")
        values[SCOPE_KEY] = InputScope.model_validate_json(record.input_scope_json).model_dump(mode="json")
    return values


def csv_scope_value(values):
    scope = values.get(SCOPE_KEY)
    return json.dumps(scope, ensure_ascii=False) if scope else ""


def workbook_business_sheets(workbook):
    """Only exclude positively identified metadata, never a user's sheet by name."""
    signature = ["来源编号", "原上传文件名", "输入覆盖", "类别", "原始范围或说明", "原因"]
    candidates = {sheet.title for sheet in workbook if sheet.max_column >= 6 and [sheet.cell(1, index).value for index in range(1, 7)] == signature}
    referenced = set()
    if candidates:
        for sheet in workbook:
            if sheet.title in candidates:
                continue
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.comment and cell.comment.author == "知意":
                        match = re.search(r"来源编号 (\d+)，实际提供和遗漏的范围见「(.+)」工作表。", cell.comment.text)
                        if match:
                            referenced.add(match[2])
    business = []
    for sheet in workbook:
        marker = sheet.cell(1, 1).comment
        metadata = sheet.title in candidates and (sheet.title in referenced or (
            marker and marker.author == "知意" and marker.text == "zhiyi:input-scope-v1"))
        if not metadata and any(cell.value is not None for row in sheet.iter_rows() for cell in row):
            business.append(sheet.title)
    return business


def read_workbook_scopes(workbook, business_sheet):
    """Restore only explicitly linked metadata, never infer complete coverage."""
    result = {}
    cache = {}
    for row in business_sheet.iter_rows(min_row=2):
        for cell in row:
            comment = cell.comment
            if not comment or comment.author != "知意":
                continue
            match = re.search(r"来源编号 (\d+)，实际提供和遗漏的范围见「(.+)」工作表。", comment.text)
            if not match:
                continue
            source_id, title = int(match[1]), match[2]
            if title not in cache:
                if title not in workbook.sheetnames:
                    raise ValueError("Excel 的知意输入范围说明页已丢失，无法保留来源范围。")
                sources = {}
                for record in workbook[title].iter_rows(min_row=3, values_only=True):
                    if len(record) >= 6 and record[3] == "内部范围记录 v1":
                        identifier, text, part = record[0], record[4], record[5]
                        if not isinstance(identifier, int) or not isinstance(part, int) or not isinstance(text, str) or not text.startswith("json:"):
                            raise ValueError("Excel 输入范围记录损坏。")
                        parts = sources.setdefault(identifier, {})
                        if part in parts:
                            raise ValueError("Excel 输入范围记录包含重复片段。")
                        parts[part] = text[5:]
                decoded = {}
                for identifier, parts in sources.items():
                    if sorted(parts) != list(range(1, len(parts) + 1)):
                        raise ValueError("Excel 输入范围记录缺少片段。")
                    decoded[identifier] = InputScope.model_validate_json("".join(parts[index] for index in sorted(parts))).model_dump_json()
                cache[title] = decoded
            if source_id not in cache[title]:
                raise ValueError("Excel 的来源批注找不到对应范围记录。")
            value = cache[title][source_id]
            if cell.row in result and result[cell.row] != value:
                raise ValueError("Excel 同一数据行关联了冲突的输入范围。")
            result[cell.row] = value
    return result
