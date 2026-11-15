"""Shared image conversion and text readers for stored originals."""

from __future__ import annotations

from pathlib import Path
import zipfile
import warnings
from xml.etree import ElementTree

from PIL import Image, ImageOps

# 需要归一化的图片类型（jpeg/png 是模型原生可读，无需转换）
CONVERTIBLE_IMAGE_TYPES = {
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/gif": ".gif",
}
RASTER_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    **CONVERTIBLE_IMAGE_TYPES,
}
_PIL_FORMATS_BY_CONTENT_TYPE = {
    "image/jpeg": {"JPEG"},
    "image/png": {"PNG"},
    "image/webp": {"WEBP"},
    "image/bmp": {"BMP"},
    "image/tiff": {"TIFF"},
    "image/gif": {"GIF"},
}

# 可在本机读取文字或单元格的文件类型
TEXT_CONTENT_TYPES = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

class UnsupportedTextFileError(ValueError):
    pass


class UnsupportedImageError(ValueError):
    pass


def inspect_image_frame_count(
    path: Path,
    *,
    expected_content_type: str | None = None,
    max_frames: int | None = None,
    max_total_pixels: int | None = None,
) -> int:
    """验证真实格式并逐帧解码；返回可安全处理的总帧数。"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                actual_format = (source.format or "").upper()
                expected_formats = _PIL_FORMATS_BY_CONTENT_TYPE.get(
                    expected_content_type or ""
                )
                if expected_formats is not None and actual_format not in expected_formats:
                    raise UnsupportedImageError(
                        "图片实际格式与上传时声明的类型不一致，请勿修改扩展名后重试。"
                    )
                frame_count = int(getattr(source, "n_frames", 1))
                if frame_count < 1:
                    raise UnsupportedImageError("图片没有可读取的页面。")
                if max_frames is not None and frame_count > max_frames:
                    raise UnsupportedImageError(
                        f"图片共 {frame_count} 页，超过当前 {max_frames} 页处理上限。"
                    )
                total_pixels = 0
                # verify() 只保证当前帧；逐帧 load 才能发现后续帧截断/损坏。
                for index in range(frame_count):
                    source.seek(index)
                    width, height = source.size
                    if width < 1 or height < 1:
                        raise UnsupportedImageError("图片包含尺寸无效的页面。")
                    total_pixels += width * height
                    if max_total_pixels is not None and total_pixels > max_total_pixels:
                        raise UnsupportedImageError(
                            f"图片总像素超过当前 {max_total_pixels} 像素处理上限。"
                        )
                    source.load()
                return frame_count
    except UnsupportedImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise UnsupportedImageError("图片像素尺寸过大，无法安全处理。") from error
    except Exception as error:
        raise UnsupportedImageError("图片文件损坏或无法读取，请更换后重试。") from error


def render_image_frames(
    src: Path,
    out_dir: Path,
    task_id: str,
    *,
    max_frames: int,
    expected_content_type: str | None = None,
    max_total_pixels: int | None = None,
    frame_numbers: list[int] | None = None,
) -> list[Path]:
    """按原始帧号渲染 PNG；未指定范围时保持处理全部帧的旧行为。"""
    frame_count = inspect_image_frame_count(
        src,
        expected_content_type=expected_content_type,
        max_frames=max_frames if frame_numbers is None else None,
        max_total_pixels=max_total_pixels,
    )
    if frame_numbers is not None and (
        not frame_numbers or frame_numbers != sorted(set(frame_numbers))
        or frame_numbers[0] < 1 or frame_numbers[-1] > frame_count
        or len(frame_numbers) > max_frames
    ):
        raise UnsupportedImageError("图片处理范围必须是预算内有效、递增且不重复的原始帧号。")
    selected = frame_numbers if frame_numbers is not None else list(range(1, frame_count + 1))
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    try:
        with Image.open(src) as source:
            for frame_number in selected:
                index = frame_number - 1
                source.seek(index)
                target = out_dir / f"{task_id}-image-{index + 1}.png"
                _image_frame_to_rgb(source).save(target, "PNG")
                rendered.append(target)
    except Exception as error:
        for target in rendered:
            target.unlink(missing_ok=True)
        raise UnsupportedImageError("图片页面转换失败，请更换文件后重试。") from error
    return rendered


def _image_frame_to_rgb(source: Image.Image) -> Image.Image:
    source = ImageOps.exif_transpose(source)
    if source.mode.startswith("I;16"):
        # Pillow's direct RGB conversion clips samples above 255. Use the full
        # unsigned 16-bit range, with rounding, rather than image-dependent gain.
        source = source.convert("F").point(lambda value: value * (255 / 65535) + 0.5).convert("L")
    elif source.mode in ("I", "F"):
        raise UnsupportedImageError("暂不支持此图片的整数或浮点灰度格式，请先转换为 8 位或 16 位灰度图片。")
    if source.mode in ("RGBA", "LA", "P"):
        rgba = source.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return source.convert("RGB")


def _validate_zip_budget(path: Path, *, max_uncompressed_bytes: int | None) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            total = 0
            for member in archive.infolist():
                if member.flag_bits & 0x1:
                    raise UnsupportedTextFileError("Office 文档包含加密内容，当前无法处理。")
                total += member.file_size
                if max_uncompressed_bytes is not None and total > max_uncompressed_bytes:
                    raise UnsupportedTextFileError(
                        f"Office 文档解压后超过当前 {max_uncompressed_bytes} 字节处理上限。"
                    )
    except UnsupportedTextFileError:
        raise
    except (zipfile.BadZipFile, OSError) as error:
        raise UnsupportedTextFileError("Office 文档损坏或不是有效的压缩文档。") from error


def _extract_docx_text(path: Path, *, max_uncompressed_bytes: int | None) -> str:
    _validate_zip_budget(path, max_uncompressed_bytes=max_uncompressed_bytes)
    try:
        with zipfile.ZipFile(path) as archive:
            xml_bytes = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as error:
        raise UnsupportedTextFileError("Word 文档损坏或不是有效的 .docx 文件。") from error
    root = ElementTree.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(
                node.text or ""
                for node in element.iter()
                if node.tag.rsplit("}", 1)[-1] == "t"
            )
            stripped = text.strip()
            if stripped:
                paragraphs.append(stripped)
    return "\n".join(paragraphs)


def _docx_rels_map(rels_bytes: bytes) -> dict[str, str]:
    """解析 word/_rels/document.xml.rels：rId -> 媒体目标文件名。

    ``Id``/``Target`` 是无命名空间属性，直接按字面读取。
    """
    try:
        root = ElementTree.fromstring(rels_bytes)
    except ElementTree.ParseError:
        return {}
    mapping: dict[str, str] = {}
    for rel in root:
        rid = rel.get("Id")
        target = rel.get("Target") or ""
        if rid:
            mapping[rid] = target
    return mapping


def _extract_xlsx_text(
    path: Path,
    *,
    max_uncompressed_bytes: int | None,
    max_rows: int | None,
    max_columns: int | None,
) -> str:
    _validate_zip_budget(path, max_uncompressed_bytes=max_uncompressed_bytes)
    try:
        from openpyxl import load_workbook
    except ImportError as error:  # pragma: no cover
        raise UnsupportedTextFileError("当前环境缺少 Excel 读取组件。") from error
    source = None
    try:
        # Upload validation runs before the staging file is renamed to ``.xlsx``.
        # Passing that ``.<uuid>.uploading`` path directly makes openpyxl reject a
        # valid workbook solely because of its temporary suffix.  A binary stream
        # keeps the same parser and ZIP checks without coupling validity to a name.
        source = path.open("rb")
        workbook = load_workbook(source, read_only=True, data_only=True)
    except Exception as error:
        if source is not None:
            source.close()
        raise UnsupportedTextFileError("Excel 文件损坏或不是有效的 .xlsx 文件。") from error
    lines: list[str] = []
    row_count = 0
    try:
        for sheet in workbook.worksheets:
            from document_pipeline_api.services.xlsx_numbers import XlsxNumericError, validate_xlsx_numbers
            try:
                validate_xlsx_numbers(sheet, max_row=max_rows + 1 if max_rows is not None else None,
                                      max_column=max_columns)
            except XlsxNumericError as error:
                raise UnsupportedTextFileError(str(error)) from error
            if workbook.worksheets.index(sheet) > 0:
                lines.append("")
            lines.append(f"【工作表】{sheet.title}")
            for row in sheet.iter_rows():
                row_count += 1
                if max_rows is not None and row_count > max_rows:
                    raise UnsupportedTextFileError(
                        f"Excel 文档超过当前 {max_rows} 行处理上限。"
                    )
                if max_columns is not None and len(row) > max_columns:
                    raise UnsupportedTextFileError(
                        f"Excel 文档超过当前 {max_columns} 列处理上限。"
                    )
                cells = [
                    str(cell.value) if cell.value is not None else ""
                    for cell in row
                ]
                if any(cells):
                    lines.append("\t".join(cells))
    finally:
        workbook.close()
        source.close()
    return "\n".join(lines)


def _excel_column_name(index: int) -> str:
    """0 基索引转 Excel 列名（0->A, 25->Z, 26->AA）。"""
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def extract_text(
    path: Path,
    content_type: str,
    *,
    max_uncompressed_bytes: int | None = None,
    max_rows: int | None = None,
    max_columns: int | None = None,
) -> str:
    """按类型提取纯文本；文本将按行分页渲染为图片。"""
    if content_type in {"text/plain", "text/markdown"}:
        raw = path.read_bytes()
        for encoding in ("utf-8", "gbk"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise UnsupportedTextFileError("文本文件编码无法识别，仅支持 UTF-8 或 GBK。")
    if content_type.endswith(".wordprocessingml.document"):
        return _extract_docx_text(
            path, max_uncompressed_bytes=max_uncompressed_bytes
        )
    if content_type.endswith(".spreadsheetml.sheet"):
        return _extract_xlsx_text(
            path,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_rows=max_rows,
            max_columns=max_columns,
        )
    raise UnsupportedTextFileError("不支持的文本文件类型。")
