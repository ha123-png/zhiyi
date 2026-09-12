"""Explicit, resumable erasure of this installation's managed local data.

Call only after stopping supervised children or acquiring the API maintenance
barrier in a worker-free development instance. A failed attempt leaves a journal
that disables processing until the user retries. External copies are never read.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.model_secrets import ModelSecretStore, create_model_secret_store
from document_pipeline_api.models import ModelProfileVersionRecord, TaskRecord
from document_pipeline_api.services.file_operation_lock import file_operation_lock


class ClearDataError(RuntimeError):
    pass


def _open_clear_connection(engine):
    """Retry only connection setup after supervised Windows children exit.

    No secret deletion or business SQL has begun. Persistent errors still leave
    the existing clear journal in place; no SQLite sidecar is removed manually.
    """
    for attempt in range(6):
        try:
            return engine.connect()
        except OperationalError as error:
            code = getattr(error.orig, "sqlite_errorcode", 0)
            if os.name != "nt" or code & 0xFF not in (5, 6, 10) or attempt == 5:
                raise
            import logging
            logging.getLogger(__name__).warning(
                "Retrying clear connection setup after %s (attempt %d/6)",
                getattr(error.orig, "sqlite_errorname", "SQLite connection error"),
                attempt + 1,
            )
            time.sleep(0.2)


def clear_journal_path(settings: Settings) -> Path:
    return settings.storage_dir.parent / "runtime" / "clear-data-pending.json"


def _checked(root: Path, path: Path) -> Path:
    absolute = path.absolute()
    if absolute == root or not absolute.is_relative_to(root):
        raise ClearDataError("清除路径不在当前知意数据目录内，未执行清除。")
    parent = absolute.parent
    while parent != root:
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ClearDataError("清除路径经过外部链接，已停止。")
        parent = parent.parent
    return absolute


def _remove_owned(root: Path, path: Path) -> None:
    path = _checked(root, path)
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink():
        path.unlink()

        return
    if hasattr(path, "is_junction") and path.is_junction():
        os.rmdir(path)  # Remove the junction itself, never traverse its target.
        return
    if getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
        raise ClearDataError("数据目录包含无法安全识别的重解析点，清除未完成。")
    if stat.S_ISDIR(details.st_mode):
        for entry in list(path.iterdir()):
            _remove_owned(root, entry)
        path.rmdir()
    else:
        path.unlink()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(value, file)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def clear_local_data(settings: Settings, secret_store: ModelSecretStore | None = None) -> dict:
    storage = settings.storage_dir.absolute()
    root = storage.parent
    database_value = make_url(settings.database_url).database
    if not database_value:
        raise ClearDataError("未找到当前业务数据库，未执行清除。")
    database = Path(database_value).absolute()
    if root == Path(root.anchor) or root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()) or database.parent != root:
        raise ClearDataError("数据目录或数据库位置不符合安全清除边界。")
    if storage.name != "uploads" or database.is_symlink():
        raise ClearDataError("当前使用非标准原件目录或数据库链接，请先迁移到标准数据目录。")
    journal = _checked(root, clear_journal_path(settings))
    for path in (root / "config" / "integration.json", root / "desktop-settings.json", root / "logs"):
        _checked(root, path)
    if journal.is_symlink():
        raise ClearDataError("清除恢复记录不能是外部链接。")
    with file_operation_lock(storage):
        engine = build_engine(settings.database_url)
        try:
            with _open_clear_connection(engine) as connection, Session(connection) as session:
                if journal.is_file():
                    pending = json.loads(journal.read_text(encoding="utf-8"))
                    if pending.get("version") != 1:
                        raise ClearDataError("清除恢复记录无法识别，请检查数据目录。")
                else:
                    refs = set(session.scalars(select(ModelProfileVersionRecord.secret_ref).where(ModelProfileVersionRecord.secret_ref.is_not(None))))
                    refs.update(session.scalars(select(TaskRecord.model_secret_ref).where(TaskRecord.model_secret_ref.is_not(None))))
                    pending = {"version": 1, "secret_refs": sorted(refs), "task_count": session.query(TaskRecord).count()}
                    _write_json(journal, pending)
                if pending["secret_refs"]:
                    store = secret_store or create_model_secret_store()
                    for ref in pending["secret_refs"]:
                        store.delete(ref)
                # Remove business rows in dependency order. Preserve Alembic
                # schema state, so restart cannot mistake this for an old DB.
                session.connection().exec_driver_sql("PRAGMA secure_delete=ON")
                for table in reversed(Base.metadata.sorted_tables):
                    session.execute(delete(table))
                session.commit()
                from document_pipeline_api.services.templates import ensure_builtin_templates
                from document_pipeline_api.services.model_profiles import ensure_default_local_profile
                ensure_builtin_templates(session)
                ensure_default_local_profile(session)
                session.commit()
            engine.dispose()
            for path in [storage, root / "desktop-settings.json", root / "config" / "integration.json", root / "logs"]:
                _remove_owned(root, path)
            for name in ("queue.db", "queue.db-wal", "queue.db-shm"):
                _remove_owned(root, root / name)
            storage.mkdir()
            # Explicitly empty tokens override environment fallbacks in a
            # still-running development API; supervised children restart too.
            from document_pipeline_api.services.integration_config import write_integration_config
            write_integration_config(root, {"read_token": "", "write_token": ""})
            result = {"state": "succeeded", "cleared_tasks": pending["task_count"], "completed_at": datetime.now(timezone.utc).isoformat()}
            _write_json(journal.parent / "clear-data-result.json", result)
            # Release the processing barrier only after success is durable.
            journal.unlink()
            return result
        except Exception as error:
            import logging
            logging.getLogger(__name__).exception("Local data clear did not finish")
            if isinstance(error, ClearDataError):
                raise
            raise ClearDataError("清除未完成，部分数据或密钥可能已清理。请重试清除；外部副本、备份和模型文件未被清理。") from error
        finally:
            engine.dispose()
