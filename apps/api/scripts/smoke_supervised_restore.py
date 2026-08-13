"""Windows 正式监督器恢复冒烟：停 API/Worker、恢复、重启且不丢回滚边界。"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _json_request(url: str, *, method: str = "GET") -> object:
    request = Request(url, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def _wait_ready(base_url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/api/v1/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise RuntimeError("监督器 API 未在预算内就绪。")


def _wait_supervisor_state(data_dir: Path, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    state_path = data_dir / "runtime" / "supervisor.json"
    while time.monotonic() < deadline:
        if state_path.is_file():
            return
        time.sleep(0.1)
    raise RuntimeError("监督器状态未在预算内就绪。")


def main() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(source_root))

    from fastapi.testclient import TestClient

    from document_pipeline_api.config import Settings
    from document_pipeline_api.main import create_app
    from document_pipeline_api.models import TaskRecord
    from document_pipeline_api.supervisor import request_stop

    with tempfile.TemporaryDirectory(prefix="supervised-restore-smoke-") as temp:
        data_dir = Path(temp).resolve()
        settings = Settings(
            database_url=f"sqlite:///{data_dir / 'document-pipeline.db'}",
            storage_dir=data_dir / "uploads",
            queue_enabled=False,
        )
        with TestClient(create_app(settings)) as client:
            first = client.post(
                "/api/v1/tasks",
                files={"file": ("before.txt", b"before", "text/plain")},
            ).json()["id"]
            with client.app.state.session_factory() as session:
                session.get(TaskRecord, first).status = "completed"
                session.commit()
            backup_name = client.post("/api/v1/backups").json()["name"]
            second = client.post(
                "/api/v1/tasks",
                files={"file": ("after.txt", b"after", "text/plain")},
            ).json()["id"]
            with client.app.state.session_factory() as session:
                session.get(TaskRecord, second).status = "completed"
                session.commit()
            seed_engine = client.app.state.session_factory.kw["bind"]
        seed_engine.dispose()

        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        supervisor = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "document_pipeline_api.launcher",
                "start",
                "--data-dir",
                str(data_dir),
                "--port",
                str(port),
                "--no-browser",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        try:
            _wait_ready(base_url)
            _wait_supervisor_state(data_dir)
            before = _json_request(f"{base_url}/api/v1/tasks?limit=10")
            if len(before) != 2:
                raise RuntimeError("恢复前隔离数据不是预期的两个任务。")
            result = _json_request(
                f"{base_url}/api/v1/backups/{backup_name}/restore",
                method="POST",
            )
            if not isinstance(result, dict) or result.get("scheduled") is not True:
                raise RuntimeError("正式恢复没有交给监督器编排。")

            # 必须观察到至少一次离线，避免把“API 从未停止”误判为安全恢复。
            saw_offline = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    with urlopen(f"{base_url}/api/v1/health", timeout=0.5):
                        if saw_offline:
                            break
                except (OSError, URLError):
                    saw_offline = True
                time.sleep(0.2)
            else:
                raise RuntimeError("恢复后 API 未在预算内重新上线。")
            if not saw_offline:
                raise RuntimeError("恢复期间没有观察到 API 安全停机。")

            after = _json_request(f"{base_url}/api/v1/tasks?limit=10")
            if [task["id"] for task in after] != [first]:
                raise RuntimeError("恢复后任务集合与备份时间点不一致。")
            status = _json_request(f"{base_url}/api/v1/backups/status")
            if not isinstance(status, dict) or status.get("restore_state") != "succeeded":
                raise RuntimeError("监督器没有记录成功恢复结果。")
            connection = sqlite3.connect(data_dir / "document-pipeline.db")
            try:
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("恢复后数据库完整性检查失败。")
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("恢复后数据库存在外键违规。")
            finally:
                connection.close()
        finally:
            stopped = request_stop(data_dir)
            try:
                supervisor.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if stopped:
                    raise RuntimeError("监督器已确认停止但主进程未退出。")
                supervisor.kill()
                supervisor.wait(timeout=5)
                raise RuntimeError("监督器未能安全停止。")
            finally:
                # Windows Job 关闭后给子进程日志/SQLite 句柄一个有限释放窗口。
                time.sleep(0.5)

    print("监督器恢复冒烟通过：API/Worker 停机、恢复、重启、数据与完整性均正确。")


if __name__ == "__main__":
    main()
