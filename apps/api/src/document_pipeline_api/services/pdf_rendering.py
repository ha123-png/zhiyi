from pathlib import Path

import pypdfium2 as pdfium


class UnsupportedPdfError(ValueError):
    pass


def inspect_pdf_page_count(pdf_path: Path, *, max_pages: int) -> int:
    document = pdfium.PdfDocument(pdf_path)
    try:
        page_count = len(document)
    finally:
        document.close()
    if page_count == 0:
        raise UnsupportedPdfError("PDF 没有可处理的页面。")
    if page_count > max_pages:
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
) -> list[Path]:
    document = pdfium.PdfDocument(pdf_path)
    try:
        page_count = len(document)
        if page_count == 0:
            raise UnsupportedPdfError("PDF 没有可处理的页面。")
        if page_count > max_pages:
            raise UnsupportedPdfError(
                f"PDF 共 {page_count} 页，超过当前 {max_pages} 页处理上限。"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[Path] = []
        for index in range(page_count):
            page = document[index]
            try:
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
