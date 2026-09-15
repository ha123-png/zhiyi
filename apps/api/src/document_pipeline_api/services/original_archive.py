"""Recoverable archive: verified publication precedes removal of the original.

Cross-volume moves are not filesystem/database transactions. The durable
publication identity and removal intent make crashes recoverable without ever
overwriting a destination or deleting a replacement source file.
"""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path

from document_pipeline_api.services.file_copies import CopyExportError, publish_original_copy, validate_copy_name
from document_pipeline_api.services.native_files import (
    guarded_directories, locked_original, matches_source, remove_locked_original, validate_local_path,
)
from document_pipeline_api.storage_paths import resolve_task_storage_path


def _digest(source):
    digest = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
    source.seek(0)
    return digest.hexdigest()


def _missing(path):
    try:
        path.lstat()
        return False
    except FileNotFoundError:
        return True


def cleanup_staging(state):
    if not state.staging_path:
        return
    staged = Path(state.staging_path)
    destination = Path(state.destination or "")
    if staged.parent != destination or not staged.name.startswith(".zhiyi-copy-") or staged.suffix != ".tmp":
        raise CopyExportError("publication_uncertain", "归档暂存记录异常，已停止操作。")
    if not _missing(staged):
        with locked_original(staged, delete=True) as temporary:
            info = os.fstat(temporary.fileno())
            if (info.st_dev, info.st_ino) != (state.published_device, state.published_inode):
                raise CopyExportError("publication_uncertain", "归档暂存文件已变化，知意不会删除它。")
            remove_locked_original(temporary)
    state.staging_path = None


def archive_original(session, settings, task, state):
    def save():
        task.export_state_json = state.model_dump_json()
        session.commit()

    try:
        if not task.source_file_json:
            raise CopyExportError("source_unavailable", "没有可移动的原文件位置。请在桌面版通过“选择文件”导入；此次可跳过归档，已保存的数据不受影响。")
        origin = json.loads(task.source_file_json)
        source_path = Path(origin["path"])
        if not state.parent_path or not state.destination or not state.folder_name:
            raise CopyExportError("destination_unavailable", "请选择归档文件夹。")
        parent = Path(state.parent_path)
        destination = Path(state.destination)
        validate_copy_name(state.folder_name)
        validate_copy_name(state.confirmed_name or task.filename)
        if destination != parent / state.folder_name:
            raise CopyExportError("invalid_destination", "归档位置已变化，请重新选择文件夹。")
        validate_local_path(parent)
        try:
            validate_local_path(source_path)
        except CopyExportError as error:
            raise CopyExportError("unsupported_source", "原文件位于网络位置、链接或云盘占位路径，暂不支持移动；请跳过此次归档，已保存的提取结果不受影响。") from error
        managed = settings.storage_dir.resolve()
        resolved = destination.resolve()
        if resolved.is_relative_to(managed) or managed.is_relative_to(resolved) or source_path.resolve().is_relative_to(managed):
            raise CopyExportError("invalid_destination", "请选择知意内部原件目录以外的文件夹。")
        with guarded_directories(parent):
            destination.mkdir(exist_ok=True)
            with guarded_directories(destination, source_path.parent):
                if state.staging_path:
                    cleanup_staging(state)
                    save()
                target = destination / (state.confirmed_name or task.filename)
                if source_path == target:
                    raise CopyExportError("same_location", "原文件已在目标位置，请跳过此次归档。")
                if state.attempted_path and Path(state.attempted_path) != target:
                    raise CopyExportError("publication_uncertain", "已写出的归档位置与记录不一致，原文件不会移除。")
                # Only a durable removal intent plus the verified publication can
                # prove completion after a crash between unlink and DB commit.
                absent = _missing(source_path)
                if absent and not state.source_removal_started:
                    raise CopyExportError("source_missing", "原文件已移动或删除，无法归档；可跳过此次归档。")
                with (nullcontext(None) if absent else locked_original(source_path, delete=True)) as source:
                    if source is not None and (not matches_source(source, origin) or _digest(source) != task.sha256):
                        raise CopyExportError("source_changed", "原文件在导入后已变化，知意不会移动或删除它；请跳过此次归档后重新导入。")
                    if not state.attempted_path:
                        if source is None:
                            raise CopyExportError("publication_uncertain", "无法核实归档结果，已停止操作。")
                        state.status = "exporting"
                        state.error_code = state.error_message = None
                        save()

                        def record_publication(path, identity):
                            state.attempted_path = str(path)
                            state.published_device, state.published_inode = identity
                            save()

                        def record_staging(path, identity):
                            state.staging_path = str(path)
                            state.published_device, state.published_inode = identity
                            save()

                        publish_original_copy(
                            resolve_task_storage_path(settings.storage_dir, task), destination, target.name,
                            expected_sha256=task.sha256, expected_size=task.size_bytes,
                            before_publish=record_publication,
                            on_staged=record_staging,
                        )
                        state.staging_path = None
                    if _missing(target):
                        # A crash before publication leaves the original intact.
                        # Never recreate after source removal was started.
                        if not state.source_removal_started and source is not None:
                            state.attempted_path = None
                            state.published_device = state.published_inode = None
                            raise CopyExportError("publication_interrupted", "上次归档写入中断，原文件仍在原位置，请重试。")
                        raise CopyExportError("publication_uncertain", "已写出的归档文件不在原位置，知意不会再次移动原文件。")
                    with locked_original(target) as published:
                        info = os.fstat(published.fileno())
                        if (info.st_dev, info.st_ino, info.st_size) != (state.published_device, state.published_inode, task.size_bytes) or _digest(published) != task.sha256:
                            raise CopyExportError("publication_uncertain", "归档目标已变化，知意不会覆盖它或移除原文件。")
                        if source is not None:
                            state.source_removal_started = True
                            save()
                            remove_locked_original(source)
                            source.close()  # target remains protected until removal is verified
                            if not _missing(source_path):
                                raise CopyExportError("source_busy", "归档文件已完整写入，原文件仍被占用；关闭占用程序后重试。")
                        state.actual_path = str(target)
                        state.status = "completed"
                        state.error_code = state.error_message = None
                        save()
    except (OSError, ValueError, KeyError) as error:
        if isinstance(error, CopyExportError) and error.code == "name_conflict" and not state.source_removal_started:
            # The no-replace publication failed. Its temporary inode never
            # became the foreign target; allow the user to choose another name.
            state.attempted_path = None
            state.staging_path = None
            state.published_device = state.published_inode = None
        state.status = "failed"
        state.error_code = error.code if isinstance(error, CopyExportError) else "archive_failed"
        if isinstance(error, CopyExportError):
            state.error_message = str(error)
        elif state.attempted_path:
            state.error_message = "归档尚未完成。请检查文件占用、目录权限和剩余空间后重试；不会覆盖目标或重新提取。"
        else:
            state.error_message = "归档受阻，原文件未移动。请检查文件占用、目录权限和剩余空间后重试。"
        save()
