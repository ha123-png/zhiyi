from openpyxl import Workbook
from openpyxl.comments import Comment
import pytest

from document_pipeline_api.schemas.input_scope import InputScope, SourceRange
from document_pipeline_api.services.export_scope import read_workbook_scopes


def test_legacy_scope_sheet_import_preserves_all_long_notes():
    scope = InputScope(rule="all", coverage="complete", selected=[SourceRange(kind="line", start=1, end=2)], omitted=[], selected_units=2, total_units=2, text_characters=20, text_budget=100, image_budget=1, notes=["长" * 40000])
    workbook = Workbook()
    sheet = workbook.active
    metadata = workbook.create_sheet("知意输入范围")
    metadata.append(["来源编号", "原上传文件名", "输入覆盖", "类别", "原始范围或说明", "原因"])
    metadata.append([None, None, None, "使用说明", None, None])
    encoded = scope.model_dump_json()
    for index, offset in enumerate(range(0, len(encoded), 30000), 1):
        metadata.append([1, None, None, "内部范围记录 v1", "json:" + encoded[offset:offset + 30000], index])
    for row in (2, 3):
        sheet.cell(row, 2, "合成数据").comment = Comment(
            "局部读取。来源编号 1，实际提供和遗漏的范围见「知意输入范围」工作表。", "知意")
    restored = read_workbook_scopes(workbook, sheet)
    assert InputScope.model_validate_json(restored[2]) == scope
    assert restored[2] == restored[3]
    metadata.delete_rows(3)
    with pytest.raises(ValueError, match="缺少片段"):
        read_workbook_scopes(workbook, sheet)
