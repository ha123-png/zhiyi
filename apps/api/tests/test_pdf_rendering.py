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


def test_pdf_pixel_budget_rejects_before_rendering_oversized_page(tmp_path: Path) -> None:
    source = tmp_path / "large-page.pdf"
    create_pdf(source, pages=1)
    output = tmp_path / "rendered"
    with pytest.raises(UnsupportedPdfError, match="像素安全上限"):
        render_pdf_pages(source, output, max_pages=1, max_total_pixels=100)
    assert not list(output.glob("*.png"))
    assert source.is_file()


def test_selected_pdf_images_keep_original_page_numbers(tmp_path: Path):
    pdf_path = tmp_path / "eighty.pdf"
    create_pdf(pdf_path, pages=80)
    pages = render_pdf_pages(pdf_path, tmp_path / "rendered", max_pages=4,
                             page_numbers=[1, 2, 79, 80], scale=0.25)
    assert [page.name for page in pages] == ["page-1.png", "page-2.png", "page-79.png", "page-80.png"]
    assert len(list((tmp_path / "rendered").glob("*.png"))) == 4


@pytest.mark.parametrize("numbers", [[], [0], [1, 1], [2, 1], [4]])
def test_invalid_pdf_scope_fails_before_rendering(tmp_path: Path, numbers):
    pdf_path = tmp_path / "three.pdf"
    create_pdf(pdf_path, pages=3)
    with pytest.raises(UnsupportedPdfError, match="处理范围"):
        render_pdf_pages(pdf_path, tmp_path / "rendered", max_pages=3, page_numbers=numbers)
    assert not (tmp_path / "rendered").exists()
