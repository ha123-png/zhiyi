"""Validation for values written to Excel's lossy cell model."""

from fastapi import HTTPException
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from decimal import Decimal, InvalidOperation
import math


_EXCEL_MAX_TEXT_LENGTH = 32767
_EXCEL_MAX_SIGNIFICANT_DIGITS = 15


def safe_excel_value(value):
    if isinstance(value, str) and (
        len(value) > _EXCEL_MAX_TEXT_LENGTH or ILLEGAL_CHARACTERS_RE.search(value)
    ):
        raise HTTPException(
            422,
            "有字段内容超出 Excel 单元格的支持范围，请导出 JSON 以保留完整内容。",
        )
    if type(value) is int:
        if abs(value) > 10**308:
            raise HTTPException(
                422,
                "有数值超出 Excel 单元格的数值范围，请导出 JSON 以保留完整数值。",
            )
        significant_digits = len(str(abs(value)).rstrip("0")) if value else 1
        if significant_digits > _EXCEL_MAX_SIGNIFICANT_DIGITS:
            raise HTTPException(
                422,
                "有数值超出 Excel 的 15 位有效数字精度，请导出 JSON 以保留完整数值。",
            )
    elif type(value) is float:
        if not math.isfinite(value):
            raise HTTPException(422, "Excel 不支持非有限数值，请先修正或导出 JSON。")
        if abs(value) >= 10**308:
            raise HTTPException(
                422,
                "有数值超出 Excel 单元格的数值范围，请导出 JSON 以保留完整数值。",
            )
        try:
            significant_digits = len(Decimal(str(value)).normalize().as_tuple().digits)
        except InvalidOperation:
            significant_digits = _EXCEL_MAX_SIGNIFICANT_DIGITS + 1
        if significant_digits > _EXCEL_MAX_SIGNIFICANT_DIGITS:
            raise HTTPException(
                422,
                "有数值超出 Excel 的 15 位有效数字精度，请导出 JSON 以保留完整数值。",
            )
    return value
