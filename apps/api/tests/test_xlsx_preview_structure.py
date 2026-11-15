"""Real workbooks keep the preview's sheet and bounded-column navigation honest."""
import hashlib

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
import pytest

from document_pipeline_api.services.document_preview import xlsx_window
from document_pipeline_api.services.file_formats import UnsupportedTextFileError


BUDGET = 4 * 1024 * 1024


@pytest.mark.parametrize('chart_first', [False, True])
def test_chart_sheets_do_not_shift_data_sheet_selection(tmp_path, chart_first):
    path = tmp_path / 'chart-and-data.xlsx'
    book = Workbook()
    data = book.active
    data.title = '真实数据页'
    data.append(['项目', '数量'])
    data.append(['合成项目', 3])
    chart = BarChart()
    chart.add_data(Reference(data, min_col=2, min_row=1, max_row=2), titles_from_data=True)
    chartsheet = book.create_chartsheet('图表页', index=0 if chart_first else None)
    chartsheet.add_chart(chart)
    book.save(path)
    book.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)

    assert result['sheet_names'] == ['真实数据页']
    assert result['sheets'][0]['name'] == '真实数据页'
    assert result['sheets'][0]['rows'] == [['项目', '数量'], ['合成项目', '3']]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with pytest.raises(UnsupportedTextFileError, match='没有找到'):
        xlsx_window(path, 1, 0, 0, max_uncompressed_bytes=BUDGET)


def test_chart_only_workbook_explains_why_grid_preview_is_unavailable(tmp_path):
    path = tmp_path / 'chart-only.xlsx'
    book = Workbook()
    book.remove(book.active)
    book.create_chartsheet('图表页').add_chart(BarChart())
    book.save(path)
    book.close()

    with pytest.raises(UnsupportedTextFileError, match='数据工作表'):
        xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)


def test_unsized_worksheet_keeps_columns_beyond_first_window_reachable(tmp_path):
    path = tmp_path / 'unsized.xlsx'
    book = Workbook(write_only=True)
    sheet = book.create_sheet('无尺寸数据')
    sheet.append([f'字段-{i}' for i in range(1, 21)])
    book.save(path)
    book.close()

    first = xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)
    assert first['next_column_offset'] == 16
    assert first['sheets'][0]['columns'] == list('ABCDEFGHIJKLMNOP')
    last = xlsx_window(path, 0, 0, first['next_column_offset'], max_uncompressed_bytes=BUDGET)
    assert last['sheets'][0]['columns'] == ['Q', 'R', 'S', 'T']
    assert last['sheets'][0]['rows'] == [[f'字段-{i}' for i in range(17, 21)]]
    assert last['next_column_offset'] is None


def test_unsized_width_is_measured_from_the_requested_row_window(tmp_path):
    path = tmp_path / 'unsized-later-wide-row.xlsx'
    book = Workbook(write_only=True)
    sheet = book.create_sheet('后续宽行')
    for _ in range(50):
        sheet.append(['窄行'])
    sheet.append([f'后续-{i}' for i in range(1, 21)])
    book.save(path)
    book.close()

    first = xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)
    assert first['next_offset'] == 50
    assert first['next_column_offset'] is None
    later = xlsx_window(path, 0, first['next_offset'], 0, max_uncompressed_bytes=BUDGET)
    assert later['next_column_offset'] == 16
    tail = xlsx_window(path, 0, 50, 16, max_uncompressed_bytes=BUDGET)
    assert tail['sheets'][0]['rows'] == [[f'后续-{i}' for i in range(17, 21)]]
