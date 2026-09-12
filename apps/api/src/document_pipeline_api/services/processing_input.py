"""Prepare one bounded model input, retaining native source coordinates."""
from io import BytesIO
from pathlib import Path
import warnings
import zipfile

from PIL import Image

from document_pipeline_api.config import Settings
from document_pipeline_api.schemas.input_scope import InputPolicy
from document_pipeline_api.schemas.input_scope import InputScope
from document_pipeline_api.services.file_formats import (
    TEXT_CONTENT_TYPES, _image_frame_to_rgb, inspect_image_frame_count, render_image_frames,
)
from document_pipeline_api.services.input_scope import (
    InputBudgetError, PreparedInput, native_units, select_input, select_prefix, visual_units,
)
from document_pipeline_api.services.pdf_rendering import inspect_pdf_page_count, render_pdf_pages


def policy_for_context(context: int, *, image_budget: int, allow_limited: bool, include_images: bool) -> InputPolicy:
    # Conservative character allocation, not a tokenizer claim. Reserve room for
    # schema/instructions/output. Providers still enforce their real context limit.
    return InputPolicy(text_budget=max(128, min(32000, (context - 4096) // 2)),
                       image_budget=image_budget, allow_limited_input=allow_limited,
                       include_images=include_images)


def policy_from_settings(session) -> InputPolicy:
    from document_pipeline_api.services.system_settings import get_bool_setting, get_setting
    return InputPolicy(version=2,
        allow_limited_input=get_bool_setting(session, "allow_limited_input", False),
        include_images=get_bool_setting(session, "word_include_images", False),
        text_budget=int(get_setting(session, "input_text_limit", "20000")),
        image_budget=int(get_setting(session, "input_page_limit", "10")),
        row_limit=int(get_setting(session, "input_row_limit", "500")),
        docx_image_limit=int(get_setting(session, "input_docx_image_limit", "10")))


def input_scope_issues(scope_json: str | None):
    from document_pipeline_api.schemas.extraction import ValidationIssue
    if not scope_json or InputScope.model_validate_json(scope_json).coverage == "complete":
        return []
    return [ValidationIssue(code="partial_input", field="input_scope", severity="warning",
                            message="本次仅基于所示范围提取，不能视为全文摘要或全部记录。请查看读取范围后确认。")]


def prepare_input(path: Path, content_type: str, settings: Settings, policy: InputPolicy) -> tuple[PreparedInput, int]:
    if content_type in TEXT_CONTENT_TYPES:
        def bounded_units():
            for count, unit in enumerate(native_units(path, content_type, settings, include_images=policy.include_images), 1):
                if count > settings.max_import_rows:
                    raise InputBudgetError(f"文档结构超过安全上限 {settings.max_import_rows} 个单元，无法继续读取。")
                yield unit
        units = bounded_units()
        page_count = 1  # legacy transport field; scope carries the natural structure
    elif content_type == "application/pdf":
        page_count = inspect_pdf_page_count(path, max_pages=10000)
        units = visual_units(page_count)
    else:
        page_count = inspect_image_frame_count(path, expected_content_type=content_type,
                                               max_frames=10000, max_total_pixels=settings.max_image_total_pixels)
        units = visual_units(page_count, frames=True)
    if policy.version >= 2:
        return select_prefix(units, limited=policy.allow_limited_input, text_limit=policy.text_budget,
            image_limit=policy.docx_image_limit if content_type.endswith("wordprocessingml.document") else policy.image_budget,
            row_limit=policy.row_limit), page_count
    return select_input(units, text_budget=policy.text_budget, image_budget=policy.image_budget), page_count


def render_input_images(path: Path, content_type: str, prepared: PreparedInput, output: Path, task_id: str, settings: Settings) -> list[Path]:
    visual = [unit for unit in prepared.units if unit.text is None]
    if not visual:
        return []
    if content_type == "application/pdf":
        return render_pdf_pages(path, output, max_pages=len(visual), page_numbers=[unit.location.start for unit in visual], max_total_pixels=settings.max_image_total_pixels)
    if content_type not in TEXT_CONTENT_TYPES:
        return render_image_frames(path, output, task_id, max_frames=len(visual),
                                   expected_content_type=content_type, max_total_pixels=settings.max_image_total_pixels,
                                   frame_numbers=[unit.location.start for unit in visual])
    output.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    pixels = 0
    with zipfile.ZipFile(path) as archive:
        for unit in visual:
            if not unit.image_member:
                raise InputBudgetError("所选 Word 图片没有可验证的内部引用。")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(archive.read(unit.image_member))) as image:
                    if getattr(image, "n_frames", 1) != 1:
                        raise InputBudgetError("所选 Word 内嵌图片含多帧，当前无法完整读取该图片。")
                    pixels += image.width * image.height
                    if pixels > settings.max_image_total_pixels:
                        raise InputBudgetError("Word 内嵌图片超过像素安全上限。")
                    image.load()
                    target = output / f"{task_id}-docx-image-{unit.location.start}.png"
                    _image_frame_to_rgb(image).save(target, "PNG")
                    paths.append(target)
    return paths
