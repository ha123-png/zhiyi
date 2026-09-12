"""Export after confirmation; failures never rewrite extraction/task success."""
import hashlib
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.schemas.file_export import ExportAction, TaskExportState
from document_pipeline_api.services.file_copies import CopyExportError, publish_original_copy, validate_copy_name
from document_pipeline_api.services.file_operation_lock import file_operation_lock
from document_pipeline_api.storage_paths import resolve_task_storage_path


def _save(session: Session, task: TaskRecord, state: TaskExportState) -> None:
    task.export_state_json = state.model_dump_json()
    session.commit()


def _recover_published(task: TaskRecord, state: TaskExportState) -> bool:
    if not state.attempted_path:
        return False
    path = Path(state.attempted_path)
    try:
        stat = path.lstat()
        if path.is_symlink() or not path.is_file() or not state.published_inode:
            return False
        if (stat.st_dev, stat.st_ino, stat.st_size) != (state.published_device, state.published_inode, task.size_bytes):
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest() == task.sha256
    except OSError:
        return False


def process_task_export(session: Session, settings: Settings, task_id: str) -> None:
    try:
        with file_operation_lock(settings.storage_dir):
            _process_task_export(session, settings, task_id)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        # Another publication/deletion owns the OS lock. Periodic recovery can
        # safely pick up the unchanged intent; never force simultaneous writes.


def _process_task_export(session: Session, settings: Settings, task_id: str) -> None:
    session.expire_all()
    task = session.get(TaskRecord, task_id)
    if task is None or task.status != "completed" or task.export_state_json is None:
        return
    if task.file_name is not None and task.file_name.status == "pending":
        return
    state = task.file_export
    if state.status not in {"awaiting_confirmation", "pending", "exporting"}:
        return
    try:
        if state.attempted_path:
            if _recover_published(task, state):
                state.status = "completed"
                state.actual_path = state.attempted_path
                state.error_code = state.error_message = None
                _save(session, task, state)
                return
            raise CopyExportError("publication_uncertain", "无法确认上次副本是否已写出或被移动；请先检查原目标，再选择新路径或跳过，知意不会重复生成或追踪外部文件。")
        if not state.parent_path or not state.folder_name or not state.destination:
            raise CopyExportError("destination_unavailable", "没有有效目标，请重新选择导出位置。")
        parent = Path(state.parent_path)
        destination = Path(state.destination)
        validate_copy_name(state.folder_name)
        if not parent.is_absolute() or not parent.is_dir() or destination != parent / state.folder_name:
            raise CopyExportError("destination_unavailable", "目标文件夹不存在或已变化，请选择新路径。")
        managed = settings.storage_dir.resolve()
        resolved = destination.resolve()
        if resolved.is_relative_to(managed) or managed.is_relative_to(resolved):
            raise CopyExportError("invalid_destination", "外部副本目标与知意内部原件目录重叠，请选择其他位置。")
        # Only the template child may be created. Never recreate the selected
        # parent after a removable drive or network share disappears.
        destination.mkdir(exist_ok=True)
        filename = state.confirmed_name or task.filename
        validate_copy_name(filename)
        state.status = "exporting"
        state.error_code = state.error_message = None
        _save(session, task, state)

        def record_publication(target: Path, identity: tuple[int, int]) -> None:
            # Durable before the atomic no-replace operation. If the process
            # dies afterwards, exact file identity plus digest proves success.
            state.attempted_path = str(target)
            state.published_device, state.published_inode = identity
            _save(session, task, state)

        published = publish_original_copy(
            resolve_task_storage_path(settings.storage_dir, task), destination, filename,
            expected_sha256=task.sha256, expected_size=task.size_bytes,
            before_publish=record_publication,
        )
        state.actual_path = str(published.path)
        state.status = "completed"
        _save(session, task, state)
    except (ValueError, OSError) as error:
        state.status = "failed"
        state.error_code = error.code if isinstance(error, CopyExportError) else "copy_failed"
        state.error_message = str(error) if isinstance(error, CopyExportError) else "副本导出失败，请检查目录权限和剩余空间；提取结果与内部原件不受影响。"
        _save(session, task, state)


def act_on_task_export(session: Session, settings: Settings, task_id: str, action: ExportAction) -> TaskRecord:
    with file_operation_lock(settings.storage_dir):
        session.expire_all()
        task = session.get(TaskRecord, task_id)
        if task is None:
            raise HTTPException(404, "没有找到这个任务。")
        state = task.file_export
        if task.status != "completed" or state is None or state.status not in {"failed", "needs_rebind", "pending", "awaiting_confirmation"}:
            raise HTTPException(409, "这个任务当前没有可操作的副本导出事项。")
        if action.action == "skip":
            state.status = "skipped"
            state.error_code = state.error_message = None
        else:
            if state.status == "needs_rebind" and not action.parent_path:
                raise HTTPException(422, "恢复备份后，请重新选择并确认导出文件夹。")
            if state.error_code == "publication_uncertain" and not action.acknowledge_uncertain:
                raise HTTPException(422, "请先检查上次导出位置，并明确确认是否重新导出。")
            if action.filename is not None:
                try:
                    validate_copy_name(action.filename)
                except CopyExportError as error:
                    raise HTTPException(422, str(error)) from error
                if Path(action.filename).suffix.lower() != Path(task.filename).suffix.lower():
                    raise HTTPException(422, "请保留原文件扩展名，只修改名称。")
                state.confirmed_name = action.filename
            if action.parent_path is not None:
                parent = Path(action.parent_path)
                if not parent.is_absolute() or not parent.is_dir():
                    raise HTTPException(422, "请选择存在且可访问的绝对文件夹路径。")
                if not state.folder_name:
                    raise HTTPException(422, "任务缺少模板文件夹信息。")
                state.parent_path = str(parent.resolve())
                state.destination = str(parent.resolve() / state.folder_name)
            state.status = "pending"
            state.error_code = state.error_message = None
            state.attempted_path = None
            state.published_device = state.published_inode = None
        _save(session, task, state)
    if action.action == "retry":
        process_task_export(session, settings, task_id)
    session.expire_all()
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(404, "任务记录已被删除。")
    return task


def recover_pending_exports(session: Session, settings: Settings) -> None:
    task_ids = list(session.scalars(select(TaskRecord.id).where(
        TaskRecord.status == "completed",
        func.json_extract(TaskRecord.export_state_json, "$.status").in_(["awaiting_confirmation", "pending", "exporting"]),
    ).limit(100)))
    for task_id in task_ids:
        process_task_export(session, settings, task_id)
