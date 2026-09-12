from pathlib import Path
import zipfile

import pytest
from openpyxl import Workbook
from PIL import Image

from document_pipeline_api.services.file_formats import (
    MAX_LINES_PER_PAGE,
    UnsupportedImageError,
    UnsupportedTextFileError,
    extract_docx_blocks,
    extract_text,
    inspect_image_frame_count,
    normalize_image,
    render_image_frames,
    render_text_pages,
    split_text_pages,
)


def test_word_preview_accepts_plain_document_without_relationships(tmp_path: Path):
    path = tmp_path / "plain.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>正文仍可阅读</w:t></w:r></w:p></w:body></w:document>''')
    assert extract_docx_blocks(path, max_uncompressed_bytes=10000) == [
        {"type": "paragraph", "text": "正文仍可阅读"},
    ]


def test_word_preview_keeps_image_in_heading(tmp_path: Path):
    path = tmp_path / "heading.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>图示题目</w:t><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p></w:body></w:document>''')
        archive.writestr("word/_rels/document.xml.rels", '<Relationships><Relationship Id="rId1" Target="media/image1.png"/></Relationships>')
        archive.writestr("word/media/image1.png", b"image-placeholder")
    assert extract_docx_blocks(path, max_uncompressed_bytes=10000) == [
        {"type": "heading", "level": 1, "text": "图示题目"},
        {"type": "image", "image_index": 1, "caption": "图片 1"},
    ]


def _bmp_bytes() -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (255, 0, 0)).save(buffer, "BMP")
    return buffer.getvalue()


def test_normalize_image_converts_bmp_to_png(tmp_path: Path) -> None:
    source = tmp_path / "sample.bmp"
    target = tmp_path / "sample.png"
    source.write_bytes(_bmp_bytes())

    normalize_image(source, target)

    with Image.open(target) as image:
        assert image.format == "PNG"
        assert image.size == (16, 16)


def test_render_image_frames_preserves_every_tiff_page(tmp_path: Path) -> None:
    source = tmp_path / "two-pages.tiff"
    red = Image.new("RGB", (12, 12), (255, 0, 0))
    blue = Image.new("RGB", (12, 12), (0, 0, 255))
    red.save(source, save_all=True, append_images=[blue], format="TIFF")

    assert inspect_image_frame_count(source, max_frames=2) == 2
    rendered = render_image_frames(source, tmp_path / "new" / "rendered", "task", max_frames=2)

    assert [path.name for path in rendered] == ["task-image-1.png", "task-image-2.png"]
    with Image.open(rendered[0]) as first, Image.open(rendered[1]) as second:
        assert first.getpixel((0, 0)) == (255, 0, 0)
        assert second.getpixel((0, 0)) == (0, 0, 255)


def test_image_frame_limit_is_enforced(tmp_path: Path) -> None:
    source = tmp_path / "three-pages.gif"
    frames = [Image.new("RGB", (8, 8), color) for color in ("red", "green", "blue")]
    frames[0].save(source, save_all=True, append_images=frames[1:], format="GIF")

    with pytest.raises(UnsupportedImageError, match="3 页"):
        inspect_image_frame_count(source, max_frames=2)


def test_selected_frames_keep_positions_and_pixels(tmp_path: Path):
    source = tmp_path / "three.tiff"
    frames = [Image.new("RGB", (8, 8), color) for color in ("red", "green", "blue")]
    frames[0].save(source, save_all=True, append_images=frames[1:], format="TIFF")
    paths = render_image_frames(source, tmp_path / "rendered", "task", max_frames=2, frame_numbers=[1, 3])
    assert [p.name for p in paths] == ["task-image-1.png", "task-image-3.png"]
    with Image.open(paths[-1]) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)
    with pytest.raises(UnsupportedImageError, match="总像素"):
        render_image_frames(source, tmp_path / "unsafe", "task", max_frames=2, frame_numbers=[1, 3], max_total_pixels=100)


def test_extract_plain_text_utf8_and_gbk(tmp_path: Path) -> None:
    utf8_path = tmp_path / "a.txt"
    utf8_path.write_text("仓库\n数量 5\n", encoding="utf-8")
    assert "仓库" in extract_text(utf8_path, "text/plain")

    gbk_path = tmp_path / "b.txt"
    gbk_path.write_bytes("送货单".encode("gbk"))
    assert extract_text(gbk_path, "text/plain") == "送货单"


def test_extract_docx_text_from_minimal_zip(tmp_path: Path) -> None:
    docx_path = tmp_path / "note.docx"
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>抬头金额</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>100</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(docx_path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    text = extract_text(docx_path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    assert "抬头金额" in text
    assert "100" in text


def test_extract_xlsx_accepts_extensionless_upload_staging_path(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "送货单"
    sheet.append(["单号", "金额"])
    sheet.append(["SH-017", 1164])
    xlsx_path = tmp_path / "source.xlsx"
    workbook.save(xlsx_path)
    workbook.close()
    staging_path = tmp_path / ".task-id.uploading"
    staging_path.write_bytes(xlsx_path.read_bytes())

    text = extract_text(
        staging_path,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert "【工作表】送货单" in text
    assert "SH-017\t1164" in text


def test_office_uncompressed_budget_is_enforced_before_parsing(tmp_path: Path) -> None:
    docx_path = tmp_path / "large.docx"
    with zipfile.ZipFile(docx_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * 1024)

    with pytest.raises(UnsupportedTextFileError, match="解压后"):
        extract_text(
            docx_path,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            max_uncompressed_bytes=100,
        )


def test_split_text_pages_enforces_max_pages() -> None:
    text = "\n".join(f"行 {index}" for index in range(MAX_LINES_PER_PAGE * 3))
    pages = split_text_pages(text, max_pages=3)
    assert len(pages) == 3

    with pytest.raises(UnsupportedTextFileError):
        split_text_pages(text, max_pages=2)


def test_empty_text_is_rejected() -> None:
    with pytest.raises(UnsupportedTextFileError):
        split_text_pages("  \n \n", max_pages=5)


@pytest.mark.skipif(
    not (Path("C:/Windows/Fonts/msyh.ttc").exists()
         or Path("C:/Windows/Fonts/simsun.ttc").exists()
         or Path("C:/Windows/Fonts/simhei.ttf").exists()),
    reason="需要 Windows 系统中文字体",
)
def test_render_text_pages_produces_pngs(tmp_path: Path) -> None:
    rendered = render_text_pages(["第一行\n第二行"], tmp_path, "task-1")

    assert len(rendered) == 1
    with Image.open(rendered[0]) as image:
        assert image.format == "PNG"
