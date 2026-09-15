"""Publish a complete original copy without taking ownership of external files.

The caller persists its destination/attempt before invoking this operation. This
module never replaces a destination, removes a published file, or changes source
bytes. It only cleans up the temporary file created by this invocation.
"""

from dataclasses import dataclass
from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import re
import tempfile


class CopyExportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def validate_copy_name(name: str) -> str:
    """Validate a single Windows filename, even when tests run on another OS."""
    if (
        not name or name != name.strip() or name.endswith(".")
        or _INVALID_NAME.search(name) or _RESERVED.match(name)
        or name in {".", ".."} or len(name.encode("utf-16-le")) // 2 > 180
    ):
        raise CopyExportError("invalid_name", "名称含非法字符、保留名称或过长；请修改名称，不要输入路径。")
    return name


def suggest_safe_stem(value: str, *, fallback: str = "未命名文件") -> str:
    """A deterministic suggestion only; caller must obtain naming confirmation."""
    clean = _INVALID_NAME.sub("_", value).strip().rstrip(".")
    if not clean or clean in {".", ".."}:
        clean = fallback
    if _RESERVED.match(clean):
        clean = "_" + clean
    # UTF-16 units, not Python character count (emoji consume two units).
    clean = clean.encode("utf-16-le")[:240].decode("utf-16-le", errors="ignore").rstrip(". ")
    return validate_copy_name(clean)


@dataclass(frozen=True)
class PublishedCopy:
    path: Path
    size_bytes: int
    sha256: str


def publish_original_copy(
    source: Path,
    destination_dir: Path,
    filename: str,
    *,
    expected_sha256: str,
    expected_size: int,
    before_publish: Callable[[Path, tuple[int, int]], None] | None = None,
    on_staged: Callable[[Path, tuple[int, int]], None] | None = None,
) -> PublishedCopy:
    """Stage, verify and atomically publish, with exclusive destination creation.

On Windows rename fails if the destination exists. POSIX uses a hard link for
the same no-replace guarantee; unsupported filesystems fail explicitly rather
than falling back to a potentially destructive rename. No model is involved.
"""
    validate_copy_name(filename)
    if not destination_dir.is_absolute():
        raise CopyExportError("invalid_destination", "请选择一个绝对路径的目标文件夹。")
    try:
        directory = destination_dir.resolve(strict=True)
        if not directory.is_dir():
            raise FileNotFoundError
    except OSError as error:
        raise CopyExportError("destination_unavailable", "目标文件夹不存在或不可访问，请选择新路径。") from error
    target = directory / filename
    if target.exists() or target.is_symlink():
        raise CopyExportError("name_conflict", "目标中已有同名文件或文件夹；请改名或选择新路径，不会覆盖。")
    temporary: Path | None = None
    identity: tuple[int, int] | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".zhiyi-copy-", suffix=".tmp", dir=directory)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "wb") as output:
            stat = os.fstat(output.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if on_staged is not None:
                on_staged(temporary, identity)
            with source.open("rb") as original:
                while chunk := original.read(1024 * 1024):
                    size += len(chunk)
                    if size > expected_size:
                        raise CopyExportError("original_changed", "内部原件与保存记录不一致，已停止导出。")
                    digest.update(chunk)
                    output.write(chunk)
            if size != expected_size or digest.hexdigest() != expected_sha256:
                raise CopyExportError("original_changed", "内部原件与保存记录不一致，已停止导出。")
            output.flush()
            os.fsync(output.fileno())
        if before_publish is not None:
            before_publish(target, identity)
        if os.name == "nt":
            os.rename(temporary, target)
        else:
            os.link(temporary, target)
        return PublishedCopy(target, size, digest.hexdigest())
    except FileExistsError as error:
        raise CopyExportError("name_conflict", "目标中已有同名文件；请改名或选择新路径，不会覆盖。") from error
    except PermissionError as error:
        raise CopyExportError("permission_denied", "没有写入权限或文件被占用，请选择新路径或稍后重试导出。") from error
    except OSError as error:
        raise CopyExportError("copy_failed", "副本未能导出，请检查目标位置和剩余空间后重试导出。") from error
    finally:
        if temporary is not None and identity is not None:
            try:
                stat = temporary.lstat()
                if (stat.st_dev, stat.st_ino) == identity:
                    temporary.unlink()
            except OSError:
                # Never turn a successfully published copy into a retry that
                # produces another file, or delete a replacement external file.
                pass
