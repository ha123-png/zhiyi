"""Explicit scalar conversions at the spreadsheet boundary, never at edit time."""
import math
import re
from datetime import date, datetime, time

from fastapi import HTTPException


def infer_import_type(values) -> str:
    kinds = set()
    for value in values:
        if value is None or isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, bool):
            kinds.add("boolean")
        elif type(value) in (int, float):
            kinds.add("number")
        elif isinstance(value, datetime):
            kinds.add("date" if value.time() == time() else "text")
        elif isinstance(value, date):
            kinds.add("date")
        else:
            # Textual identifiers such as 00123 must remain text. Do not guess
            # types from CSV strings or a label that happens to say 'amount'.
            kinds.add("text")
    return kinds.pop() if len(kinds) == 1 else "text"


def import_cell(value, kind: str, *, label: str, row_index: int):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, (dict, list)):
        raise HTTPException(422, f"第 {row_index} 条数据的“{label}”包含对象或列表，不能保存为普通单元格。")
    try:
        if kind == "text":
            if isinstance(value, (datetime, date)):
                return value.isoformat()
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)
        if kind == "number":
            if isinstance(value, bool):
                raise ValueError()
            if isinstance(value, str):
                text = value.strip()
                if not re.fullmatch(r"[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text):
                    raise ValueError()
                text = text.replace(",", "")
                value = int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError()
            return value
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                if value.strip().casefold() in {"true", "是", "1"}:
                    return True
                if value.strip().casefold() in {"false", "否", "0"}:
                    return False
            raise ValueError()
        if kind == "date":
            if isinstance(value, datetime):
                if value.time() != time():
                    raise ValueError()
                value = value.date()
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, str):
                return date.fromisoformat(value.strip()).isoformat()
            raise ValueError()
        raise ValueError()
    except (ValueError, TypeError, OverflowError) as error:
        raise HTTPException(422, f"第 {row_index} 条数据的“{label}”不符合目标列类型（{kind}），本次未导入。请修正原文件后重试。") from error
