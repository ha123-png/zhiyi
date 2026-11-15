from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree

import pytest
from openpyxl import Workbook, load_workbook

from document_pipeline_api.services import data_tables


def _workbook(sheet_path, *, merged, relationship_type="worksheet"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Value"])
    sheet.append(["synthetic"])
    if merged:
        sheet.merge_cells("A2:A101")
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    changed = BytesIO()
    old = "xl/worksheets/sheet1.xml"
    with ZipFile(BytesIO(buffer.getvalue())) as source, ZipFile(changed, "w") as target:
        for entry in source.infolist():
            content = source.read(entry)
            name = entry.filename
            if name == old:
                name = sheet_path
            if name in {"xl/_rels/workbook.xml.rels", "[Content_Types].xml"}:
                content = content.replace(f"/{old}".encode(), f"/{sheet_path}".encode())
            if name == "xl/_rels/workbook.xml.rels":
                content = content.replace(b'/worksheet"', f'/{relationship_type}"'.encode())
            target.writestr(name, content)
    return changed.getvalue()


@pytest.mark.parametrize("sheet_path,relationship_type", [
    ("xl/custom/sheet1.xml", "worksheet"),
    ("custom/sheet.bin", "worksheet"),
    ("xl/worksheets/nested/sheet1.xml", "worksheet"),
    ("xl/custom/sheet1.xml", "other-sheet"),
])
def test_related_sheet_is_budgeted_before_dense_allocation(monkeypatch, sheet_path, relationship_type):
    payload = _workbook(sheet_path, merged=True, relationship_type=relationship_type)

    def forbidden(*args, **kwargs):
        pytest.fail("Workbook allocated before structural budget check")

    monkeypatch.setattr(data_tables, "load_workbook", forbidden)
    with pytest.raises(data_tables.ImportFileError, match="Excel 范围超过"):
        data_tables.parse_import_rows(payload, "small.xlsx", max_rows=2, max_columns=2)


def test_small_related_sheet_outside_conventional_directory_remains_importable():
    payload = _workbook("custom/sheet.bin", merged=False)

    rows = data_tables.parse_import_rows(payload, "small.xlsx", max_rows=2, max_columns=2)

    assert len(rows) == 1
    assert rows[0]["Value"] == "synthetic"


def test_repeated_sheet_target_counts_each_actual_allocation_against_budget():
    from document_pipeline_api.services.xlsx_budget import check_workbook_budget

    payload = _workbook("xl/custom/sheet1.xml", merged=False)
    changed = BytesIO()
    with ZipFile(BytesIO(payload)) as source, ZipFile(changed, "w") as target:
        for entry in source.infolist():
            content = source.read(entry)
            if entry.filename == "xl/workbook.xml":
                root = ElementTree.fromstring(content)
                sheets = root.find("{*}sheets")
                first = sheets[0]
                second = ElementTree.fromstring(ElementTree.tostring(first))
                second.set("name", "Second")
                second.set("sheetId", "2")
                sheets.append(second)
                content = ElementTree.tostring(root)
            target.writestr(entry, content)
    payload = changed.getvalue()
    workbook = load_workbook(BytesIO(payload))
    try:
        assert len(workbook.worksheets) == 2
        assert [sheet.max_row * sheet.max_column for sheet in workbook] == [2, 2]
    finally:
        workbook.close()

    with ZipFile(BytesIO(payload)) as archive:
        with pytest.raises(ValueError, match="逻辑网格过大"):
            check_workbook_budget(archive, max_rows=2, max_columns=2, max_cells=3)
