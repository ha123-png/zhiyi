from pathlib import Path
from math import ceil

import pypdfium2 as pdfium


class UnsupportedPdfError(ValueError):
    pass


def inspect_pdf_page_count(pdf_path: Path, *, max_pages: int | None) -> int:
    document = pdfium.PdfDocument(pdf_path)
    try:
        page_count = len(document)
    finally:
        document.close()
    if page_count == 0:
        raise UnsupportedPdfError("PDF 没有可处理的页面。")
    if max_pages is not None and page_count > max_pages:
        raise UnsupportedPdfError(
            f"PDF 共 {page_count} 页，超过当前 {max_pages} 页处理上限。"
        )
    return page_count


def render_pdf_pages(
    pdf_path: Path,
    output_dir: Path,
    *,
    max_pages: int,
    scale: float = 3.0,
    page_numbers: list[int] | None = None,
    max_total_pixels: int | None = None,
) -> list[Path]:
    document = pdfium.PdfDocument(pdf_path)
    try:
        page_count = len(document)
        if page_count == 0:
            raise UnsupportedPdfError("PDF 没有可处理的页面。")
        if page_numbers is not None and (
            not page_numbers or page_numbers != sorted(set(page_numbers))
            or page_numbers[0] < 1 or page_numbers[-1] > page_count
        ):
            raise UnsupportedPdfError("PDF 处理范围必须是有效、递增且不重复的原始页码。")
        selected = page_numbers if page_numbers is not None else list(range(1, page_count + 1))
        if len(selected) > max_pages:
            raise UnsupportedPdfError(
                f"PDF 共 {page_count} 页，超过当前 {max_pages} 页处理上限。"
                if page_numbers is None else
                f"本次选定 {len(selected)} 页，超过单次 {max_pages} 页输入预算。"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[Path] = []
        total_pixels = 0
        for page_number in selected:
            index = page_number - 1
            page = document[index]
            try:
                total_pixels += ceil(page.get_width() * scale) * ceil(page.get_height() * scale)
                if max_total_pixels is not None and total_pixels > max_total_pixels:
                    raise UnsupportedPdfError("所选 PDF 页面渲染后超过像素安全上限。")
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    output_path = output_dir / f"page-{index + 1}.png"
                    image.save(output_path, format="PNG")
                    rendered.append(output_path)
                finally:
                    bitmap.close()
            finally:
                page.close()
    finally:
        document.close()
    return rendered


def render_single_page_pdf(pdf_path: Path, output_path: Path, scale: float = 3.0) -> Path:
    pages = render_pdf_pages(
        pdf_path,
        output_path.parent,
        max_pages=1,
        scale=scale,
    )
    pages[0].replace(output_path)
    return output_path
