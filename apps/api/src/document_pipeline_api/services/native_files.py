"""Native-picker capabilities and Windows handle-based original-file access.

HTTP callers never supply a source path. Only a native file dialog issues an
opaque, expiring, single-use import ticket. Paths remain private task metadata.
"""
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import stat
import time

from document_pipeline_api.services.file_copies import CopyExportError


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    return kernel


def validate_local_path(path: Path) -> None:
    # Do not resolve first: that would hide a junction/symlink in the input.
    if os.name != "nt" or not path.is_absolute() or ".." in path.parts or str(path).startswith("\\\\"):
        raise CopyExportError("unsupported_location", "原文件归档仅支持 Windows 本机固定磁盘上的普通文件夹。")
    if _kernel().GetDriveTypeW(path.anchor) != 3:
        raise CopyExportError("unsupported_location", "请选择本机固定磁盘，网络、移动磁盘和云盘占位文件暂不支持归档。")
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            if part == path:
                continue
            raise
        if info.st_file_attributes & (0x400 | 0x1000 | 0x40000 | 0x400000):
            raise CopyExportError("unsupported_location", "该位置包含链接、联接点或云盘占位文件，请使用本机普通文件夹。")


@contextmanager
def guarded_directories(*paths: Path):
    """Deny renaming/replacing every ancestor while a file action is underway."""
    kernel = _kernel()
    with ExitStack() as stack:
        seen = set()
        for path in paths:
            validate_local_path(path)
            for directory in [*reversed(path.parents), path]:
                key = str(directory).casefold()
                if key in seen:
                    continue
                seen.add(key)
                handle = kernel.CreateFileW(str(directory), 0x80, 0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                stack.callback(kernel.CloseHandle, handle)
                # Check again after obtaining the handle, before using children.
                validate_local_path(directory)
        yield


@contextmanager
def locked_original(path: Path, *, delete: bool = False):
    """Hold the exact file, deny writers/deleters; never unlink a path by name."""
    import msvcrt
    validate_local_path(path)
    with guarded_directories(path.parent):
        kernel = _kernel()
        handle = kernel.CreateFileW(str(path), 0x80000000 | (0x10000 if delete else 0),
                                    0x1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        with os.fdopen(descriptor, "rb") as source:
            validate_local_path(path)
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise CopyExportError("unsupported_source", "归档来源必须是普通文件。")
            yield source


def remove_locked_original(source) -> None:
    """Delete the verified handle on close, without a path-replacement race."""
    import msvcrt
    disposition = ctypes.c_ubyte(1)
    if not _kernel().SetFileInformationByHandle(msvcrt.get_osfhandle(source.fileno()), 4,
                                               ctypes.byref(disposition), ctypes.sizeof(disposition)):
        raise ctypes.WinError(ctypes.get_last_error())


def fingerprint(source, path: Path) -> dict:
    info = os.fstat(source.fileno())
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
            "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def matches_source(source, value: dict) -> bool:
    info = os.fstat(source.fileno())
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) == (
        value.get("device"), value.get("inode"), value.get("size"), value.get("mtime_ns"))


@contextmanager
def import_source(path: Path):
    # Network/cloud files can still be read for extraction. Archive performs
    # its own stricter local-path check before any destructive action.
    try:
        validate_local_path(path)
        supported = True
    except CopyExportError:
        supported = False
    with (locked_original(path) if supported else path.open("rb")) as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise CopyExportError("unsupported_source", "请选择普通文件。")
        yield source


def issue_import_ticket(data_dir: Path, path: Path) -> dict:
    # Called by the native dialog bridge only, never registered as an API route.
    with import_source(path) as source:
        value = fingerprint(source, path)
    value["expires"] = time.time() + 3600
    directory = data_dir / "native-imports"
    directory.mkdir(exist_ok=True)
    validate_local_path(directory)
    for old in directory.iterdir():
        if re.fullmatch(r"[a-f0-9]{64}\.(json|claimed)", old.name) and not old.is_symlink():
            try:
                if old.stat().st_mtime < time.time() - 86400:
                    old.unlink()
            except OSError:
                pass
    token = secrets.token_hex(32)
    with (directory / f"{token}.json").open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".md":
        content_type = "text/markdown"
    return {"token": token, "name": path.name, "size": value["size"], "type": content_type}


@contextmanager
def consume_import_ticket(data_dir: Path, token: str):
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise CopyExportError("invalid_ticket", "文件选择已失效，请重新选择文件。")
    directory = data_dir / "native-imports"
    path = directory / f"{token}.json"
    claimed = path.with_suffix(".claimed")
    try:
        validate_local_path(path)
        path.rename(claimed)  # Windows no-replace; one consumer owns the ticket.
        value = json.loads(claimed.read_text(encoding="utf-8"))
        if value["expires"] < time.time():
            raise ValueError
    except (OSError, ValueError, KeyError):
        raise CopyExportError("invalid_ticket", "文件选择已失效或已导入，请重新选择文件。") from None
    try:
        with import_source(Path(value["path"])) as source:
            if not matches_source(source, value):
                raise CopyExportError("source_changed", "文件在选择后已发生变化，请重新选择。")
            yield source, value
    finally:
        claimed.unlink(missing_ok=True)
