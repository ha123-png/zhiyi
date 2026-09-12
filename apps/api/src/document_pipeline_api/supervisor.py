from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import msvcrt
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import webbrowser

from document_pipeline_api.runtime_paths import default_data_dir
from document_pipeline_api.windows_job import KillOnCloseJob, ProcessExitWait


class SingleInstance:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.file = None

    def __enter__(self) -> "SingleInstance":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            self.file.close()
            self.file = None
            raise RuntimeError("知意已经在运行。") from error
        return self

    def __exit__(self, *_args: object) -> None:
        if self.file is not None:
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


_CLEAR_DATA_SENTINEL = "__clear_all_local_data__"


class _ControlServer(ThreadingHTTPServer):
    token: str
    stop_event: threading.Event
    restore_lock: threading.Lock
    restore_name: str | None


class _ControlHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            self.send_error(404)
            return
        if self.path == "/stop":
            self.send_response(204)
            self.end_headers()
            self.server.stop_event.set()
            return
        if self.path not in {"/restore", "/clear-data"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not (1 <= length <= 512):
                raise ValueError
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            name = payload["name"]
            if not isinstance(name, str) or len(name) > 255:
                raise ValueError
            if self.path == "/clear-data" and name != _CLEAR_DATA_SENTINEL:
                raise ValueError
            if self.path == "/restore" and name == _CLEAR_DATA_SENTINEL:
                raise ValueError
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400)
            return
        with self.server.restore_lock:
            if self.server.restore_name is not None:
                self.send_error(409)
                return
            self.server.restore_name = name
        self.send_response(202)
        self.end_headers()
        # 先让 API 把“已安排维护”响应送到浏览器，再停止子进程。
        threading.Timer(0.5, self.server.stop_event.set).start()

    def log_message(self, *_args: object) -> None:
        return


def _child_command(mode: str, data_dir: Path, app_port: int) -> list[str]:
    common = [mode, "--data-dir", str(data_dir)]
    if mode == "api":
        common.extend(["--host", "127.0.0.1", "--port", str(app_port)])
    if getattr(sys, "frozen", False):
        return [sys.executable, *common]
    return [sys.executable, "-m", "document_pipeline_api.launcher", *common]


# 端口自动避让：默认 8765，被占用时顺延并写入 runtime/app-config.json，
# 之后保持稳定。前端页面由 API 同源托管，端口变化对用户无感。
_PORT_CONFIG_FILE = "app-config.json"
_DEFAULT_PORT = 8765
_PORT_SCAN_LIMIT = 50


def _port_in_use(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        return True
    return False


def _read_saved_port(config_path: Path) -> int | None:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        port = int(payload["port"])
        if 1 <= port <= 65535:
            return port
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        pass
    return None


def _save_port(config_path: Path, port: int) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"port": port}, ensure_ascii=False),
        encoding="utf-8",
    )


def resolve_app_port(data_dir: Path, explicit_port: int | None) -> int:
    """解析应用端口：显式指定 > 上次保存的端口 > 默认 8765。

    - 显式指定的端口被占用时报错（尊重用户意图，不擅自更换）；
    - 未显式指定时，端口被占用会自动顺延找下一个空闲端口，
      并把选定的端口写入配置，之后启动保持稳定。
    """
    config_path = data_dir / "runtime" / _PORT_CONFIG_FILE
    base = explicit_port if explicit_port is not None else _read_saved_port(config_path)
    if base is None:
        base = _DEFAULT_PORT
    if explicit_port is not None and _port_in_use(base):
        raise RuntimeError(
            f"端口 {base} 已被占用，请先关闭占用该端口的程序，"
            f"或使用 --port 指定其他空闲端口。"
        )
    port = base
    if _port_in_use(port):
        for candidate in range(port + 1, port + _PORT_SCAN_LIMIT):
            if not _port_in_use(candidate):
                port = candidate
                break
        else:
            raise RuntimeError(
                f"端口 {base} 起连续 {_PORT_SCAN_LIMIT} 个端口均被占用，无法启动。"
            )
    if explicit_port is None and port != _read_saved_port(config_path):
        _save_port(config_path, port)
    return port


def _write_state(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def _wait_for_health(port: int, processes: list[subprocess.Popen[bytes]]) -> None:
    deadline = time.monotonic() + 30
    url = f"http://127.0.0.1:{port}/api/v1/health"
    while time.monotonic() < deadline:
        failed = next((process for process in processes if process.poll() is not None), None)
        if failed is not None:
            raise RuntimeError(f"程序组件启动失败，退出代码：{failed.returncode}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.25)
    raise RuntimeError("程序启动超过 30 秒，请检查运行日志。")


def _wait_for_worker_heartbeat(
    heartbeat_path: Path,
    worker: subprocess.Popen[bytes],
) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise RuntimeError(f"任务处理器启动失败，退出代码：{worker.returncode}")
        if heartbeat_path.is_file():
            return
        time.sleep(0.1)
    raise RuntimeError("任务处理器启动超过 15 秒，请检查运行日志。")


def run_supervisor(
    data_dir: Path | None = None,
    explicit_port: int | None = None,
    *,
    open_browser: bool = True,
    stop_event: threading.Event | None = None,
) -> None:
    active_data_dir = (data_dir or default_data_dir()).resolve()
    app_port = resolve_app_port(active_data_dir, explicit_port)
    runtime_dir = active_data_dir / "runtime"
    logs_dir = active_data_dir / "logs"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    state_path = runtime_dir / "supervisor.json"
    active_stop_event = stop_event or threading.Event()
    token = secrets.token_urlsafe(32)

    with SingleInstance(runtime_dir / "instance.lock"), ExitStack() as stack:
        control = _ControlServer(("127.0.0.1", 0), _ControlHandler)
        control.token = token
        control.stop_event = active_stop_event
        control.restore_lock = threading.Lock()
        control.restore_name = None
        control_thread = threading.Thread(target=control.serve_forever, daemon=True)
        control_thread.start()
        stack.callback(control.server_close)
        stack.callback(control.shutdown)

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        stack.callback(state_path.unlink, missing_ok=True)
        first_start = True

        while True:
            active_stop_event.clear()
            # Maintenance can remove logs after the child handles close.
            # Recreate the owned directory for every launch, including restart.
            logs_dir.mkdir(parents=True, exist_ok=True)
            processes: list[subprocess.Popen[bytes]] = []
            with ExitStack() as child_stack:
                job = child_stack.enter_context(KillOnCloseJob())

                def start_child(mode: str) -> subprocess.Popen[bytes]:
                    log = child_stack.enter_context(
                        (logs_dir / f"{mode}.log").open("ab")
                    )
                    child_env = os.environ.copy()
                    child_env["DOCUMENT_PIPELINE_SUPERVISED"] = "1"
                    process = subprocess.Popen(
                        _child_command(mode, active_data_dir, app_port),
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=creation_flags,
                        env=child_env,
                    )
                    if process.pid <= 4:
                        process.kill()
                        raise RuntimeError("拒绝管理不安全的子进程 PID。")
                    job.assign(process._handle)
                    processes.append(process)
                    return process

                api_process = start_child("api")
                _wait_for_health(app_port, [api_process])
                heartbeat_path = runtime_dir / "worker-heartbeat.json"
                heartbeat_path.unlink(missing_ok=True)
                worker_process = start_child("worker")
                _wait_for_worker_heartbeat(heartbeat_path, worker_process)

                _write_state(
                    state_path,
                    {
                        "pid": os.getpid(),
                        "token": token,
                        "control_port": control.server_port,
                        "app_port": app_port,
                        "child_pids": [process.pid for process in processes],
                    },
                )
                if first_start and open_browser:
                    webbrowser.open(f"http://127.0.0.1:{app_port}/")
                first_start = False

                while not active_stop_event.wait(0.5):
                    failed = next(
                        (process for process in processes if process.poll() is not None),
                        None,
                    )
                    if failed is not None:
                        raise RuntimeError(
                            f"程序组件意外退出，退出代码：{failed.returncode}"
                        )

            # 关闭 Job 后其明确子进程树会被终止；等句柄真正释放后才替换 SQLite。
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError("程序组件停止超时，已拒绝恢复数据。") from error

            with control.restore_lock:
                restore_name = control.restore_name
                control.restore_name = None
            if restore_name is None:
                break
            if restore_name == _CLEAR_DATA_SENTINEL:
                _perform_supervised_clear_data(active_data_dir)
            else:
                _perform_supervised_restore(active_data_dir, restore_name)


def _perform_supervised_clear_data(data_dir: Path) -> None:
    from document_pipeline_api.config import Settings
    from document_pipeline_api.services.clear_data import clear_local_data
    try:
        result = clear_local_data(Settings(database_url=f"sqlite:///{data_dir / 'document-pipeline.db'}", storage_dir=data_dir / "uploads"))
        result["message"] = "全部本地数据已清除，正在重新启动应用。外部副本、备份和模型文件保留。"
    except Exception:
        result = {"state": "failed", "message": "清除未完成，部分数据可能已清理。请重试清除；未清理外部副本、备份或模型文件。"}
    result["completed_at"] = time.time()
    _write_state(data_dir / "runtime" / "clear-data-result.json", result)


def request_clear_data(data_dir: Path) -> bool:
    return request_restore(data_dir, _CLEAR_DATA_SENTINEL, _control_path="/clear-data")


def _perform_supervised_restore(data_dir: Path, name: str) -> None:
    """在 API/Worker 已退出后恢复；无论成功失败，监督器随后都会重启子进程。"""
    from document_pipeline_api.business_backup import (
        BusinessBackupError,
        restore_business_backup,
    )
    from document_pipeline_api.config import Settings

    result_path = data_dir / "runtime" / "restore-result.json"
    try:
        if Path(name).name != name or not name.endswith(".dpbak"):
            raise BusinessBackupError("恢复请求包含无效备份名称。")
        archive = (data_dir / "backups" / name).resolve()
        if archive.parent != (data_dir / "backups").resolve() or not archive.is_file():
            raise BusinessBackupError("恢复请求指定的备份不存在。")
        rollback = restore_business_backup(
            Settings(
                database_url=f"sqlite:///{data_dir / 'document-pipeline.db'}",
                storage_dir=data_dir / "uploads",
            ),
            archive,
            data_dir,
        )
        payload = {
            "state": "succeeded",
            "message": "备份恢复成功，应用已重新启动。",
            "rollback_dir": str(rollback),
            "completed_at": time.time(),
        }
    except (BusinessBackupError, OSError) as error:
        payload = {
            "state": "failed",
            "message": f"恢复未完成，原有数据保持不变：{error}",
            "rollback_dir": None,
            "completed_at": time.time(),
        }
    _write_state(result_path, payload)


def _wait_for_instance_release(lock_path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with SingleInstance(lock_path):
                return True
        except RuntimeError:
            time.sleep(0.05)
    return False


def request_restore(data_dir: Path, name: str, *, _control_path: str = "/restore") -> bool:
    """请求正式监督器安全停子进程、恢复并重启；不直接操作任何 PID。"""
    active_data_dir = data_dir.resolve()
    state_path = active_data_dir / "runtime" / "supervisor.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pid = int(state["pid"])
        port = int(state["control_port"])
        token = str(state["token"])
        if (
            pid <= 4
            or not (1 <= port <= 65535)
            or len(token) < 32
            or Path(name).name != name
        ):
            return False
        body = json.dumps({"name": name}).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{port}{_control_path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=2) as response:
            return response.status == 202
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, URLError):
        return False


def request_stop(data_dir: Path | None = None) -> bool:
    active_data_dir = (data_dir or default_data_dir()).resolve()
    state_path = active_data_dir / "runtime" / "supervisor.json"
    lock_path = active_data_dir / "runtime" / "instance.lock"
    # API 健康检查会早于 Worker 心跳和 supervisor.json 就绪。安装器可能恰好在
    # 这个窗口覆盖升级，因此只要实例锁仍被持有，就等待监督器完成控制端点发布。
    state: dict[str, object] | None = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            candidate = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                state = candidate
                break
        except (OSError, json.JSONDecodeError):
            pass
        try:
            with SingleInstance(lock_path):
                return False
        except RuntimeError:
            time.sleep(0.05)
    if state is None:
        return False
    try:
        pid = int(state["pid"])
        port = int(state["control_port"])
        token = str(state["token"])
        if pid <= 4 or not (1 <= port <= 65535) or len(token) < 32:
            return False
        # Open the exact authenticated supervisor before asking it to exit. Waiting on this
        # stable handle prevents an installer from racing the final executable file close.
        with ProcessExitWait(pid) as supervisor_process:
            request = Request(
                f"http://127.0.0.1:{port}/stop",
                method="POST",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=2) as response:
                if response.status != 204:
                    return False
            if not _wait_for_instance_release(lock_path):
                return False
            return supervisor_process.wait(5)
    except (OSError, ValueError, KeyError, TypeError, URLError):
        return False
