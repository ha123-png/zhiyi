"""Publish a complete new file without ever replacing an existing destination."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import time
import json


def replace_file(source: Path, target: Path) -> None:
    """A brief Windows reader may deny delete-sharing; do not lose state writes."""
    for attempt in range(10):
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if os.name != "nt" or error.winerror not in (5, 32, 33) or attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def new_text_file(path: Path, *, encoding: str = "utf-8", newline: str | None = None):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, newline=newline,
                dir=path.parent, prefix=".zhiyi-export-", suffix=".partial", delete=False) as stream:
            temporary = Path(stream.name)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            # Windows rename fails if the destination exists (unlike replace).
            temporary.rename(path)
        else:
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
