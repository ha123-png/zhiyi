"""业务备份的普通用户 API：创建、列表、删除、恢复与状态。

继续使用 business_backup 的一致快照 + SHA-256 清单契约；备份文件固定存放在
数据目录的 backups/ 子目录，文件名带 UTC 时间戳，便于按时间排序与保留策略。
恢复采用"暂存校验 → 替换"且失败自动回滚；替换前关闭 API 侧数据库连接池，
队列进程持有文件句柄时给出"请先停止应用"的明确提示（ISSUE-069）。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from document_pipeline_api.business_backup import (
    BusinessBackupError,
    create_business_backup,
    inspect_business_backup,
    restore_business_backup,
)
from document_pipeline_api.config import Settings
from document_pipeline_api.public_errors import public_error_message


router = APIRouter(prefix="/backups", tags=["backups"])

BACKUP_SUFFIX = ".dpbak"
BACKUP_RETENTION = 10  # 最多保留的备份份数，超出自动删除最旧


class BackupRead(BaseModel):
    name: str
    size_bytes: int
    created_at: str


class BackupStatus(BaseModel):
    backup_dir: str
    count: int
    retention: int
    free_bytes: int
    last_success_at: str | None = None
    database_size_bytes: int
    uploads_size_bytes: int
    restore_state: str | None = None
    restore_message: str | None = None


class RestoreResult(BaseModel):
    rollback_dir: str | None = None
    restart_required: bool
    scheduled: bool = False


def _backup_dir(settings: Settings) -> Path:
    return settings.storage_dir.parent / "backups"


def _parse_backup_name(name: str) -> datetime | None:
    """文件名形如 20260810T010203000000Z.dpbak → UTC 时间；格式不符返回 None。"""
    if not name.endswith(BACKUP_SUFFIX):
        return None
    stem = name[: -len(BACKUP_SUFFIX)]
    try:
        return datetime.strptime(stem, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _list_backup_files(settings: Settings) -> list[Path]:
    backup_dir = _backup_dir(settings)
    if not backup_dir.is_dir():
        return []
    valid = [
        path
        for path in backup_dir.iterdir()
        if path.is_file() and _parse_backup_name(path.name) is not None
    ]
    return sorted(valid, key=lambda path: _parse_backup_name(path.name) or datetime.min)


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _database_size(settings: Settings) -> int:
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return 0
    database = Path(url.database).resolve()
    total = database.stat().st_size if database.is_file() else 0
    for suffix in ("-wal", "-shm", "-journal"):
        extra = Path(f"{database}{suffix}")
        if extra.is_file():
            total += extra.stat().st_size
    return total


@router.get("", response_model=list[BackupRead])
def list_backups(request: Request) -> list[BackupRead]:
    """备份列表（按时间倒序，最新在前）。"""
    settings: Settings = request.app.state.settings
    backups = list(reversed(_list_backup_files(settings)))
    return [
        BackupRead(
            name=path.name,
            size_bytes=path.stat().st_size,
            created_at=(_parse_backup_name(path.name) or datetime.min).isoformat(),
        )
        for path in backups
    ]


@router.get("/status", response_model=BackupStatus)
def backup_status(request: Request) -> BackupStatus:
    """备份状态：目录、数量、保留上限、剩余空间与最近成功时间。"""
    settings: Settings = request.app.state.settings
    backup_dir = _backup_dir(settings)
    backups = _list_backup_files(settings)
    disk_path = backup_dir if backup_dir.is_dir() else settings.storage_dir.parent
    free_bytes = shutil.disk_usage(disk_path).free
    last = backups[-1] if backups else None
    restore_state = None
    restore_message = None
    restore_result = settings.storage_dir.parent / "runtime" / "restore-result.json"
    try:
        if restore_result.is_file() and restore_result.stat().st_size <= 4096:
            payload = json.loads(restore_result.read_text(encoding="utf-8"))
            if payload.get("state") in {"succeeded", "failed"}:
                restore_state = payload["state"]
                restore_message = str(payload.get("message") or "")[:500]
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return BackupStatus(
        backup_dir=str(backup_dir),
        count=len(backups),
        retention=BACKUP_RETENTION,
        free_bytes=free_bytes,
        last_success_at=(
            (_parse_backup_name(last.name) or datetime.min).isoformat()
            if last is not None
            else None
        ),
        database_size_bytes=_database_size(settings),
        uploads_size_bytes=_dir_size(settings.storage_dir),
        restore_state=restore_state,
        restore_message=restore_message,
    )


@router.post("", response_model=BackupRead, status_code=status.HTTP_201_CREATED)
def create_backup(request: Request) -> BackupRead:
    """创建备份：空间预检后生成一致快照；超出保留数量自动删除最旧备份。"""
    settings: Settings = request.app.state.settings
    backup_dir = _backup_dir(settings)
    backup_dir.mkdir(parents=True, exist_ok=True)
    estimated = _database_size(settings) + _dir_size(settings.storage_dir)
    if shutil.disk_usage(backup_dir).free < estimated + 16 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="磁盘空间不足，无法创建备份。请清理空间后重试。",
        )
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}{BACKUP_SUFFIX}"
    output = backup_dir / name
    try:
        create_business_backup(settings, output)
    except BusinessBackupError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=public_error_message(error, "备份创建失败，请检查磁盘空间后重试。"),
        ) from error
    backups = _list_backup_files(settings)
    for old in backups[: max(0, len(backups) - BACKUP_RETENTION)]:
        old.unlink(missing_ok=True)
    return BackupRead(
        name=name,
        size_bytes=output.stat().st_size,
        created_at=(_parse_backup_name(name) or datetime.min).isoformat(),
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(request: Request, name: str) -> None:
    settings: Settings = request.app.state.settings
    if _parse_backup_name(name) is None:
        raise HTTPException(status_code=404, detail="没有找到这个备份。")
    path = _backup_dir(settings) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="没有找到这个备份。")
    path.unlink()


@router.post("/{name}/restore", response_model=RestoreResult)
def restore_backup(request: Request, name: str) -> RestoreResult:
    """从备份恢复：校验清单 → 暂存 → 替换数据目录；失败自动回滚。

    替换前关闭 API 侧数据库连接池；若其他进程（队列消费者）仍占用数据库文件，
    替换会失败并回滚，此时返回 409 并提示先停止应用后重试。
    """
    settings: Settings = request.app.state.settings
    if _parse_backup_name(name) is None:
        raise HTTPException(status_code=404, detail="没有找到这个备份。")
    archive = _backup_dir(settings) / name
    if not archive.is_file():
        raise HTTPException(status_code=404, detail="没有找到这个备份。")
    try:
        inspect_business_backup(archive)
    except BusinessBackupError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=public_error_message(error, "恢复未完成，当前数据没有改变。请检查备份文件后重试。"),
        ) from error

    # 正式桌面由监督器持有 instance.lock。API 只安排维护，监督器会在响应送达后
    # 停止自己创建的 API/Worker、恢复并重启；开发/测试进程没有监督器时才直接恢复。
    data_dir = settings.storage_dir.parent
    supervisor_state = data_dir / "runtime" / "supervisor.json"
    supervised = os.getenv("DOCUMENT_PIPELINE_SUPERVISED") == "1"
    if supervised or supervisor_state.is_file():
        from document_pipeline_api.supervisor import request_restore

        if not supervisor_state.is_file():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="应用仍在启动处理组件，请稍候几秒再恢复；当前数据未改变。",
            )
        if not request_restore(data_dir, name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="无法安排安全恢复，请重启应用后再试；当前数据未改变。",
            )
        return RestoreResult(restart_required=True, scheduled=True)
    try:
        session_factory = request.app.state.session_factory
        bind = getattr(session_factory, "kw", {}).get("bind")
        if bind is not None:
            bind.dispose()
        # 队列存储持有 queue.db 的常驻连接，Windows 下会阻止替换文件，必须先释放，
        # 否则 _install_staged_restore 移动文件失败并回滚（“恢复替换失败，当前数据已回滚”）。
        # 恢复完成后需要重启应用重新建立队列连接（响应已标记 restart_required）。
        from document_pipeline_api.queue import huey

        huey.storage.close()

        def _close_main_db_before_install() -> None:
            # staging 期间 SQLAlchemy 可能已重建主库池连接；替换前兜底关闭，
            # 否则 Windows 上文件被占用导致替换失败并回滚
            from document_pipeline_api.business_backup import (
                force_close_main_database_connections,
            )

            force_close_main_database_connections(data_dir / "document-pipeline.db")
            if bind is not None:
                bind.dispose()

        rollback_dir = restore_business_backup(
            settings,
            archive,
            data_dir,
            before_install=_close_main_db_before_install,
        )
    except BusinessBackupError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=public_error_message(error, "恢复未完成，当前数据没有改变。请重启知意后重试。"),
        ) from error
    return RestoreResult(
        rollback_dir=str(rollback_dir),
        restart_required=True,
        scheduled=False,
    )
