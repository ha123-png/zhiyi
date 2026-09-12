"""Bounded, deterministic head/tail selection over native file structures.

Adapters scan to the end for validation and inventory, but retain only a bounded
prefix and suffix. Scanning a source is never reported as sending it to a model.
Single paragraphs/rows remain atomic: an oversized unit is omitted explicitly,
and an empty selection is rejected instead of issuing a misleading model call.
"""

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

from document_pipeline_api.config import Settings
from document_pipeline_api.schemas.input_scope import InputScope, OmittedRange, SourceRange
from document_pipeline_api.services.file_formats import (
    UnsupportedTextFileError,
    _validate_zip_budget,
)


@dataclass(frozen=True)
class InputUnit:
    location: SourceRange
    text: str | None = None
    image_member: str | None = None
    omission: str | None = None

    @cached_property
    def payload(self) -> str:
        return f"【{self.location.label()}】\n{self.text}\n" if self.text is not None else ""

    @cached_property
    def cost(self) -> int:
        return len(self.payload) if self.text is not None else 1


@dataclass(frozen=True)
class PreparedInput:
    units: tuple[InputUnit, ...]
    scope: InputScope

    @property
    def text(self) -> str:
        return "".join(unit.payload for unit in self.units if unit.text is not None)


class InputBudgetError(ValueError):
    pass


class _Window:
    def __init__(self, budget: int):
        self.budget = budget
        self.prefix: list[tuple[int, InputUnit]] = []
        self.suffix: deque[tuple[int, InputUnit]] = deque()
        self.prefix_cost = self.suffix_cost = self.total_cost = 0
        self.prefix_closed = False

    def add(self, index: int, unit: InputUnit) -> None:
        self.total_cost += unit.cost
        if not self.prefix_closed and self.prefix_cost + unit.cost <= self.budget:
            self.prefix.append((index, unit))
            self.prefix_cost += unit.cost
        else:
            self.prefix_closed = True
        self.suffix.append((index, unit))
        self.suffix_cost += unit.cost
        while self.suffix and self.suffix_cost > self.budget:
            self.suffix_cost -= self.suffix.popleft()[1].cost

    def selected(self) -> list[tuple[int, InputUnit]]:
        if self.total_cost <= self.budget:
            return self.prefix
        head_budget = (self.budget + 1) // 2
        head: list[tuple[int, InputUnit]] = []
        cost = 0
        for pair in self.prefix:
            if cost + pair[1].cost > head_budget:
                break
            head.append(pair)
            cost += pair[1].cost
        # Give unused prefix capacity to the suffix, without exceeding the budget.
        tail_budget = self.budget - cost
        tail: list[tuple[int, InputUnit]] = []
        tail_cost = 0
        for pair in reversed(self.suffix):
            if tail_cost + pair[1].cost > tail_budget:
                break
            tail.append(pair)
            tail_cost += pair[1].cost
        return head + list(reversed(tail))


def _same_group(a: SourceRange, b: SourceRange) -> bool:
    return (a.kind, a.container, a.columns) == (b.kind, b.container, b.columns) and a.character_start is None and b.character_start is None


def _append_range(ranges: list[SourceRange], location: SourceRange) -> None:
    if ranges and _same_group(ranges[-1], location) and ranges[-1].end + 1 == location.start:
        ranges[-1] = ranges[-1].model_copy(update={"end": location.end})
    else:
        ranges.append(location)


def _subtract(location: SourceRange, selected: list[SourceRange]) -> Iterator[SourceRange]:
    cursor = location.start
    for part in selected:
        if not _same_group(location, part) or part.end < cursor or part.start > location.end:
            continue
        if part.start > cursor:
            yield location.model_copy(update={"start": cursor, "end": part.start - 1})
        cursor = max(cursor, part.end + 1)
    if cursor <= location.end:
        yield location.model_copy(update={"start": cursor})


def select_input(units: Iterable[InputUnit], *, text_budget: int, image_budget: int) -> PreparedInput:
    if text_budget < 0 or image_budget < 0:
        raise ValueError("输入预算不能为负数。")
    text_window, image_window = _Window(text_budget), _Window(image_budget)
    inventory: list[SourceRange] = []
    excluded: list[OmittedRange] = []
    total = 0
    for index, unit in enumerate(units):
        total += unit.location.end - unit.location.start + 1
        if unit.omission:
            excluded.append(OmittedRange(location=unit.location, reason=unit.omission))
            continue
        _append_range(inventory, unit.location)
        (text_window if unit.text is not None else image_window).add(index, unit)
    indexed = {index: unit for index, unit in text_window.selected() + image_window.selected()}
    chosen = tuple(indexed[index] for index in sorted(indexed))
    if not chosen or not any(unit.text is None or unit.text.strip() for unit in chosen):
        raise InputBudgetError("没有可送入模型的完整内容单元；单个段落或表格行可能超过输入预算，或图片未启用。请缩小文件内容或调整处理设置。")
    selected: list[SourceRange] = []
    for unit in chosen:
        _append_range(selected, unit.location)
    omitted = excluded + [OmittedRange(location=part) for location in inventory for part in _subtract(location, selected)]
    partial = bool(omitted)
    return PreparedInput(chosen, InputScope(
        rule="head_tail_v1" if partial else "all",
        coverage="partial" if partial else "complete",
        selected=selected,
        omitted=omitted,
        selected_units=sum(part.end - part.start + 1 for part in selected),
        total_units=total,
        text_characters=sum(len(unit.payload) for unit in chosen),
        text_budget=text_budget,
        image_budget=image_budget,
        notes=["Excel 公式保留为公式文本，未调用 Office 重新计算；图片、批注等未支持部分单独列出。"] if any(unit.location.kind == "sheet_row" for unit in chosen) else [],
    ))


def select_prefix(units: Iterable[InputUnit], *, limited: bool, text_limit: int,
                  image_limit: int, row_limit: int) -> PreparedInput:
    """Select a continuous prefix, never fill a gap with later content.

    Continue inventory after the boundary for truthful omission reporting.
    Text offsets describe an oversized paragraph/line without corrupting files.
    """
    chosen: list[InputUnit] = []
    selected: list[SourceRange] = []
    omitted: list[OmittedRange] = []
    characters = images = rows = total = 0
    stopped = False
    for unit in units:
        total += unit.location.end - unit.location.start + 1
        if unit.omission:
            omitted.append(OmittedRange(location=unit.location, reason=unit.omission))
            continue
        if unit.text is not None and not unit.text.strip():
            continue
        if stopped:
            if omitted and omitted[-1].reason == "input_budget":
                locations = [omitted[-1].location]
                _append_range(locations, unit.location)
                if len(locations) == 1:
                    omitted[-1] = OmittedRange(location=locations[0])
                    continue
            omitted.append(OmittedRange(location=unit.location))
            continue
        is_row = unit.location.kind == "sheet_row"
        length = len(unit.text) if unit.text is not None else 0
        exceeds = limited and (characters + length > text_limit or
            (unit.text is None and images >= image_limit) or (is_row and rows >= row_limit))
        if exceeds:
            remaining = text_limit - characters
            if remaining > 0 and unit.text is not None and unit.location.kind in {"line", "paragraph"}:
                location = unit.location.model_copy(update={"character_start": 1, "character_end": remaining})
                chosen.append(InputUnit(location, text=unit.text[:remaining]))
                selected.append(location)
                characters += remaining
                omitted.append(OmittedRange(location=unit.location.model_copy(update={
                    "character_start": remaining + 1, "character_end": length})))
            else:
                omitted.append(OmittedRange(location=unit.location))
            stopped = True
            continue
        chosen.append(unit)
        _append_range(selected, unit.location)
        characters += length
        images += int(unit.text is None)
        rows += int(is_row)
    if not chosen:
        raise InputBudgetError("从开头读取后没有可发送的内容。首个表格行可能超过文字上限，请提高读取上限或关闭限制后重试。")
    return PreparedInput(tuple(chosen), InputScope(
        version=2, rule="prefix_v1" if limited else "all",
        coverage="partial" if omitted else "complete", selected=selected, omitted=omitted,
        selected_units=sum(r.end-r.start+1 for r in selected), total_units=total,
        text_characters=characters, text_budget=text_limit if limited else 0,
        image_budget=image_limit if limited else 0,
        notes=["已按工作簿顺序读取；公式保留为原始公式文字，未重新计算。"] if rows else []))


def text_units(path: Path) -> Iterator[InputUnit]:
    # Validate the entire encoding first, so a malformed tail cannot be accepted
    # after an early UTF-8 prefix. Two streaming passes use bounded memory.
    encoding = None
    for candidate in ("utf-8-sig", "gbk"):
        try:
            with path.open(encoding=candidate) as source:
                while source.read(65536):
                    pass
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if encoding is None:
        raise UnsupportedTextFileError("文本文件编码无法识别，仅支持 UTF-8 或 GBK。")
    found = False
    with path.open(encoding=encoding) as source:
        for line_number, line in enumerate(source, 1):
            text = line.rstrip("\r\n")
            found = found or bool(text.strip())
            yield InputUnit(SourceRange(kind="line", start=line_number, end=line_number), text=text)
    if not found:
        raise UnsupportedTextFileError("文档没有可提取的正文内容。")


def xlsx_units(path: Path, settings: Settings) -> Iterator[InputUnit]:
    from openpyxl import load_workbook

    _validate_zip_budget(path, max_uncompressed_bytes=settings.max_import_uncompressed_bytes)
    with zipfile.ZipFile(path) as archive:
        unsupported_parts = [name for name in sorted(archive.namelist()) if name.startswith(("xl/drawings/", "xl/comments")) and name.endswith(".xml")]
    with path.open("rb") as source:
        try:
            workbook = load_workbook(source, read_only=True, data_only=False)
        except Exception as error:
            raise UnsupportedTextFileError("Excel 文件损坏或不是有效的 .xlsx 文件。") from error
        try:
            total_rows = 0
            for sheet in workbook.worksheets:
                # Reject abusive dimensions before iter_rows allocates a wide row.
                width = sheet.max_column or 1
                if width > settings.max_import_columns:
                    raise UnsupportedTextFileError(f"Excel 工作表超过安全上限 {settings.max_import_columns} 列。")
                if (sheet.max_row or 0) + total_rows > settings.max_import_rows:
                    raise UnsupportedTextFileError(f"Excel 文档超过安全上限 {settings.max_import_rows} 行。")
                # Some producers write A1:A1 even when later cells exist. Do not
                # trust that hint as a data boundary: read the actual worksheet XML.
                sheet.reset_dimensions()
                for number, row in enumerate(sheet.iter_rows(), 1):
                    total_rows += 1
                    if total_rows > settings.max_import_rows or len(row) > settings.max_import_columns:
                        raise UnsupportedTextFileError("Excel 文档超过安全行列上限。")
                    # Formulas are included explicitly, never silently replaced by
                    # null when the file has no cached calculation result.
                    values = ["" if cell.value is None else str(cell.value) for cell in row]
                    yield InputUnit(SourceRange(kind="sheet_row", container=sheet.title,
                                                start=number, end=number, columns=max(1, len(row))), text="\t".join(values))
        finally:
            workbook.close()
    for name in unsupported_parts:
        yield InputUnit(SourceRange(kind="document_part", container=name, start=1, end=1), omission="unsupported_structure")


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _paragraph_text(element) -> str:
    return "".join(node.text or "" if node.tag == f"{_W}t" else "\t" if node.tag == f"{_W}tab" else "\n" if node.tag in {f"{_W}br", f"{_W}cr"} else "" for node in element.iter())


def docx_units(path: Path, settings: Settings, *, include_images: bool) -> Iterator[InputUnit]:
    _validate_zip_budget(path, max_uncompressed_bytes=settings.max_import_uncompressed_bytes)
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            rels = ET.fromstring(archive.read("word/_rels/document.xml.rels")) if "word/_rels/document.xml.rels" in archive.namelist() else []
            image_map = {rel.get("Id"): rel.get("Target", "") for rel in rels if rel.get("TargetMode") != "External"}
            members = set(archive.namelist())
            unsupported_parts = [name for name in sorted(members) if name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes", "word/comments")) and name.endswith(".xml")]
    except (KeyError, ET.ParseError, zipfile.BadZipFile) as error:
        raise UnsupportedTextFileError("Word 文档结构损坏，无法读取正文。") from error
    body = root.find(f"{_W}body")
    if body is None:
        raise UnsupportedTextFileError("Word 文档没有正文。")
    paragraph = table = image_number = 0
    section = None
    for block_number, element in enumerate(body, 1):
        if element.tag == f"{_W}p":
            paragraph += 1
            text = _paragraph_text(element)
            style = element.find(f"{_W}pPr/{_W}pStyle")
            style_name = style.get(f"{_W}val", "").lower() if style is not None else ""
            if text.strip() and ("heading" in style_name or "标题" in style_name):
                section = f"章节：{text.strip()[:80]}"
            yield InputUnit(SourceRange(kind="paragraph", container=section, start=paragraph, end=paragraph), text=text)
        elif element.tag == f"{_W}tbl":
            table += 1
            for number, row in enumerate(element.findall(f"{_W}tr"), 1):
                cells = row.findall(f"{_W}tc")
                if number > settings.max_import_rows or len(cells) > settings.max_import_columns:
                    raise UnsupportedTextFileError("Word 表格超过安全行列上限。")
                text = "\t".join("\n".join(_paragraph_text(p) for p in cell.iter(f"{_W}p")) for cell in cells)
                yield InputUnit(SourceRange(kind="table_row", container=f"表格 {table}", start=number, end=number), text=text)
        else:
            if element.tag != f"{_W}sectPr":
                yield InputUnit(SourceRange(kind="document_part", container=f"Word 正文结构 {element.tag.rsplit('}', 1)[-1]}", start=1, end=1), omission="unsupported_structure")
            continue
        for drawing in element.iter(f"{_A}blip"):
            image_number += 1
            target = image_map.get(drawing.get(f"{_R}embed"), "")
            # ZIP member lookup only; never extract an arbitrary relationship path.
            member = f"word/{target}"
            supported = member in members and Path(member).suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"}
            omission = "images_disabled" if not include_images else None if supported else "unsupported_image"
            yield InputUnit(SourceRange(kind="image", start=image_number, end=image_number), image_member=member if supported else None, omission=omission)
        if any(node.tag in {f"{_W}pict", f"{_W}object"} or node.tag.endswith("}oMath") for node in element.iter()):
            yield InputUnit(SourceRange(kind="document_part", container=f"Word 正文块 {block_number} 中的旧式图片、对象或公式", start=1, end=1), omission="unsupported_structure")
    for name in unsupported_parts:
        yield InputUnit(SourceRange(kind="document_part", container=name, start=1, end=1), omission="unsupported_structure")


def native_units(path: Path, content_type: str, settings: Settings, *, include_images: bool = False) -> Iterator[InputUnit]:
    if content_type in {"text/plain", "text/markdown"}:
        yield from text_units(path)
    elif content_type.endswith("spreadsheetml.sheet"):
        yield from xlsx_units(path, settings)
    elif content_type.endswith("wordprocessingml.document"):
        yield from docx_units(path, settings, include_images=include_images)
    else:
        raise UnsupportedTextFileError("这个类型没有原生文本读取器。")


def visual_units(count: int, *, frames: bool = False) -> Iterator[InputUnit]:
    for number in range(1, count + 1):
        yield InputUnit(SourceRange(kind="frame" if frames else "page", start=number, end=number))
