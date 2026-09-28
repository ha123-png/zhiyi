"""Check logical workbook size before openpyxl allocates merged/dense cells."""
import re
from xml.etree.ElementTree import iterparse, ParseError
from openpyxl.utils.cell import range_boundaries


def check_workbook_budget(archive, *, max_rows: int, max_columns: int,
                          max_cells: int = 1_000_000, max_merged_cells: int = 250_000):
    grid_cells = merged_cells = explicit_cells = merged_count = 0
    try:
        for info in archive.infolist():
            if not re.fullmatch(r"xl/worksheets/[^/]+\.xml", info.filename):
                continue
            last_row = last_column = 0
            with archive.open(info) as source:
                for _, element in iterparse(source, events=("end",)):
                    kind = element.tag.rsplit("}", 1)[-1]
                    if kind in {"dimension", "mergeCell", "c"}:
                        coordinate = element.get("r" if kind == "c" else "ref", "")
                        left, top, right, bottom = range_boundaries(coordinate)
                        if None in (left, top, right, bottom) or left < 1 or top < 1 or right < left or bottom < top:
                            raise ValueError("Excel 包含无法识别的单元格范围。")
                        if bottom > max_rows + 1 or right > max_columns:
                            raise ValueError(f"Excel 范围超过 {max_rows} 条数据或 {max_columns} 列，已停止读取。")
                        last_row, last_column = max(last_row, bottom), max(last_column, right)
                        if grid_cells + last_row * last_column > max_cells:
                            raise ValueError("Excel 逻辑网格过大，请删除多余空白范围或拆分文件。")
                        if kind == "mergeCell":
                            merged_count += 1
                            merged_cells += (right - left + 1) * (bottom - top + 1)
                            if merged_cells > max_merged_cells or merged_count > 10_000:
                                raise ValueError("Excel 合并单元格范围过大，请拆分后导入。")
                        if kind == "c":
                            explicit_cells += 1
                            if explicit_cells > max_cells:
                                raise ValueError("Excel 单元格数量过多，请拆分后导入。")
                    element.clear()
            grid_cells += last_row * last_column
    except (ParseError, TypeError) as error:
        raise ValueError("Excel 工作表结构损坏。") from error
