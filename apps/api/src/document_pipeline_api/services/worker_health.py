import json
import os
from pathlib import Path
import threading
import time
from typing import TypedDict


class WorkerHealth(TypedDict):
    online: bool
    last_seen_seconds_ago: float | None


_heartbeat_stop = threading.Event()
_heartbeat_thread: threading.Thread | None = None
_heartbeat_lock = threading.Lock()


def heartbeat_path() -> Path:
    return Path(
        os.getenv(
            "DOCUMENT_PIPELINE_WORKER_HEARTBEAT",
            "data/worker-heartbeat.json",
        )
    )


def start_worker_heartbeat(interval_seconds: float = 2) -> None:
    global _heartbeat_thread
    with _heartbeat_lock:
        if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
            return
        _heartbeat_stop.clear()
        _heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(interval_seconds,),
            daemon=True,
            name="document-pipeline-worker-heartbeat",
        )
        _heartbeat_thread.start()


def stop_worker_heartbeat() -> None:
    _heartbeat_stop.set()
    thread = _heartbeat_thread
    if thread is not None:
        thread.join(timeout=3)
    heartbeat_path().unlink(missing_ok=True)


def read_worker_health(
    path: Path | None = None,
    *,
    stale_after_seconds: float = 8,
) -> WorkerHealth:
    target = path or heartbeat_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        age = max(0.0, time.time() - float(payload["timestamp"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"online": False, "last_seen_seconds_ago": None}
    return {
        "online": age <= stale_after_seconds,
        "last_seen_seconds_ago": round(age, 1),
    }


def _heartbeat_loop(interval_seconds: float) -> None:
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    while not _heartbeat_stop.is_set():
        _write_heartbeat(
            path,
            {"pid": os.getpid(), "timestamp": time.time()},
        )
        _heartbeat_stop.wait(interval_seconds)


def _write_heartbeat(path: Path, payload: dict[str, float | int]) -> bool:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True),
            encoding="utf-8",
        )
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                return True
            except PermissionError:
                if attempt == 2:
                    return False
                time.sleep(0.05 * (attempt + 1))
    except OSError:
        return False
    finally:
        temporary.unlink(missing_ok=True)
    return False
