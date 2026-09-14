from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import shutil
import tempfile
import time
from typing import Any, Callable
from uuid import UUID
import zipfile

from sqlalchemy.engine import make_url

from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import upgrade_database
from document_pipeline_api.storage_paths import task_storage_name, resolve_original_reference


logger = logging.getLogger(__name__)


BACKUP_FORMAT = "document-pipeline-backup-v1"
MAX_BACKUP_FILES = 100_000
MAX_MANIFEST_BYTES = MAX_BACKUP_FILES * 512 + 4096
MAX_BACKUP_CONTENT_BYTES = 100 * 1024 * 1024 * 1024


class BusinessBackupError(RuntimeError):
    pass


def create_business_backup(settings: Settings, output_path: Path) -> Path:
    from document_pipeline_api.services.file_operation_lock import file_operation_lock
    with file_operation_lock(settings.storage_dir):
        return _create_business_backup(settings, output_path)


def _create_business_backup(settings: Settings, output_path: Path) -> Path:
    database_path = _database_path(settings)
    if not database_path.is_file():
        raise BusinessBackupError("业务数据库不存在，无法创建备份。")
    output = output_path.resolve()
    if output.exists():
        raise BusinessBackupError("目标备份文件已经存在，请更换名称。")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.name}.partial")
    if partial.exists():
        raise BusinessBackupError("目标目录存在未完成备份，请先检查后再重试。")

    runtime_dir = database_path.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="backup-", dir=runtime_dir) as temp:
            snapshot_path = Path(temp) / "database.db"
            _online_snapshot(database_path, snapshot_path)
            manifest, sources = _build_manifest(settings, snapshot_path)
            with zipfile.ZipFile(
                partial,
                mode="x",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                )
                archive.write(snapshot_path, "database.db")
                for item in manifest["files"]:
                    source = sources[item["task_id"]]
                    archive.write(source, item["archive_path"])
            inspect_business_backup(partial)
            partial.replace(output)
    except BusinessBackupError:
        partial.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        partial.unlink(missing_ok=True)
        raise BusinessBackupError("业务备份创建失败，未发布不完整文件。") from error
    return output


def inspect_business_backup(archive_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) > MAX_BACKUP_FILES + 2:
                raise BusinessBackupError("备份超过十万份原件的容量上限，请分批整理后重试。")
            if len(names) != len(set(names)):
                raise BusinessBackupError("备份包含重复成员。")
            for info in infos:
                _validate_member(info)
            if "manifest.json" not in names or "database.db" not in names:
                raise BusinessBackupError("备份缺少清单或数据库。")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise BusinessBackupError("备份清单超过安全上限。")
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            expected = _validate_manifest(manifest)
            if set(names) != expected:
                raise BusinessBackupError("备份成员与清单不一致。")
            total = sum(info.file_size for info in infos)
            if total > MAX_BACKUP_CONTENT_BYTES:
                raise BusinessBackupError("备份解压后的总大小超过安全上限。")
            _verify_zip_member(
                archive,
                archive.getinfo("database.db"),
                manifest["database"]["size_bytes"],
                manifest["database"]["sha256"],
            )
            for item in manifest["files"]:
                _verify_zip_member(
                    archive,
                    archive.getinfo(item["archive_path"]),
                    item["size_bytes"],
                    item["sha256"],
                )
            return manifest
    except BusinessBackupError:
        raise
    except (
        OSError,
        KeyError,
        RuntimeError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        zipfile.BadZipFile,
    ) as error:
        raise BusinessBackupError("备份格式损坏或不受支持。") from error


def restore_business_backup(
    settings: Settings,
    archive_path: Path,
    data_dir: Path,
    *,
    before_install: Callable[[], None] | None = None,
) -> Path:
    from document_pipeline_api.services.file_operation_lock import file_operation_lock
    with file_operation_lock(settings.storage_dir):
        return _restore_business_backup(settings, archive_path, data_dir, before_install=before_install)


def _restore_business_backup(
    settings: Settings,
    archive_path: Path,
    data_dir: Path,
    *,
    before_install: Callable[[], None] | None = None,
) -> Path:
    manifest = inspect_business_backup(archive_path)
    active_data_dir = data_dir.resolve()
    current_database = _database_path(settings)
    current_uploads = settings.storage_dir.resolve()
    if (
        current_database != active_data_dir / "document-pipeline.db"
        or current_uploads != active_data_dir / "uploads"
    ):
        raise BusinessBackupError("恢复目标不属于指定的正式数据目录。")
    runtime_dir = active_data_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    required_bytes = manifest["database"]["size_bytes"] + sum(
        item["size_bytes"] for item in manifest["files"]
    )
    if shutil.disk_usage(active_data_dir).free < required_bytes:
        raise BusinessBackupError("可用磁盘空间不足，恢复尚未修改当前数据。")
    with tempfile.TemporaryDirectory(prefix="restore-", dir=runtime_dir) as temp:
        stage = Path(temp)
        stage_database = stage / "database.db"
        stage_uploads = stage / "uploads"
        stage_uploads.mkdir()
        _extract_staged_backup(archive_path, manifest, stage_database, stage_uploads)
        _prepare_staged_database(stage_database, stage_uploads, manifest)
        if before_install is not None:
            before_install()
        return _install_staged_restore(
            active_data_dir,
            current_database,
            current_uploads,
            stage_database,
            stage_uploads,
        )


def force_close_main_database_connections(database_path: Path) -> None:
    """强制关闭进程内所有仍指向主库的 sqlite 连接。

    Windows 上任何打开的文件句柄都会阻止替换（WinError 32）。SQLAlchemy
    dispose 只能关闭池中空闲连接，staging 期间新建的池连接需要在替换前
    兜底关闭，否则恢复替换失败并回滚。
    """
    import gc
    import sqlite3

    target = str(database_path.resolve())
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            try:
                rows = obj.execute("PRAGMA database_list").fetchall()
            except Exception:
                continue
            if any(row[2] == target for row in rows):
                try:
                    obj.close()
                except Exception:
                    pass


def _database_path(settings: Settings) -> Path:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise BusinessBackupError("只有本地文件数据库支持完整业务备份。")
    return Path(url.database).resolve()


def _extract_staged_backup(
    archive_path: Path,
    manifest: dict[str, Any],
    database_path: Path,
    uploads_dir: Path,
) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _copy_zip_member(
                archive,
                archive.getinfo("database.db"),
                database_path,
                manifest["database"]["size_bytes"],
                manifest["database"]["sha256"],
            )
            for item in manifest["files"]:
                _copy_zip_member(
                    archive,
                    archive.getinfo(item["archive_path"]),
                    uploads_dir / Path(item["archive_path"]).name,
                    item["size_bytes"],
                    item["sha256"],
                )
    except BusinessBackupError:
        raise
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
        raise BusinessBackupError("备份在恢复读取期间发生变化或损坏。") from error


def _copy_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    expected_size: int,
    expected_digest: str,
) -> None:
    digest = hashlib.sha256()
    total = 0
    with archive.open(info) as source, destination.open("xb") as target:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > expected_size:
                raise BusinessBackupError("备份成员超过声明大小。")
            digest.update(chunk)
            target.write(chunk)
    if total != expected_size or digest.hexdigest() != expected_digest:
        raise BusinessBackupError("备份成员在恢复时校验失败。")


def _prepare_staged_database(
    database_path: Path,
    uploads_dir: Path,
    manifest: dict[str, Any],
) -> None:
    engine = build_engine(f"sqlite:///{database_path.resolve()}")
    try:
        upgrade_database(engine)
    finally:
        engine.dispose()
    expected = {item["task_id"]: item for item in manifest["files"]}
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT id, content_type, size_bytes, sha256 FROM tasks ORDER BY id"
        ).fetchall()
        if {row[0] for row in rows} != set(expected):
            raise BusinessBackupError("备份数据库与原文件任务集合不一致。")
        for task_id, content_type, size_bytes, sha256 in rows:
            item = expected[task_id]
            name = task_storage_name(task_id, content_type)
            actual_size, actual_digest = _hash_file(uploads_dir / name)
            if (
                item["content_type"] != content_type
                or item["size_bytes"] != size_bytes
                or item["sha256"] != sha256
                or actual_size != size_bytes
                or actual_digest != sha256
            ):
                raise BusinessBackupError("恢复文件与数据库记录不一致。")
            connection.execute(
                "UPDATE tasks SET storage_path = ?, internal_storage_json = NULL WHERE id = ?",
                (name, task_id),
            )
        # A portable restore must never resume writes into another machine's
        # directories. Keep historical destinations, but require explicit rebind.
        connection.execute("UPDATE template_local_bindings SET enabled = 0, revision = revision + 1")
        from document_pipeline_api.schemas.file_export import TaskExportState
        for task_id, state_json in connection.execute(
            "SELECT id, export_state_json FROM tasks WHERE export_state_json IS NOT NULL"
        ).fetchall():
            state = TaskExportState.model_validate_json(state_json)
            if state.status not in {"disabled", "completed", "skipped"}:
                state.status = "needs_rebind"
                state.error_code = "backup_restored"
                state.error_message = "已恢复备份；请重新选择并确认外部副本目标，不会自动写入旧路径。"
                connection.execute("UPDATE tasks SET export_state_json = ? WHERE id = ?", (state.model_dump_json(), task_id))
        connection.execute("UPDATE assistant_runs SET status='interrupted', error='已恢复备份；历史内容保留，请重新提问。' WHERE status IN ('running','waiting','cancelling')")
        connection.execute("UPDATE assistant_tool_calls SET status='expired' WHERE status='pending'")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        journal_mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if journal_mode is None or journal_mode[0].lower() != "delete":
            raise BusinessBackupError("恢复数据库无法合并临时日志。")
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise BusinessBackupError("恢复数据库完整性检查失败。")
    finally:
        connection.close()
    if Path(f"{database_path}-wal").exists() or Path(f"{database_path}-shm").exists():
        raise BusinessBackupError("恢复数据库仍包含未合并的临时日志。")


def _install_staged_restore(
    data_dir: Path,
    current_database: Path,
    current_uploads: Path,
    stage_database: Path,
    stage_uploads: Path,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    rollback = data_dir / "backups" / f"before-restore-{timestamp}"
    rollback.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    current_items = [
        current_database,
        Path(f"{current_database}-wal"),
        Path(f"{current_database}-shm"),
        Path(f"{current_database}-journal"),
        current_uploads,
        data_dir / "queue.db",
        data_dir / "queue.db-wal",
        data_dir / "queue.db-shm",
        data_dir / "queue.db-journal",
    ]
    try:
        for source in current_items:
            if source.exists():
                destination = rollback / source.name
                _move_path(source, destination)
                moved.append((source, destination))
        current_database.parent.mkdir(parents=True, exist_ok=True)
        current_uploads.parent.mkdir(parents=True, exist_ok=True)
        _move_path(stage_database, current_database)
        installed.append(current_database)
        _move_path(stage_uploads, current_uploads)
        installed.append(current_uploads)
        connection = sqlite3.connect(current_database)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise BusinessBackupError("安装恢复数据后的数据库完整性检查失败。")
        finally:
            connection.close()
    except Exception as error:
        logger.exception("恢复替换失败，回滚中：%s", type(error).__name__)
        for path in reversed(installed):
            _remove_installed_path(path)
        for original, saved in reversed(moved):
            if saved.exists():
                _move_path(saved, original)
        if isinstance(error, BusinessBackupError):
            raise
        raise BusinessBackupError(
            f"恢复替换失败，当前数据已回滚。{type(error).__name__}: {error}"
        ) from error
    return rollback


def _move_path(source: Path, destination: Path) -> None:
    # Windows 上 Defender/延迟句柄会瞬时占用文件或目录（WinError 32/5）；
    # 短暂重试几次，避免偶发锁导致整次恢复回滚
    for attempt in range(4):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.3)


def _remove_installed_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _online_snapshot(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise BusinessBackupError("数据库快照完整性检查失败。")
    finally:
        destination.close()
        source.close()


def _build_manifest(
    settings: Settings,
    snapshot_path: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    files: list[dict[str, Any]] = []
    sources: dict[str, Path] = {}
    connection = sqlite3.connect(snapshot_path)
    connection.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        internal_column = "internal_storage_json" if "internal_storage_json" in columns else "NULL AS internal_storage_json"
        tasks = connection.execute(
            f"SELECT id, content_type, size_bytes, sha256, storage_path, {internal_column} "
            f"FROM tasks ORDER BY id LIMIT {MAX_BACKUP_FILES + 1}"
        ).fetchall()
        if len(tasks) > MAX_BACKUP_FILES:
            raise BusinessBackupError("备份超过十万份原件的容量上限，请分批整理后重试。")
        for task in tasks:
            task_id = task["id"]
            content_type = task["content_type"]
            _validate_task_id(task_id)
            name = task_storage_name(task_id, content_type)
            source = _resolve_backup_source(
                settings.storage_dir,
                task["storage_path"],
                name,
                (json.loads(task["internal_storage_json"]) if task["internal_storage_json"] else {}).get("previous_path"),
            )
            if not source.is_file():
                raise BusinessBackupError("存在无法找到的任务原文件，备份已停止。")
            size, digest = _hash_file(source)
            if size != task["size_bytes"] or digest != task["sha256"]:
                raise BusinessBackupError("任务原文件与数据库摘要不一致，备份已停止。")
            files.append(
                {
                    "task_id": task_id,
                    "archive_path": f"files/{name}",
                    "size_bytes": size,
                    "sha256": digest,
                    "content_type": content_type,
                }
            )
            sources[task_id] = source
    finally:
        connection.close()
    database_size, database_digest = _hash_file(snapshot_path)
    manifest = {
        "format": BACKUP_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "archive_path": "database.db",
            "size_bytes": database_size,
            "sha256": database_digest,
        },
        "files": files,
        "credentials_included": False,
    }
    return manifest, sources


def _validate_manifest(manifest: Any) -> set[str]:
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
        raise BusinessBackupError("备份版本不受支持。")
    if manifest.get("credentials_included") is not False:
        raise BusinessBackupError("备份的凭据边界声明无效。")
    database = manifest.get("database")
    files = manifest.get("files")
    if not isinstance(database, dict) or not isinstance(files, list):
        raise BusinessBackupError("备份清单结构无效。")
    _validate_digest_record(database, expected_path="database.db")
    expected = {"manifest.json", "database.db"}
    task_ids: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise BusinessBackupError("备份文件记录无效。")
        task_id = item.get("task_id")
        content_type = item.get("content_type")
        if not isinstance(task_id, str) or not isinstance(content_type, str):
            raise BusinessBackupError("备份任务标识无效。")
        _validate_task_id(task_id)
        if task_id in task_ids:
            raise BusinessBackupError("备份包含重复任务文件。")
        task_ids.add(task_id)
        expected_path = f"files/{task_storage_name(task_id, content_type)}"
        _validate_digest_record(item, expected_path=expected_path)
        expected.add(expected_path)
    return expected


def _validate_digest_record(item: dict[str, Any], *, expected_path: str) -> None:
    if item.get("archive_path") != expected_path:
        raise BusinessBackupError("备份文件路径与任务不一致。")
    size = item.get("size_bytes")
    digest = item.get("sha256")
    if not isinstance(size, int) or size < 0 or size > MAX_BACKUP_CONTENT_BYTES:
        raise BusinessBackupError("备份文件大小声明无效。")
    if not isinstance(digest, str) or len(digest) != 64:
        raise BusinessBackupError("备份文件摘要声明无效。")
    try:
        int(digest, 16)
    except ValueError as error:
        raise BusinessBackupError("备份文件摘要声明无效。") from error


def _validate_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or info.is_dir()
    ):
        raise BusinessBackupError("备份包含不安全路径。")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise BusinessBackupError("备份不能包含符号链接。")


def _verify_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    expected_size: int,
    expected_digest: str,
) -> None:
    if info.file_size != expected_size:
        raise BusinessBackupError("备份成员大小与清单不一致。")
    digest = hashlib.sha256()
    total = 0
    with archive.open(info) as source:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > expected_size:
                raise BusinessBackupError("备份成员超过声明大小。")
            digest.update(chunk)
    if total != expected_size or digest.hexdigest() != expected_digest:
        raise BusinessBackupError("备份成员校验和不一致。")


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _resolve_backup_source(storage_dir: Path, stored_value: str, expected_name: str, previous: str | None = None) -> Path:
    try:
        return resolve_original_reference(storage_dir, stored_value, expected_name, previous)
    except ValueError as error:
        raise BusinessBackupError("任务原文件引用无效，备份已停止。") from error


def _validate_task_id(task_id: str) -> None:
    try:
        parsed = UUID(task_id)
    except ValueError as error:
        raise BusinessBackupError("备份任务标识不是有效 UUID。") from error
    if str(parsed) != task_id:
        raise BusinessBackupError("备份任务标识格式不规范。")
