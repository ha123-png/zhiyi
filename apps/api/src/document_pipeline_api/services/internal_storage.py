"""Classify the canonical internal original without making another data copy."""
import hashlib
import json
import os
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.models import TaskRecord, TemplateLocalBindingRecord
from document_pipeline_api.services.file_copies import suggest_safe_stem, validate_copy_name
from document_pipeline_api.services.file_operation_lock import file_operation_lock
from document_pipeline_api.services.templates import get_template_version
from document_pipeline_api.storage_paths import ensure_managed_path, resolve_task_storage_path, task_storage_name


def _move_exclusive(source: Path, target: Path) -> None:
    if os.name == "nt":
        os.rename(source, target)
    else:
        os.link(source, target)
        source.unlink()


def classify_task_original(session: Session, settings: Settings, task_id: str) -> None:
    try:
        with file_operation_lock(settings.storage_dir):
            _classify(session, settings, task_id)
    except HTTPException as error:
        if error.status_code != 409:
            raise


def _classify(session: Session, settings: Settings, task_id: str) -> None:
    session.expire_all()
    task = session.get(TaskRecord, task_id)
    if task is None or task.template_id is None or task.template_version is None:
        return
    # A worker may still hold raw-image paths during model execution.
    if task.status in {"processing", "validating"}:
        return
    previous = task.storage_path
    try:
        source = ensure_managed_path(settings.storage_dir, resolve_task_storage_path(settings.storage_dir, task))
        with source.open("rb") as original:
            if hashlib.file_digest(original, "sha256").hexdigest() != task.sha256 or source.stat().st_size != task.size_bytes:
                raise ValueError("内部原件与保存记录不一致，未移动文件。")
        template = get_template_version(session, task.template_id, task.template_version)
        session.execute(insert(TemplateLocalBindingRecord).values(
            template_id=template.id, revision=0, enabled=False,
        ).on_conflict_do_nothing(index_elements=[TemplateLocalBindingRecord.template_id]))
        session.expire_all()
        binding = session.get(TemplateLocalBindingRecord, template.id)
        root = settings.storage_dir.resolve()
        folders = ensure_managed_path(root, root / "templates")
        folders.mkdir(exist_ok=True)
        if binding.internal_folder is None:
            base = suggest_safe_stem(template.name)
            used = {name.casefold() for name in session.scalars(select(TemplateLocalBindingRecord.internal_folder).where(TemplateLocalBindingRecord.internal_folder.is_not(None)))}
            folder = base
            index = 2
            while folder.casefold() in used or (folders / folder).exists():
                folder = f"{base} ({index})"
                index += 1
            binding.internal_folder = folder
        validate_copy_name(binding.internal_folder)
        destination = ensure_managed_path(root, folders / binding.internal_folder)
        destination.mkdir(exist_ok=True)
        session.commit()
        target = ensure_managed_path(root, destination / task_storage_name(task.id, task.content_type))
        relative = target.relative_to(root).as_posix()
        if source != target:
            if target.exists() and not source.samefile(target):
                raise ValueError("内部目标已存在另一份文件，未覆盖或移动原件。")
            actual_previous = source.relative_to(root).as_posix()
            task.storage_path = relative
            task.internal_storage_json = json.dumps({"status": "moving", "folder": binding.internal_folder, "previous_path": actual_previous}, ensure_ascii=False)
            session.commit()
            if target.exists() and source.samefile(target):
                source.unlink()
            else:
                _move_exclusive(source, target)
        task.storage_path = relative
        task.internal_storage_json = json.dumps({"status": "classified", "folder": binding.internal_folder, "relative_path": relative}, ensure_ascii=False)
        session.commit()
    except (OSError, ValueError) as error:
        session.rollback()
        task = session.get(TaskRecord, task_id)
        if task is None:
            return
        # A persisted move intent retains the old reference until the move is
        # proven complete. Never point preview at an unrelated conflicting file.
        info = task.internal_storage or {}
        old = info.get("previous_path") or previous
        task.internal_storage_json = json.dumps({"status": "failed", "previous_path": old, "error": str(error) if isinstance(error, ValueError) else "内部归类暂未完成，未删除原件；请检查数据目录或磁盘。稍后将重试。"}, ensure_ascii=False)
        session.commit()


def recover_internal_storage(session: Session, settings: Settings) -> None:
    ids = list(session.scalars(select(TaskRecord.id).where(
        TaskRecord.template_id.is_not(None),
        TaskRecord.status.notin_(["processing", "validating"]),
        (TaskRecord.internal_storage_json.is_(None)) | (func.json_extract(TaskRecord.internal_storage_json, "$.status") != "classified"),
    ).order_by(TaskRecord.updated_at.asc()).limit(100)))
    for task_id in ids:
        classify_task_original(session, settings, task_id)
