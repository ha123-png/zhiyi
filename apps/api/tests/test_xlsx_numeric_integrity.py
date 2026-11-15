"""Preserve XLSX numeric lexemes at real import and original-read boundaries."""

import copy
import hashlib
import json
import zipfile
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from threading import Event
from time import monotonic
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from sqlalchemy import func, select

from document_pipeline_api.config import Settings
from document_pipeline_api.models import DataRowRecord, DataTableRecord, TaskRecord
from document_pipeline_api.services.data_tables import ImportFileError, parse_import_rows
from document_pipeline_api.services.document_preview import PREVIEW_SECONDS, _CheckedReader, _deadline, xlsx_window
from document_pipeline_api.services.file_formats import UnsupportedTextFileError, _extract_xlsx_text
from document_pipeline_api.services.input_scope import xlsx_units
from document_pipeline_api.services.xlsx_numbers import XlsxNumericError, validate_xlsx_numbers
from test_table_management import _client

LOSSY = ["0.10000000000000001", "999999999999999.99", "1e-400"]
BIG = "123456789012345678901234567890"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
BUDGET = 2 * 1024 * 1024
STRUCTURAL_CASES = [
    "foreign_cell", "cell_row_differs_from_parent", "duplicate_relationship",
    "style_index_namespace", "duplicate_cellxfs", "ambiguous_workbook_part",
    "duplicate_sheets_container",
]


def raw_workbook(token, *, text=False, formula=False, date_format=None, second=False, coordinate="A2", blank=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "目标页"
    sheet.append(["amount"])
    sheet[coordinate] = token if text else 17
    if date_format:
        sheet[coordinate].number_format = date_format
    if second:
        workbook.create_sheet("普通页").append(["amount"])
        workbook["普通页"].append([21.5])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(buffer.getvalue())) as source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml" and not text:
                root = ET.fromstring(data)
                cell = root.find('.//{%s}c[@r="%s"]' % (NS, coordinate))
                cell.find("{%s}v" % NS).text = None if blank else token
                if formula:
                    ET.SubElement(cell, "{%s}f" % NS).text = "1/10"
                data = ET.tostring(root, encoding="utf-8")
            target.writestr(info, data)
    return output.getvalue()


@contextmanager
def numeric_sheet(content):
    with BytesIO(content) as source:
        workbook = load_workbook(source, read_only=True, data_only=True)
        try:
            yield workbook["目标页"]
        finally:
            workbook.close()


def ambiguous_workbook(case, token=LOSSY[0]):
    date_format = "yyyy-mm-dd" if case in {"style_index_namespace", "duplicate_cellxfs"} else None
    raw = raw_workbook(token, date_format=date_format)
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as archive, zipfile.ZipFile(output, "w") as target:
        decoy = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        decoy.find('.//{%s}c[@r="A2"]/{%s}v' % (NS, NS)).text = "21.50"
        for info in archive.infolist():
            data = archive.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                cell = root.find('.//{%s}c[@r="A2"]' % NS)
                if case == "foreign_cell":
                    cell.tag = "{urn:synthetic-extension}c"
                elif case == "non_cell_child":
                    cell.tag = "{%s}unexpected" % NS
                elif case == "cell_row_differs_from_parent":
                    cell.set("r", "A900")
                data = ET.tostring(root, encoding="utf-8")
            elif info.filename == "xl/styles.xml" and date_format:
                root = ET.fromstring(data)
                styles = root.find("{%s}cellXfs" % NS)
                if case == "style_index_namespace":
                    plain = copy.deepcopy(styles[0])
                    plain.tag = "{urn:synthetic-extension}xf"
                    styles.insert(1, plain)
                    styles.set("count", "3")
                else:
                    replacement = copy.deepcopy(styles)
                    replacement[1].set("numFmtId", "0")
                    root.append(replacement)
                data = ET.tostring(root, encoding="utf-8")
            elif info.filename == "[Content_Types].xml" and case == "ambiguous_workbook_part":
                root = ET.fromstring(data)
                original = next(item for item in root if item.get("ContentType") == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
                original.set("ContentType", "application/vnd.ms-excel.sheet.macroEnabled.main+xml")
                root.insert(0, ET.Element(original.tag, {"PartName": "/xl/decoy.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"}))
                data = ET.tostring(root, encoding="utf-8")
            elif info.filename == "xl/workbook.xml" and case == "duplicate_sheets_container":
                root = ET.fromstring(data)
                sheets = copy.deepcopy(root.find("{%s}sheets" % NS))
                sheets[0].set("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "rIdDecoy")
                root.insert(0, sheets)
                data = ET.tostring(root, encoding="utf-8")
            elif info.filename == "xl/_rels/workbook.xml.rels" and case in {"duplicate_relationship", "duplicate_sheets_container"}:
                root = ET.fromstring(data)
                relation = copy.deepcopy(next(item for item in root if item.get("Type", "").endswith("/worksheet")))
                relation.set("Target", "/xl/worksheets/decoy.xml")
                if case == "duplicate_relationship":
                    root.insert(0, relation)
                else:
                    relation.set("Id", "rIdDecoy")
                    root.append(relation)
                data = ET.tostring(root, encoding="utf-8")
            target.writestr(info, data)
        if case == "ambiguous_workbook_part":
            target.writestr("xl/decoy.xml", archive.read("xl/workbook.xml"))
            target.writestr("xl/_rels/decoy.xml.rels", archive.read("xl/_rels/workbook.xml.rels").replace(b"/xl/worksheets/sheet1.xml", b"/xl/worksheets/decoy.xml"))
        if case in {"duplicate_relationship", "ambiguous_workbook_part", "duplicate_sheets_container"}:
            target.writestr("xl/worksheets/decoy.xml", ET.tostring(decoy, encoding="utf-8"))
    return output.getvalue()


def duplicate_title_workbook(with_chart):
    workbook = Workbook()
    first = workbook.active
    first.title = "Data"
    for sheet, value in [(first, 1), (workbook.create_sheet("Data1"), 1), (workbook.create_sheet("Data2"), 2)]:
        sheet.append(["amount"])
        sheet.append([value])
    if with_chart:
        chart = BarChart()
        chart.add_data(Reference(first, min_col=1, min_row=2, max_row=2))
        workbook.create_chartsheet("统计", index=1).add_chart(chart)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(buffer.getvalue())) as archive, zipfile.ZipFile(output, "w") as target:
        for info in archive.infolist():
            data = archive.read(info.filename)
            if info.filename == "xl/workbook.xml":
                root = ET.fromstring(data)
                sheets = [sheet for sheet in root.find("{%s}sheets" % NS) if sheet.get("name") != "统计"]
                for sheet, title in zip(sheets, ["Data", "Data", "Data1"]):
                    sheet.set("name", title)
                data = ET.tostring(root, encoding="utf-8")
            elif info.filename == "xl/worksheets/sheet2.xml":
                root = ET.fromstring(data)
                root.find('.//{%s}c[@r="A2"]/{%s}v' % (NS, NS)).text = LOSSY[0]
                data = ET.tostring(root, encoding="utf-8")
            target.writestr(info, data)
    return output.getvalue()


def state(client):
    with client.app.state.session_factory() as session:
        return {
            "tables": [(row.id, row.columns_json) for row in session.scalars(select(DataTableRecord).order_by(DataTableRecord.id))],
            "rows": [(row.id, row.row_version, row.row_json) for row in session.scalars(select(DataRowRecord).order_by(DataRowRecord.id))],
        }


@pytest.mark.parametrize("token", LOSSY)
@pytest.mark.parametrize("destination", ["new", "number", "text"])
@pytest.mark.parametrize("formula", [False, True])
def test_raw_xlsx_lossy_numbers_are_rejected_before_any_import_write(tmp_path, token, destination, formula):
    with _client(tmp_path) as client:
        if destination == "new":
            url = "/api/v1/tables/import-new"
        else:
            base = client.post("/api/v1/tables/import-new", files={"file": ("base.xlsx", raw_workbook("base" if destination == "text" else "21.50", text=destination == "text"))})
            assert base.status_code == 201, base.text
            url = f"/api/v1/tables/{base.json()['id']}/import"
        before = state(client)
        response = client.post(url, files={"file": ("exact-token.xlsx", raw_workbook(token, formula=formula))})
        assert response.status_code == 422, response.text
        assert "精度" in response.json()["detail"] and "A2" in response.json()["detail"]
        assert state(client) == before


@pytest.mark.parametrize("token,expected", [("0.1", 0.1), ("21.50", 21.5), (BIG, int(BIG)), ("-0", 0)])
def test_supported_xlsx_numbers_keep_numeric_storage_and_export_boundaries(tmp_path, token, expected):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/tables/import-new", files={"file": ("supported.xlsx", raw_workbook(token))})
        assert response.status_code == 201, response.text
        table_id = response.json()["id"]
        with client.app.state.session_factory() as session:
            row = session.scalar(select(DataRowRecord).where(DataRowRecord.table_id == table_id))
            values = {key: value for key, value in json.loads(row.row_json).items() if not key.startswith("_")}
        assert list(values.values()) == [expected]
        assert type(next(iter(values.values()))) is type(expected)
        exported = client.get(f"/api/v1/tables/{table_id}/export.json").json()
        assert all(exported[0][key] == value for key, value in values.items())
        assert client.get(f"/api/v1/tables/{table_id}/export.xlsx").status_code == (422 if token == BIG else 200)


@pytest.mark.parametrize("token", ["00123", LOSSY[0], BIG])
def test_actual_text_cells_preserve_digits_in_new_text_columns(tmp_path, token):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/tables/import-new", files={"file": ("text.xlsx", raw_workbook(token, text=True))})
        assert response.status_code == 201, response.text
        detail = client.get('/api/v1/tables/' + response.json()['id']).json()
        column = detail["columns"][0]
        assert column["value_type"] == "text"
        assert detail["rows"][0]["values"][column["key"]] == token


def test_sheet_selection_precedes_numeric_validation_and_only_selected_sheet_is_checked(tmp_path):
    with _client(tmp_path) as client:
        content = raw_workbook(LOSSY[0], second=True)
        files = {"file": ("selected.xlsx", content)}
        assert client.post("/api/v1/tables/import-new", files=files).status_code == 409
        good = client.post("/api/v1/tables/import-new", files=files, data={"sheet_name": "普通页"})
        assert good.status_code == 201, good.text
        before = state(client)
        bad = client.post("/api/v1/tables/import-new", files=files, data={"sheet_name": "目标页"})
        assert bad.status_code == 422 and "精度" in bad.json()["detail"]
        assert state(client) == before


@pytest.mark.parametrize("formula", [False, True])
@pytest.mark.parametrize("token,number_format,expected", [
    ("45292", "yyyy-mm-dd", datetime(2024, 1, 1)),
    ("45292.5", "yyyy-mm-dd hh:mm:ss", datetime(2024, 1, 1, 12)),
])
def test_legitimate_date_serials_and_cached_dates_keep_existing_date_semantics(token, number_format, expected, formula):
    rows = parse_import_rows(raw_workbook(token, date_format=number_format, formula=formula), "date.xlsx")
    assert rows[0]["amount"] == expected


def test_empty_numeric_cell_is_not_an_unsupported_number():
    rows = parse_import_rows(raw_workbook("", blank=True), "blank.xlsx")
    assert not rows or rows[0]["amount"] is None


@pytest.mark.parametrize("reader", ["original_text", "model_units", "window"])
@pytest.mark.parametrize("token", LOSSY)
def test_original_readers_reject_unsupported_numeric_lexemes(tmp_path, reader, token):
    path = tmp_path / "source.xlsx"
    path.write_bytes(raw_workbook(token))
    with pytest.raises(UnsupportedTextFileError, match="精度"):
        if reader == "original_text":
            _extract_xlsx_text(path, max_uncompressed_bytes=BUDGET, max_rows=100, max_columns=32)
        elif reader == "model_units":
            list(xlsx_units(path, Settings(database_url="sqlite://", storage_dir=tmp_path, max_import_uncompressed_bytes=BUDGET)))
        else:
            xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)


def test_formula_text_reader_does_not_validate_an_unused_cached_number(tmp_path):
    path = tmp_path / "formula.xlsx"
    path.write_bytes(raw_workbook(LOSSY[0], formula=True))
    assert [unit.text for unit in xlsx_units(path, Settings(database_url="sqlite://", storage_dir=tmp_path))] == ["amount", "=1/10"]


def test_window_does_not_reject_unselected_sheet_rows_or_columns(tmp_path):
    path = tmp_path / "window.xlsx"
    path.write_bytes(raw_workbook(LOSSY[0], coordinate="T100", second=True))
    assert xlsx_window(path, 1, 0, 0, max_uncompressed_bytes=BUDGET)["sheets"][0]["rows"][1] == ["21.5"]
    assert xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)["sheets"][0]["rows"][0][0] == "amount"
    assert xlsx_window(path, 0, 99, 0, max_uncompressed_bytes=BUDGET)["sheets"][0]["rows"][0] == [""] * 16
    with pytest.raises(UnsupportedTextFileError, match="精度"):
        xlsx_window(path, 0, 99, 16, max_uncompressed_bytes=BUDGET)


@pytest.mark.parametrize("token", LOSSY)
def test_actual_upload_refuses_bad_numeric_source_before_model_or_task_write(tmp_path, token):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/tasks", files={"file": ("source.xlsx", raw_workbook(token), XLSX_MEDIA_TYPE)},
                               data={"template_id": "builtin-invoice"})
        assert response.status_code == 422 and "精度" in response.json()["detail"]
        with client.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(TaskRecord)) == 0


def test_actual_existing_original_window_reports_precision_error(tmp_path):
    with _client(tmp_path) as client:
        data = raw_workbook(LOSSY[0])
        path = client.app.state.settings.storage_dir / "old-xlsx.xlsx"
        path.write_bytes(data)
        with client.app.state.session_factory() as session:
            session.add(TaskRecord(id="old-xlsx", filename="old.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), storage_path="old-xlsx.xlsx", status="completed"))
            session.commit()
        response = client.get("/api/v1/tasks/old-xlsx/preview/xlsx")
        assert response.status_code == 422 and "精度" in response.json()["detail"]
        assert path.read_bytes() == data


@pytest.mark.parametrize("target_type", ["number", "text"])
@pytest.mark.parametrize("token", ["00123", LOSSY[0]])
def test_true_numeric_strings_follow_the_explicit_destination_type(tmp_path, target_type, token):
    with _client(tmp_path) as client:
        base = client.post("/api/v1/tables/import-new", files={"file": ("base.xlsx", raw_workbook("seed" if target_type == "text" else "21.50", text=target_type == "text"))})
        table_id = base.json()["id"]
        before = state(client)
        response = client.post(f"/api/v1/tables/{table_id}/import", files={"file": ("text.xlsx", raw_workbook(token, text=True))})
        if target_type == "number" and token == LOSSY[0]:
            assert response.status_code == 422 and state(client) == before
        else:
            assert response.status_code == 200, response.text
            detail = client.get(f"/api/v1/tables/{table_id}").json()
            key = detail["columns"][0]["key"]
            assert detail["rows"][-1]["values"][key] == (123 if target_type == "number" else token)


@pytest.mark.parametrize("failure", [InterruptedError("synthetic cancellation"), UnsupportedTextFileError("合成读取超时")])
def test_raw_xml_validation_preserves_cancellation_and_deadline_errors(failure):
    def cancelled():
        raise failure

    with numeric_sheet(raw_workbook("21.50")) as sheet, pytest.raises(type(failure)) as raised:
        validate_xlsx_numbers(sheet, check_cancelled=cancelled)
    assert raised.value is failure


def test_raw_guard_resolves_sheet_relationship_instead_of_assuming_sheet_index(tmp_path):
    source = raw_workbook(LOSSY[0], second=True)
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(source)) as archive, zipfile.ZipFile(output, "w") as target:
        for info in archive.infolist():
            data = archive.read(info.filename)
            name = info.filename
            if name == "xl/worksheets/sheet1.xml":
                name = "xl/worksheets/custom-name.xml"
            elif name == "xl/_rels/workbook.xml.rels":
                data = data.replace(b"/xl/worksheets/sheet1.xml", b"worksheets/custom-name.xml")
            elif name == "[Content_Types].xml":
                data = data.replace(b"/xl/worksheets/sheet1.xml", b"/xl/worksheets/custom-name.xml")
            target.writestr(name, data)
    with _client(tmp_path) as client:
        files = {"file": ("renamed.xlsx", output.getvalue())}
        assert client.post("/api/v1/tables/import-new", files=files, data={"sheet_name": "普通页"}).status_code == 201
        response = client.post("/api/v1/tables/import-new", files=files, data={"sheet_name": "目标页"})
        assert response.status_code == 422 and "A2" in response.json()["detail"]


def test_foreign_namespace_row_cannot_hide_selected_numeric_cells(tmp_path):
    data = raw_workbook(LOSSY[0])
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as archive, zipfile.ZipFile(output, "w") as target:
        for info in archive.infolist():
            body = archive.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(body)
                root.insert(0, ET.Element("{urn:synthetic-extension}row", {"r": "1000000"}))
                body = ET.tostring(root, encoding="utf-8")
            target.writestr(info, body)
    path = tmp_path / "extension.xlsx"
    path.write_bytes(output.getvalue())
    with pytest.raises(UnsupportedTextFileError, match="精度"):
        xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)


@pytest.mark.parametrize("case", STRUCTURAL_CASES)
@pytest.mark.parametrize("destination", ["new", "number", "text"])
def test_ambiguous_xlsx_numbers_are_rejected_before_import_writes(tmp_path, case, destination):
    with _client(tmp_path) as client:
        if destination == "new":
            url = "/api/v1/tables/import-new"
        else:
            base = client.post("/api/v1/tables/import-new", files={"file": ("base.xlsx", raw_workbook("seed" if destination == "text" else "21.50", text=destination == "text"))})
            assert base.status_code == 201, base.text
            url = f"/api/v1/tables/{base.json()['id']}/import"
        before = state(client)
        response = client.post(url, files={"file": ("ambiguous.xlsx", ambiguous_workbook(case))})
        assert response.status_code == 422, response.text
        assert any(word in response.json()["detail"] for word in ["精度", "结构"])
        assert state(client) == before


@pytest.mark.parametrize("case", STRUCTURAL_CASES)
@pytest.mark.parametrize("reader", ["original_text", "model_units", "window"])
def test_original_readers_do_not_bypass_numeric_validation_on_ambiguous_xlsx(tmp_path, case, reader):
    path = tmp_path / "ambiguous.xlsx"
    content = ambiguous_workbook(case)
    path.write_bytes(content)
    with pytest.raises(UnsupportedTextFileError, match="精度|结构"):
        if reader == "original_text":
            _extract_xlsx_text(path, max_uncompressed_bytes=BUDGET, max_rows=100, max_columns=32)
        elif reader == "model_units":
            list(xlsx_units(path, Settings(database_url="sqlite://", storage_dir=tmp_path)))
        else:
            xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)
    assert path.read_bytes() == content


@pytest.mark.parametrize("case", STRUCTURAL_CASES)
def test_ambiguous_numeric_upload_is_rejected_before_task_creation(tmp_path, case):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/tasks", files={"file": ("ambiguous.xlsx", ambiguous_workbook(case), XLSX_MEDIA_TYPE)},
                               data={"template_id": "builtin-invoice"})
        assert response.status_code == 422, response.text
        assert any(word in response.json()["detail"] for word in ["精度", "结构"])
        with client.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(TaskRecord)) == 0


@pytest.mark.parametrize("case", ["foreign_cell", "non_cell_child", "cell_row_differs_from_parent"])
def test_invalid_row_structure_is_rejected_even_for_supported_numeric_tokens(case):
    with numeric_sheet(ambiguous_workbook(case, "21.50")) as sheet:
        with pytest.raises(XlsxNumericError, match="结构"):
            validate_xlsx_numbers(sheet, max_row=32)


@pytest.mark.parametrize("with_chart", [False, True])
def test_selected_duplicate_title_uses_the_same_worksheet_in_both_read_modes(tmp_path, with_chart):
    content = duplicate_title_workbook(with_chart)
    assert parse_import_rows(content, "titles.xlsx", sheet_name="Data")[0]["amount"] == 1
    assert parse_import_rows(content, "titles.xlsx", sheet_name="Data11")[0]["amount"] == 2
    with _client(tmp_path) as client:
        before = state(client)
        response = client.post("/api/v1/tables/import-new", files={"file": ("titles.xlsx", content)},
                               data={"sheet_name": "Data1"})
        assert response.status_code == 422 and "精度" in response.json()["detail"]
        assert state(client) == before


@pytest.mark.parametrize("attribute", ["_date_formats", "_archive"])
def test_raw_reader_adapter_rejects_missing_private_state(monkeypatch, attribute):
    with numeric_sheet(raw_workbook("21.50")) as sheet, monkeypatch.context() as patch:
        patch.delattr(sheet.parent, attribute)
        with pytest.raises(XlsxNumericError, match="结构"):
            validate_xlsx_numbers(sheet)


@pytest.mark.parametrize("mode", ["implicit_coordinates", "unused_cell_outside_row"])
def test_raw_guard_follows_actual_row_membership_and_implicit_coordinates(tmp_path, mode):
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(raw_workbook("21.50"))) as archive, zipfile.ZipFile(output, "w") as target:
        for info in archive.infolist():
            data = archive.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                row = root.find('.//{%s}row[@r="2"]' % NS)
                if mode == "implicit_coordinates":
                    row.attrib.pop("r")
                    row[0].attrib.pop("r")
                else:
                    unused = copy.deepcopy(row[0])
                    unused.find("{%s}v" % NS).text = LOSSY[0]
                    root.insert(0, unused)
                data = ET.tostring(root, encoding="utf-8")
            target.writestr(info, data)
    path = tmp_path / "compatible.xlsx"
    path.write_bytes(output.getvalue())
    with numeric_sheet(path.read_bytes()) as sheet:
        validate_xlsx_numbers(sheet)
        assert list(sheet.values)[1] == (21.5,)
    # The import ZIP budget separately requires explicit cell coordinates.
    # This control covers implicit coordinates at the raw reader/guard boundary.
    if mode != "implicit_coordinates":
        assert parse_import_rows(path.read_bytes(), path.name)[0]["amount"] == 21.5
        assert xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)["sheets"][0]["rows"][1] == ["21.5"]


def test_supported_formula_cache_is_read_without_evaluating_the_formula():
    assert parse_import_rows(raw_workbook("0.1", formula=True), "formula.xlsx")[0]["amount"] == 0.1


def test_formula_cache_check_does_not_trust_a_truncated_dimension():
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(raw_workbook("", formula=True, blank=True, coordinate="A900"))) as archive, zipfile.ZipFile(output, "w") as target:
        for info in archive.infolist():
            data = archive.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                root.find("{%s}dimension" % NS).set("ref", "A1:A1")
                data = ET.tostring(root, encoding="utf-8")
            target.writestr(info, data)
    with pytest.raises(ImportFileError, match="公式"):
        parse_import_rows(output.getvalue(), "formula.xlsx")


@pytest.mark.parametrize("failure", ["cancel", "deadline"])
def test_shared_checked_archive_preserves_interruption_during_source_read(failure):
    class TriggeredReader(BytesIO):
        trigger = None
        tripped = False

        def read(self, *args):
            if self.trigger is not None:
                self.tripped = True
                self.trigger()
            return super().read(*args)

    cancelled = Event()
    with TriggeredReader(raw_workbook("21.50")) as source:
        checked = _CheckedReader(source, monotonic(), cancelled)
        workbook = load_workbook(checked, read_only=True, data_only=True)
        try:
            if failure == "cancel":
                source.trigger = cancelled.set
                message = "已关闭原件预览"
            else:
                source.trigger = lambda: setattr(checked, "started", monotonic() - PREVIEW_SECONDS - 1)
                message = "耗时过长"
            with pytest.raises(UnsupportedTextFileError, match=message):
                validate_xlsx_numbers(workbook["目标页"],
                                      check_cancelled=lambda: _deadline(checked.started, cancelled))
            assert source.tripped
        finally:
            workbook.close()
