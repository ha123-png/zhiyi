"""Check original XLSX numeric tokens after the caller's ZIP/size budgets."""

import math
import zipfile
from xml.etree import ElementTree as ET

from openpyxl.utils.cell import coordinate_to_tuple
from openpyxl.worksheet._read_only import ReadOnlyWorksheet

from document_pipeline_api.schemas.json_numbers import NumericPrecisionError, number_from_token

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ROW_TAG, CELL_TAG = f"{{{MAIN}}}row", f"{{{MAIN}}}c"


class XlsxNumericError(ValueError):
    """A workbook cannot supply a lossless ordinary numeric value."""


class _CheckedXml:
    def __init__(self, source, checkpoint):
        self.source, self.checkpoint = source, checkpoint

    def read(self, size=-1):
        self.checkpoint()
        data = self.source.read(size)
        self.checkpoint()
        return data


def _reader_date_styles(sheet):
    # Central adapter for pinned openpyxl 3.1.5. Reuse its chosen ZIP member
    # and parsed style indices instead of interpreting workbook metadata twice.
    if not isinstance(sheet, ReadOnlyWorksheet):
        raise ValueError("需要已加载的只读工作表")
    parent = sheet.parent
    styles = getattr(parent, "_date_formats", None)
    if (getattr(parent, "read_only", False) is not True
        or not isinstance(getattr(parent, "_archive", None), zipfile.ZipFile)
        or not isinstance(getattr(sheet, "_worksheet_path", None), str)
        or not sheet._worksheet_path or not callable(getattr(sheet, "_get_source", None))
        or not isinstance(styles, set) or any(type(index) is not int or index < 0 for index in styles)):
        raise ValueError("只读工作表读取状态不可用")
    # parse_cell converts only indices in _date_formats; _timedelta_formats
    # selects the conversion mode but does not independently enable conversion.
    return frozenset(styles)


def validate_xlsx_numbers(sheet, *, min_row=1, max_row=None, min_column=1,
                          max_column=None, formulas_as_text=False, check_cancelled=None):
    """Validate selected numeric cells without changing values or unrelated sheets.

    Excel date/time serials retain openpyxl's existing conversion precision.
    Formula caches are ignored only when the caller reads the formula expression.
    No XML member is extracted to disk, and worksheet XML is streamed.
    Return the greatest column coordinate observed in the requested row window;
    previews can use it when the workbook does not declare its dimensions.
    """
    interruption = None

    def checkpoint():
        nonlocal interruption
        if check_cancelled:
            try:
                check_cancelled()
            except Exception as error:
                interruption = error
                raise

    try:
        checkpoint()
        date_styles = _reader_date_styles(sheet)
        row_number = column_number = 0
        observed_column = 0
        active_row, ancestors = None, []
        with sheet._get_source() as stream:
            for event, element in ET.iterparse(_CheckedXml(stream, checkpoint), events=("start", "end")):
                if event == "start":
                    parent = ancestors[-1] if ancestors else None
                    ancestors.append(element)
                    # parse_row treats every direct child as a cell, regardless
                    # of its tag/namespace. Reject ambiguous children explicitly.
                    if active_row is not None and parent is active_row and element.tag != CELL_TAG:
                        raise ValueError("行包含非单元格子节点")
                    if element.tag == ROW_TAG:
                        if active_row is not None:
                            raise ValueError("工作表包含嵌套行")
                        active_row = element
                        row_number = int(element.get("r", row_number + 1))
                        column_number = 0
                        if row_number < 1:
                            raise ValueError("行号无效")
                        checkpoint()
                        if max_row is not None and row_number > max_row:
                            break
                    continue
                parent = ancestors[-2] if len(ancestors) > 1 else None
                if active_row is not None and parent is active_row:
                    coordinate = element.get("r")
                    row, column_number = coordinate_to_tuple(coordinate) if coordinate else (row_number, column_number + 1)
                    # Normal worksheets place by cell coordinates; read-only
                    # worksheets yield by the parent row. Neither may hide a value.
                    if row != row_number or column_number < 1:
                        raise ValueError("单元格坐标与所属行不一致")
                    if row >= min_row and (max_row is None or row <= max_row):
                        observed_column = max(observed_column, column_number)
                    value = element.find(f"{{{MAIN}}}v")
                    token = value.text.strip() if value is not None and value.text else ""
                    selected = row >= min_row and (max_row is None or row <= max_row) and column_number >= min_column and (max_column is None or column_number <= max_column)
                    numeric = element.get("t", "n") == "n"
                    formula = element.find(f"{{{MAIN}}}f") is not None
                    if selected and numeric and token and not (formulas_as_text and formula):
                        try:
                            if int(element.get("s", "0")) in date_styles:
                                # Calendar/time rendering retains the reader's contract.
                                if not math.isfinite(float(token)):
                                    raise NumericPrecisionError("nonfinite date serial")
                            else:
                                number_from_token(token)
                        except (NumericPrecisionError, ValueError, OverflowError) as error:
                            raise XlsxNumericError(
                                f"工作表“{sheet.title}”单元格 {coordinate or f'第{row}行第{column_number}列'} 的数值精度超出支持范围，无法无损读取。请核对原值，或将编号等内容明确保存为文本单元格后重试。"
                            ) from error
                    element.clear()
                elif element is active_row:
                    element.clear()
                    active_row = None
                ancestors.pop()
        return observed_column
    except XlsxNumericError:
        raise
    except (ET.ParseError, AttributeError, KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile) as error:
        if error is interruption:
            raise
        # The shared archive may raise from the caller's checked reader before
        # our next checkpoint. Give cancellation/deadline state precedence.
        checkpoint()
        raise XlsxNumericError("Excel 工作表结构损坏，无法核对原始数值。") from error
