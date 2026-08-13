from pathlib import Path

from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.services.file_formats import (
    CONVERTIBLE_IMAGE_TYPES,
    TEXT_CONTENT_TYPES,
)

CONTENT_SUFFIXES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    **CONVERTIBLE_IMAGE_TYPES,
    **TEXT_CONTENT_TYPES,
}


def task_storage_name(task_id: str, content_type: str) -> str:
    try:
        suffix = CONTENT_SUFFIXES[content_type]
    except KeyError as error:
        raise ValueError("任务文件类型没有安全的存储后缀。") from error
    return f"{task_id}{suffix}"


def resolve_task_storage_path(storage_dir: Path, task: TaskRecord) -> Path:
    expected_name = task_storage_name(task.id, task.content_type)
    canonical = storage_dir.resolve() / expected_name
    stored = Path(task.storage_path)
    if not stored.is_absolute():
        if stored.name != expected_name or len(stored.parts) != 1:
            raise ValueError("任务的原文件引用格式无效。")
        return canonical
    if stored.name != expected_name:
        raise ValueError("旧任务的原文件路径与任务标识不一致。")
    if canonical.is_file():
        return canonical
    return stored
