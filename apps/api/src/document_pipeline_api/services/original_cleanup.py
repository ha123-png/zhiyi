"""Retry internal original deletion without losing obligations on crash or lock."""
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.models import PendingOriginalDeletionRecord, TaskRecord
from document_pipeline_api.services.file_operation_lock import file_operation_lock
from document_pipeline_api.storage_paths import ensure_managed_path


def enqueue_original_deletions(session: Session, settings: Settings, paths: list[Path]) -> None:
    root = settings.storage_dir.resolve()
    for path in paths:
        safe = ensure_managed_path(settings.storage_dir, path)
        relative = safe.relative_to(root).as_posix()
        if session.get(PendingOriginalDeletionRecord, relative) is None:
            session.add(PendingOriginalDeletionRecord(relative_path=relative, task_id=safe.stem))
    session.flush()


def enqueue_rendered_deletions(session: Session, task_ids: list[str]) -> None:
    """Persist a lexical target even if a hostile reparse point blocks cleanup."""
    for task_id in task_ids:
        if not task_id or task_id in {".", ".."} or any(char in task_id for char in "/\\:\x00"):
            raise ValueError("Invalid rendered cleanup identity")
        relative = f"rendered/{task_id}"
        if session.get(PendingOriginalDeletionRecord, relative) is None:
            session.add(PendingOriginalDeletionRecord(relative_path=relative, task_id=task_id))
    session.flush()


def pending_cleanup_count(session: Session, task_ids: list[str] | None = None) -> int:
    query = select(func.count()).select_from(PendingOriginalDeletionRecord)
    if task_ids is not None:
        query = query.where(PendingOriginalDeletionRecord.task_id.in_(task_ids))
    return session.scalar(query) or 0


def _remove_rendered_tree(root: Path, directory: Path) -> None:
    """Delete one task's derived pages, retaining the obligation on any failure."""
    safe = ensure_managed_path(root, directory)
    if not safe.exists():
        return
    if not safe.is_dir():
        raise ValueError("Rendered cleanup target is not a directory")
    # Validate every descendant immediately before touching it. Never follow
    # a reparse point that could lead outside the managed storage tree.
    for child in safe.iterdir():
        validated = ensure_managed_path(root, child)
        if validated.is_dir():
            _remove_rendered_tree(root, validated)
        else:
            validated.unlink()
    safe.rmdir()


def drain_original_deletions(session: Session, settings: Settings, *, limit: int = 100) -> int:
    """Caller holds file_operation_lock; only committed, ownerless paths qualify."""
    pending = list(session.scalars(select(PendingOriginalDeletionRecord)
        .order_by(PendingOriginalDeletionRecord.attempts, PendingOriginalDeletionRecord.created_at).limit(limit)))
    for entry in pending:
        if session.get(TaskRecord, entry.task_id) is not None:
            entry.failure_code = "owner_exists"
            entry.attempts += 1
            continue
        try:
            path = Path(entry.relative_path)
            if path.is_absolute() or ".." in path.parts or path.stem != entry.task_id:
                raise ValueError("Invalid cleanup identity")
            safe = ensure_managed_path(settings.storage_dir, settings.storage_dir / path)
            if len(path.parts) == 2 and path.parts[0] == "rendered" and path.name == entry.task_id:
                _remove_rendered_tree(settings.storage_dir, safe)
            elif len(path.parts) == 1 or (len(path.parts) == 3 and path.parts[0] == "templates"):
                safe.unlink(missing_ok=True)
            else:
                raise ValueError("Invalid cleanup path shape")
        except (OSError, ValueError) as error:
            entry.attempts += 1
            entry.failure_code = "invalid_path" if isinstance(error, ValueError) else "file_unavailable"
        else:
            session.delete(entry)
    # If this commit fails, a later retry safely observes missing files.
    session.commit()
    return pending_cleanup_count(session)


def recover_original_deletions(session_factory, settings: Settings) -> None:
    with session_factory() as session:
        if not pending_cleanup_count(session):
            return
        with file_operation_lock(settings.storage_dir):
            drain_original_deletions(session, settings)
