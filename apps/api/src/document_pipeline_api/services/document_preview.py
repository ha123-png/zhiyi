"""Read-only, bounded windows of originals. These limits do not affect extraction.

No preview cache contains user files. Each request validates its own source and
returns one window; closing/changing a preview therefore cannot retain a stale
original from another task. Office XML is streamed rather than assembled first.
"""
from __future__ import annotations

import codecs
from io import BytesIO
from pathlib import Path
import time
import zipfile
from xml.parsers import expat
from threading import Event
from typing import BinaryIO

from PIL import Image, ImageOps

from .file_formats import (
    UnsupportedTextFileError,
    _docx_rels_map,
    _excel_column_name,
    _validate_zip_budget,
)

TEXT_WINDOW_BYTES = 32_768
BLOCK_WINDOW = 40
ROW_WINDOW = 50
COLUMN_WINDOW = 16
CELL_CHARS = 256
PARAGRAPH_CHARS = 4_096
PREVIEW_DECODE_PIXELS = 24_000_000
PREVIEW_SECONDS = 8.0
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def text_window(path: Path, offset: int = 0, *, cancelled: Event | None = None) -> dict:
    """Offsets are byte boundaries returned by this function, not character counts."""
    _deadline(time.monotonic(), cancelled)
    size = path.stat().st_size
    with path.open("rb") as source:
        # Detect from a bounded prefix. UTF-8 BOM and UTF-16 remain readable too.
        prefix = source.read(TEXT_WINDOW_BYTES)
        if prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16-le" if prefix.startswith(b"\xff\xfe") else "utf-16-be"
        else:
            try:
                codecs.getincrementaldecoder("utf-8")().decode(prefix, final=False)
                encoding = "utf-8"
            except UnicodeDecodeError:
                encoding = "gb18030"
        source.seek(min(offset, size))
        raw = source.read(TEXT_WINDOW_BYTES)
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    text = decoder.decode(raw, final=offset + len(raw) >= size)
    pending, _ = decoder.getstate()
    next_offset = min(size, offset + len(raw) - len(pending))
    if offset == 0:
        text = text.lstrip("\ufeff")
    return {"text": text, "offset": min(offset, size),
            "next_offset": next_offset if next_offset < size else None,
            "truncated": next_offset < size, "total_bytes": size}


def _deadline(start: float, cancelled: Event | None = None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise UnsupportedTextFileError("已关闭原件预览。")
    if time.monotonic() - start > PREVIEW_SECONDS:
        raise UnsupportedTextFileError("这份文件的预览读取耗时过长，请在原件中查看。")


class _CheckedReader:
    """Allow cancellation while openpyxl skips rows before the requested window."""

    def __init__(self, source: BinaryIO, started: float, cancelled: Event | None):
        self.source, self.started, self.cancelled = source, started, cancelled

    def read(self, size: int = -1):
        _deadline(self.started, self.cancelled)
        return self.source.read(size)

    def seek(self, *args):
        _deadline(self.started, self.cancelled)
        return self.source.seek(*args)

    def __getattr__(self, name):
        return getattr(self.source, name)


class _WindowComplete(Exception):
    """Stop parsing after one look-ahead block, including mid-paragraph."""


def docx_window(path: Path, offset: int, *, max_uncompressed_bytes: int,
                cancelled: Event | None = None) -> dict:
    """Stream character events; even one enormous paragraph stays bounded.

    Paragraphs are split at stable 4096-character boundaries, table rows at 16
    columns. Later windows remain reachable. No whole-document tree is built.
    """
    _validate_zip_budget(path, max_uncompressed_bytes=max_uncompressed_bytes)
    started = time.monotonic()
    blocks: list[dict] = []
    position = 0
    clipped = False
    has_more = False
    stack: list[str] = []
    paragraph: dict | None = None
    paragraph_text = ""
    paragraph_images: list[int] = []
    cell_text: str | None = None
    row_cells: list[str] | None = None

    def emit(block: dict) -> None:
        nonlocal position, has_more
        if position >= offset:
            if len(blocks) == BLOCK_WINDOW:
                has_more = True
                raise _WindowComplete
            blocks.append(block)
        position += 1
    with zipfile.ZipFile(path) as archive:
        media = sorted(i.filename for i in archive.infolist()
                       if i.filename.startswith("word/media/") and not i.is_dir())[:50]
        rels_name = "word/_rels/document.xml.rels"
        if rels_name in archive.namelist():
            if archive.getinfo(rels_name).file_size > 1_048_576:
                raise UnsupportedTextFileError("文档图片索引超过预览上限。")
            rels = _docx_rels_map(archive.read(rels_name))
        else:
            rels = {}
        media_index = {name.rsplit("/", 1)[-1]: i + 1 for i, name in enumerate(media)}

        def start(name: str, attrs: dict) -> None:
            nonlocal paragraph, paragraph_text, paragraph_images, cell_text, row_cells
            tag = name.rsplit("}", 1)[-1]
            parent = stack[-1] if stack else ""
            stack.append(tag)
            if tag == "p" and parent in ("body", "sdtContent") and cell_text is None:
                paragraph, paragraph_text, paragraph_images = {"type": "paragraph"}, "", []
            elif tag == "tr" and row_cells is None:
                row_cells = []
            elif tag == "tc" and cell_text is None:
                cell_text = ""
            elif paragraph is not None:
                if tag == "pStyle":
                    style = attrs.get(f"{W_NS}}}val", "").lower()
                    digits = "".join(c for c in style if c.isdigit())
                    if digits and ("heading" in style or "标题" in style):
                        paragraph = {"type": "heading", "level": min(6, int(digits[:2]))}
                elif tag == "numPr":
                    paragraph["type"] = "list"
                elif tag == "blip":
                    rid = attrs.get("http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed", "")
                    image = media_index.get(rels.get(rid, "").rsplit("/", 1)[-1])
                    if image is not None and len(paragraph_images) < 50:
                        paragraph_images.append(image)

        def characters(value: str) -> None:
            nonlocal paragraph_text, cell_text, clipped
            if not stack or stack[-1] != "t":
                return
            if cell_text is not None:
                clipped = clipped or len(cell_text) + len(value) > CELL_CHARS
                cell_text = (cell_text + value[:CELL_CHARS])[:CELL_CHARS]
            elif paragraph is not None:
                paragraph_text += value
                while len(paragraph_text) >= PARAGRAPH_CHARS:
                    emit({**paragraph, "text": paragraph_text[:PARAGRAPH_CHARS]})
                    paragraph_text = paragraph_text[PARAGRAPH_CHARS:]

        def end(name: str) -> None:
            nonlocal paragraph, paragraph_text, paragraph_images, cell_text, row_cells
            tag = name.rsplit("}", 1)[-1]
            if tag == "p" and paragraph is not None:
                if paragraph_text.strip():
                    emit({**paragraph, "text": paragraph_text})
                for image in paragraph_images:
                    emit({"type": "image", "image_index": image, "caption": f"图片 {image}"})
                paragraph, paragraph_text, paragraph_images = None, "", []
            elif tag == "p" and cell_text is not None and cell_text:
                cell_text = (cell_text + " ")[:CELL_CHARS]
            elif tag == "tc" and cell_text is not None and row_cells is not None:
                row_cells.append(cell_text.rstrip())
                cell_text = None
                if len(row_cells) == COLUMN_WINDOW:
                    emit({"type": "table", "rows": [row_cells]})
                    row_cells = []
            elif tag == "tr" and row_cells is not None:
                if row_cells:
                    emit({"type": "table", "rows": [row_cells]})
                row_cells = None
            stack.pop()

        def reject_doctype(*_args) -> None:
            raise UnsupportedTextFileError("文档含不支持的 XML 实体定义。")

        parser = expat.ParserCreate(namespace_separator="}")
        parser.StartElementHandler, parser.EndElementHandler = start, end
        parser.CharacterDataHandler = characters
        parser.StartDoctypeDeclHandler = reject_doctype
        parser.ExternalEntityRefHandler = lambda *_args: 0
        try:
            with archive.open("word/document.xml") as source:
                while True:
                    _deadline(started, cancelled)
                    chunk = source.read(32_768)
                    parser.Parse(chunk, not chunk)
                    if not chunk:
                        break
        except _WindowComplete:
            pass
        except expat.ExpatError as error:
            raise UnsupportedTextFileError("Word 文档结构损坏，无法读取正文。") from error
    return {"blocks": blocks, "offset": offset,
            "next_offset": offset + len(blocks) if has_more else None,
            "clipped_cells": clipped}


def xlsx_window(path: Path, sheet_index: int, offset: int, column_offset: int,
                *, max_uncompressed_bytes: int, cancelled: Event | None = None) -> dict:
    _validate_zip_budget(path, max_uncompressed_bytes=max_uncompressed_bytes)
    # openpyxl loads shared strings/styles even in read-only mode. Bound those
    # tables independently of the permitted size of streamed worksheet XML.
    with zipfile.ZipFile(path) as archive:
        for name in ("xl/sharedStrings.xml", "xl/styles.xml", "xl/workbook.xml"):
            if name in archive.namelist() and archive.getinfo(name).file_size > 8 * 1024 * 1024:
                raise UnsupportedTextFileError("这个工作簿的索引超过预览上限，请在原件中查看。")
    from openpyxl import load_workbook
    started = time.monotonic()
    with path.open("rb") as source:
        _deadline(started, cancelled)
        workbook = load_workbook(_CheckedReader(source, started, cancelled),
                                 read_only=True, data_only=True, keep_links=False)
        try:
            _deadline(started, cancelled)
            names = workbook.sheetnames
            if not 0 <= sheet_index < len(names):
                raise UnsupportedTextFileError("没有找到这个工作表。")
            sheet = workbook.worksheets[sheet_index]
            total_rows = sheet.max_row
            total_columns = sheet.max_column
            rows = []
            clipped = False
            # Explicit max_col prevents sparse formatting at XFD from creating
            # thousands of blank Python cells per requested row.
            for row in sheet.iter_rows(min_row=offset + 1, max_row=offset + ROW_WINDOW + 1,
                                       min_col=column_offset + 1,
                                       max_col=column_offset + COLUMN_WINDOW, values_only=True):
                _deadline(started, cancelled)
                values = [str(value) if value is not None else "" for value in row]
                clipped = clipped or any(len(value) > CELL_CHARS for value in values)
                rows.append([value[:CELL_CHARS] for value in values])
            more = len(rows) > ROW_WINDOW and (total_rows is None or offset + ROW_WINDOW < total_rows)
            rows = rows[:ROW_WINDOW]
            if total_rows is not None:
                rows = rows[:max(0, total_rows - offset)]
            columns = min(COLUMN_WINDOW, max(0, (total_columns or COLUMN_WINDOW) - column_offset))
            result = {"name": sheet.title,
                      "columns": [_excel_column_name(column_offset + i) for i in range(columns)],
                      "rows": [row[:columns] for row in rows]}
            return {"sheets": [result], "sheet_names": names, "sheet_index": sheet_index,
                    "offset": offset, "column_offset": column_offset,
                    "next_offset": offset + ROW_WINDOW if more else None,
                    "next_column_offset": column_offset + COLUMN_WINDOW
                    if total_columns and column_offset + COLUMN_WINDOW < total_columns else None,
                    "total_rows": total_rows, "clipped_cells": clipped}
        finally:
            workbook.close()


def image_thumbnail(path: Path | BinaryIO, *, max_pixels: int,
                    cancelled: Event | None = None) -> bytes:
    """Return at most 1600² pixels within a separate preview decode budget."""
    started = time.monotonic()
    _deadline(started, cancelled)
    with Image.open(path) as original:
        if original.width * original.height > max_pixels:
            raise UnsupportedTextFileError("这张图片超过预览像素上限。")
        original.draft("RGB", (1600, 1600))
        if original.width * original.height > PREVIEW_DECODE_PIXELS:
            raise UnsupportedTextFileError("这张图片超出安全预览大小，请在原件中查看。")
        original.thumbnail((1600, 1600))
        _deadline(started, cancelled)
        preview = ImageOps.exif_transpose(original).convert("RGB")
        output = BytesIO()
        preview.save(output, format="JPEG", quality=85)
        return output.getvalue()
