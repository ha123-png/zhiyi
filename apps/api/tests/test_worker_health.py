import json
import os
from pathlib import Path
import time

from document_pipeline_api.services import worker_health
from document_pipeline_api.services.worker_health import read_worker_health


def test_worker_health_reports_fresh_heartbeat_online(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text(
        json.dumps({"pid": 1, "timestamp": time.time()}),
        encoding="utf-8",
    )

    status = read_worker_health(heartbeat)

    assert status["online"] is True
    assert status["last_seen_seconds_ago"] is not None


def test_worker_health_reports_stale_or_missing_heartbeat_offline(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text(
        json.dumps({"pid": 1, "timestamp": time.time() - 30}),
        encoding="utf-8",
    )

    assert read_worker_health(heartbeat)["online"] is False
    assert read_worker_health(tmp_path / "missing.json") == {
        "online": False,
        "last_seen_seconds_ago": None,
    }


def test_worker_heartbeat_retries_transient_windows_replace_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    native_replace = os.replace
    attempts = 0

    def transient_replace(source, target) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("temporarily locked")
        native_replace(source, target)

    monkeypatch.setattr(worker_health.os, "replace", transient_replace)

    assert worker_health._write_heartbeat(
        heartbeat,
        {"pid": 7, "timestamp": time.time()},
    )
    assert attempts == 2
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["pid"] == 7
