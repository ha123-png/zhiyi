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
    if not task_id or any(character in task_id for character in "/\\:\x00") or task_id in {".", ".."}:
        raise ValueError("任务标识不能包含路径字符。")
    try:
        suffix = CONTENT_SUFFIXES[content_type]
    except KeyError as error:
        raise ValueError("任务文件类型没有安全的存储后缀。") from error
    return f"{task_id}{suffix}"


def resolve_task_storage_path(storage_dir: Path, task: TaskRecord) -> Path:
    expected_name = task_storage_name(task.id, task.content_type)
    previous = (task.internal_storage or {}).get("previous_path")
    return resolve_original_reference(storage_dir, task.storage_path, expected_name, previous)


def resolve_original_reference(storage_dir: Path, stored_value: str, expected_name: str, previous: str | None = None) -> Path:
    if previous and previous != stored_value:
        old = resolve_original_reference(storage_dir, previous, expected_name)
        if old.is_file():
            return old
    canonical = storage_dir.resolve() / expected_name
    stored = Path(stored_value)
    if not stored.is_absolute():
        if stored.name != expected_name or ".." in stored.parts:
            raise ValueError("任务的原文件引用格式无效。")
        if len(stored.parts) == 1:
            target = canonical
        elif len(stored.parts) == 3 and stored.parts[0] == "templates":
            target = storage_dir.resolve() / stored
        else:
            raise ValueError("任务的原文件引用格式无效。")
        ensure_managed_path(storage_dir, target)
        # A durable relocation intent may precede the atomic move. The old
        # flat original stays readable if a crash occurs in that window.
        if not target.exists() and len(stored.parts) > 1:
            ensure_managed_path(storage_dir, canonical)
            if canonical.is_file():
                return canonical
            if previous and previous != stored_value:
                return resolve_original_reference(storage_dir, previous, expected_name)
        return target
    if stored.name != expected_name:
        raise ValueError("旧任务的原文件路径与任务标识不一致。")
    ensure_managed_path(storage_dir, canonical)
    if canonical.is_file():
        return canonical
    return stored


def ensure_managed_path(storage_dir: Path, path: Path) -> Path:
    root = storage_dir.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("文件路径不属于知意管理的内部原件目录。")
    # Reject reparse points even if they currently resolve back inside root.
    current = path.absolute()
    while current != root and current.is_relative_to(root):
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("内部原件路径不能经过符号链接或目录联接。")
        current = current.parent
    return resolved
