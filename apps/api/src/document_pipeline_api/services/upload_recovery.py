"""Crash recovery for the file-system half of task uploads.

Task originals are published just before the task row commits. A process exit in
that gap leaves a final file with no database owner. Each upload therefore
keeps a small, per-task intent under the non-restorable runtime directory until
the task commit and queue hand-off boundary is safe to recover.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import uuid

from document_pipeline_api.config import Settings
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services.dataset_identity import dataset_generation
from document_pipeline_api.storage_paths import CONTENT_SUFFIXES


logger = logging.getLogger(__name__)
_RECOVERY_LIMIT = 100
_CANONICAL_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_UPLOAD_SUFFIXES = tuple(sorted(set(CONTENT_SUFFIXES.values())))


@dataclass
class UploadIntent:
    task_id: str
    directory: Path
    marker_path: Path
    lock_path: Path
    lock_handle: object


def _strict_uuid(value: str) -> bool:
    return bool(_CANONICAL_UUID.fullmatch(value)) and str(uuid.UUID(value)) == value


def _is_reparse_point(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _intent_directory(data_dir: Path, *, create: bool) -> Path:
    root = data_dir.resolve()
    runtime = root / "runtime"
    if _is_reparse_point(runtime):
        raise OSError("Upload recovery runtime directory cannot be a reparse point")
    if create:
        runtime.mkdir(parents=True, exist_ok=True)
    directory = runtime / "ingest-pending"
    if _is_reparse_point(directory):
        raise OSError("Upload recovery directory cannot be a reparse point")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _lock_handle(handle: object) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: object) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def begin_upload_intent(data_dir: Path, task_id: str) -> UploadIntent:
    """Persist an intent and hold its OS lock before creating upload bytes."""
    if not _strict_uuid(task_id):
        raise ValueError("Invalid task upload identity")
    directory = _intent_directory(data_dir, create=True)
    marker_path = directory / f"{task_id}.json"
    temporary_marker = directory / f"{task_id}.json.tmp"
    lock_path = directory / f"{task_id}.lock"
    if any(_is_reparse_point(path) or path.exists() for path in (marker_path, temporary_marker)):
        raise FileExistsError("An upload intent already exists for this task")
    if _is_reparse_point(lock_path):
        raise OSError("Upload intent lock cannot be a reparse point")

    lock_handle = lock_path.open("a+b")
    try:
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"0")
            lock_handle.flush()
            os.fsync(lock_handle.fileno())
        _lock_handle(lock_handle)
        # The task ID is generated locally. No user path is written to the
        # journal, so recovery can only derive these fixed managed names.
        value = {
            "version": 1,
            "task_id": task_id,
            "dataset_generation": dataset_generation(data_dir),
        }
        with temporary_marker.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_marker, marker_path)
        return UploadIntent(
            task_id=task_id,
            directory=directory,
            marker_path=marker_path,
            lock_path=lock_path,
            lock_handle=lock_handle,
        )
    except BaseException:
        try:
            temporary_marker.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            _unlock_handle(lock_handle)
        except OSError:
            pass
        lock_handle.close()
        if not marker_path.exists():
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def finish_upload_intent(intent: UploadIntent, *, remove_marker: bool) -> None:
    """Release this upload's lock; preserve the marker when cleanup is unsure."""
    if remove_marker:
        try:
            intent.marker_path.unlink(missing_ok=True)
        except OSError:
            # Startup and periodic recovery will retry with the database owner
            # check still in place.
            pass
    try:
        _unlock_handle(intent.lock_handle)
    except OSError:
        pass
    finally:
        intent.lock_handle.close()
    if remove_marker and not intent.marker_path.exists():
        try:
            intent.lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _try_lock(lock_path: Path):
    if _is_reparse_point(lock_path):
        return None
    try:
        handle = lock_path.open("r+b")
    except OSError:
        return None
    try:
        _lock_handle(handle)
    except OSError:
        handle.close()
        return None
    return handle


def _unlock_and_close(handle: object) -> None:
    try:
        _unlock_handle(handle)
    except OSError:
        pass
    finally:
        handle.close()


def _candidate_markers(directory: Path, limit: int):
    """Yield a bounded set of flat marker files with canonical UUID names."""
    candidates: list[tuple[str, Path, bool]] = []
    examined = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                examined += 1
                if examined > limit * 4:
                    break
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name
                temporary = name.endswith(".json.tmp")
                if temporary:
                    task_id = name[: -len(".json.tmp")]
                elif name.endswith(".json"):
                    task_id = name[: -len(".json")]
                else:
                    continue
                if _strict_uuid(task_id):
                    candidates.append((task_id, Path(entry.path), temporary))
                    if len(candidates) >= limit:
                        break
    except OSError:
        return []
    candidates.sort(key=lambda item: (item[2], item[0]))
    return candidates


def _remove_upload_artifacts(settings: Settings, task_id: str) -> bool:
    """Remove only the exact flat files this service can publish for this ID."""
    paths = [settings.storage_dir / f".{task_id}.uploading"]
    paths.extend(settings.storage_dir / f"{task_id}{suffix}" for suffix in _UPLOAD_SUFFIXES)
    try:
        for path in paths:
            path.unlink(missing_ok=True)
    except OSError:
        return False
    return all(not path.exists() for path in paths)


def _remove_staging_upload(settings: Settings, task_id: str) -> bool:
    path = settings.storage_dir / f".{task_id}.uploading"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return not path.exists()


def _empty_recovery_summary() -> dict[str, int]:
    return {
        "scanned": 0,
        "orphan_files_removed": 0,
        "owned_uploads_preserved": 0,
        "active_uploads_skipped": 0,
        "stale_generation_markers_removed": 0,
        "cleanup_failures": 0,
        "blocked_by_maintenance": 0,
    }


def recover_pending_uploads(
    session_factory,
    settings: Settings,
    *,
    limit: int = _RECOVERY_LIMIT,
) -> dict[str, int]:
    """Reconcile intents while serialized with restore, clear, and deletion."""
    from fastapi import HTTPException

    from document_pipeline_api.services.file_operation_lock import file_operation_lock

    data_dir = settings.storage_dir.parent
    try:
        directory = _intent_directory(data_dir, create=False)
        if not directory.is_dir():
            return _empty_recovery_summary()
        with os.scandir(directory) as entries:
            if next(entries, None) is None:
                return _empty_recovery_summary()
    except OSError:
        # Let the locked pass classify an unsafe or unreadable journal path.
        pass

    try:
        with file_operation_lock(settings.storage_dir):
            return _recover_pending_uploads_locked(session_factory, settings, limit=limit)
    except HTTPException as error:
        if error.status_code == 409:
            blocked = _empty_recovery_summary()
            blocked["blocked_by_maintenance"] = 1
            return blocked
        raise


def _recover_pending_uploads_locked(
    session_factory,
    settings: Settings,
    *,
    limit: int,
) -> dict[str, int]:
    """Reconcile at most ``limit`` durable upload intents without guessing.

    A live uploader owns the per-ID OS lock and is skipped. The active dataset
    generation and a successful database read are required before deleting any
    original bytes. Restore/clear journals and their shared file lock leave all
    journal entries untouched.
    """
    from document_pipeline_api.business_backup import restore_journal_path
    from document_pipeline_api.services.clear_data import clear_journal_path

    summary = _empty_recovery_summary()
    data_dir = settings.storage_dir.parent
    if clear_journal_path(settings).exists() or restore_journal_path(data_dir).exists():
        summary["blocked_by_maintenance"] = 1
        return summary
    try:
        directory = _intent_directory(data_dir, create=False)
    except OSError:
        summary["cleanup_failures"] = 1
        return summary
    if not directory.is_dir():
        return summary

    try:
        generation = dataset_generation(data_dir)
    except (OSError, ValueError, KeyError, TypeError):
        summary["cleanup_failures"] = 1
        return summary
    max_items = min(max(int(limit), 1), 1000)
    for task_id, marker_path, temporary in _candidate_markers(directory, max_items):
        summary["scanned"] += 1
        lock_path = directory / f"{task_id}.lock"
        lock_handle = _try_lock(lock_path)
        if lock_handle is None:
            summary["active_uploads_skipped"] += 1
            continue
        try:
            if temporary:
                # Upload bytes are not created until the complete marker is
                # atomically published, so an abandoned marker temp is safe to
                # remove without consulting the business database.
                try:
                    marker_path.unlink(missing_ok=True)
                except OSError:
                    summary["cleanup_failures"] += 1
                continue
            try:
                value = json.loads(marker_path.read_text(encoding="utf-8"))
                if (
                    not isinstance(value, dict)
                    or value.get("version") != 1
                    or value.get("task_id") != task_id
                    or not isinstance(value.get("dataset_generation"), str)
                ):
                    summary["cleanup_failures"] += 1
                    continue
            except (OSError, ValueError, TypeError):
                summary["cleanup_failures"] += 1
                continue

            if value["dataset_generation"] != generation:
                # A restore or clear advanced the dataset identity. Do not
                # touch a same-named original in the newly installed dataset.
                try:
                    marker_path.unlink(missing_ok=True)
                except OSError:
                    summary["cleanup_failures"] += 1
                else:
                    summary["stale_generation_markers_removed"] += 1
                continue

            try:
                with session_factory() as session:
                    owner = session.get(TaskRecord, task_id)
            except Exception:
                # An unavailable database is not evidence that this file is
                # ownerless. Keep both marker and original for the next pass.
                summary["cleanup_failures"] += 1
                break

            if owner is None:
                cleaned = _remove_upload_artifacts(settings, task_id)
                if not cleaned:
                    summary["cleanup_failures"] += 1
                    continue
                summary["orphan_files_removed"] += 1
            else:
                if not _remove_staging_upload(settings, task_id):
                    summary["cleanup_failures"] += 1
                    continue
                summary["owned_uploads_preserved"] += 1

            try:
                marker_path.unlink(missing_ok=True)
            except OSError:
                summary["cleanup_failures"] += 1
        finally:
            # On Windows an open marker/lock may not be unlinkable. The marker
            # lives separately from the handle, but defer lock deletion until
            # after releasing it.
            _unlock_and_close(lock_handle)
            temporary_marker = directory / f"{task_id}.json.tmp"
            if not marker_path.exists() and not temporary_marker.exists():
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
    return summary
