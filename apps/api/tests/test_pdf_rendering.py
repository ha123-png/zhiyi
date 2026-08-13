from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from document_pipeline_api.services.pdf_rendering import (
    UnsupportedPdfError,
    render_pdf_pages,
    render_single_page_pdf,
)


def create_pdf(path: Path, pages: int) -> None:
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            page = document.new_page(595, 842)
            page.close()
        document.save(path)
    finally:
        document.close()


def test_renders_single_pdf_page_to_model_image(tmp_path: Path) -> None:
    pdf_path = tmp_path / "single.pdf"
    image_path = tmp_path / "rendered" / "page.png"
    create_pdf(pdf_path, pages=1)

    render_single_page_pdf(pdf_path, image_path)

    with Image.open(image_path) as image:
        assert image.width == 1785
        assert image.height == 2526


def test_renders_multi_page_pdf_in_page_order(tmp_path: Path) -> None:
    pdf_path = tmp_path / "multi.pdf"
    create_pdf(pdf_path, pages=2)

    pages = render_pdf_pages(pdf_path, tmp_path / "rendered", max_pages=3)

    assert [page.name for page in pages] == ["page-1.png", "page-2.png"]
    assert all(page.is_file() for page in pages)


def test_rejects_pdf_over_explicit_page_limit(tmp_path: Path) -> None:
    pdf_path = tmp_path / "large.pdf"
    create_pdf(pdf_path, pages=3)

    with pytest.raises(UnsupportedPdfError, match="3 页.*2 页处理上限"):
        render_pdf_pages(pdf_path, tmp_path / "rendered", max_pages=2)
