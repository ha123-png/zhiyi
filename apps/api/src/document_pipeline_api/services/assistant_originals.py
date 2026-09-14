"""Read one authorized source page without modifying the original or persisting image payloads."""

import base64
from io import BytesIO
from tempfile import TemporaryDirectory
from pathlib import Path

from fastapi import HTTPException
from PIL import Image
import pypdfium2 as pdfium

from document_pipeline_api.models import TaskRecord
from document_pipeline_api.storage_paths import resolve_task_storage_path
from document_pipeline_api.services.file_formats import extract_text, TEXT_CONTENT_TYPES
from document_pipeline_api.services.pdf_rendering import render_pdf_pages


def image_payload(image):
    image = image.convert("RGB")
    image.thumbnail((1600, 1600))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def read_original(session, settings, body):
    task = session.get(TaskRecord, body.task_id)
    if not task:
        raise HTTPException(404, "原件记录已不存在。")
    path = resolve_task_storage_path(settings.storage_dir, task)
    if not path.resolve().is_relative_to(settings.storage_dir.resolve()):
        raise HTTPException(403, "原件不在知意管理目录内，请通过原生页面核对存储位置。")
    if not path.is_file():
        raise HTTPException(404, "原件已不在知意存储目录中。")
    from document_pipeline_api.services.system_settings import upload_limit_bytes
    if path.stat().st_size > upload_limit_bytes(session, settings.max_upload_bytes):
        raise HTTPException(422, "原件超过当前单文件读取限制。")
    result = {
        "original": {"kind": "original", "id": task.id, "label": task.display_filename},
        "page": body.page,
        "vision": body.vision,
    }
    text = ""
    if task.content_type == "application/pdf":
        with pdfium.PdfDocument(path) as document:
            result["page_count"] = len(document)
            if body.page > len(document):
                raise HTTPException(422, "页码超过原件页数。")
            page = document[body.page - 1]
            try:
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_range()
                finally:
                    text_page.close()
            finally:
                page.close()
        if body.vision:
            with TemporaryDirectory(prefix="zhiyi-page-") as directory:
                rendered = render_pdf_pages(
                    path,
                    Path(directory),
                    max_pages=1,
                    page_numbers=[body.page],
                    scale=1.5,
                    max_total_pixels=12_000_000,
                )
                with Image.open(rendered[0]) as image:
                    result["_image"] = image_payload(image)
    elif task.content_type.startswith("image/"):
        with Image.open(path) as image:
            result["page_count"] = getattr(image, "n_frames", 1)
            if body.page > result["page_count"]:
                raise HTTPException(422, "页码超过图片帧数。")
            image.seek(body.page - 1)
            if image.width * image.height > settings.max_image_total_pixels:
                raise HTTPException(422, "该页超过图像像素限制。")
            if body.vision:
                result["_image"] = image_payload(image)
        result["note"] = "图片没有文本层，需支持视觉的模型并选择 vision=true 才能读取图像内容。"
    elif task.content_type in TEXT_CONTENT_TYPES:
        if body.page != 1:
            raise HTTPException(422, "文字和 Office 使用 offset 读取后续文字，不使用版面页码。")
        if body.vision:
            raise HTTPException(
                422, "此格式按文字读取；没有伪造 Office 原始版面图像。请使用 vision=false。"
            )
        text = extract_text(
            path,
            task.content_type,
            max_uncompressed_bytes=settings.max_import_uncompressed_bytes,
            max_rows=settings.max_import_rows,
            max_columns=settings.max_import_columns,
        )
        result["note"] = "原件文字；不代表 Office 的原始排版或图片内容。"
    else:
        raise HTTPException(422, "此原件格式暂不支持对话内读取，请打开原件查看。")
    result.update(
        text=text[body.offset : body.offset + 4000],
        offset=body.offset,
        total_characters=len(text),
        next_offset=body.offset + 4000 if len(text) > body.offset + 4000 else None,
    )
    return result
