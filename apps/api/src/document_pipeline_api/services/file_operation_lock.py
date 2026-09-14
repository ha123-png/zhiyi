"""OS-released lock coordinating original deletion and external publication."""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path

from fastapi import HTTPException


@contextmanager
def file_operation_lock(storage_dir: Path, *, lock_name: str = ".file-actions.lock"):
    storage_dir.mkdir(parents=True, exist_ok=True)
    # Keep the lock outside uploads so backup restoration can swap that
    # directory while holding the same lock (Windows forbids moving open files).
    path = storage_dir.parent / lock_name
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise HTTPException(409, "文件操作锁路径异常，请检查知意数据目录。")
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise HTTPException(409, "正在保存或导出文件，请稍后重试此操作。") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def guard_file_operation(function):
    @wraps(function)
    def guarded(session, settings, *args, **kwargs):
        with file_operation_lock(settings.storage_dir):
            return function(session, settings, *args, **kwargs)
    return guarded
