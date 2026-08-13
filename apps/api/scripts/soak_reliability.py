import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import httpx
from PIL import Image, PngImagePlugin


ACTIVE_STATUSES = {"queued", "processing", "validating", "paused"}


class FakeModelState:
    def __init__(self) -> None:
        self.available = True
        self.responses = 0
        self.lock = threading.Lock()


class FakeModelServer(ThreadingHTTPServer):
    state: FakeModelState


class FakeModelHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self.send_error(404)
            return
        if not self.server.state.available:
            self.send_error(503)
            return
        self._json(200, {"data": [{"id": "soak-model"}]})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 1 <= length <= 20 * 1024 * 1024:
            self.send_error(400)
            return
        self.rfile.read(length)
        if not self.server.state.available:
            self._json(503, {"error": {"message": "planned soak outage"}})
            return
        with self.server.state.lock:
            self.server.state.responses += 1
            sequence = self.server.state.responses
        result = {
            "document_type": "发票",
            "seller_name": "长稳销售方",
            "buyer_name": "长稳购买方",
            "document_number": f"SOAK-{sequence:06d}",
            "document_date": "2026-08-10",
            "amount_before_tax": 100,
            "tax_amount": 6,
            "total_amount": 106,
            "items": [
                {
                    "name": "长稳明细",
                    "specification": None,
                    "unit": "项",
                    "quantity": 1,
                    "unit_price": 100,
                    "amount": 100,
                    "tax_rate": "6%",
                    "tax_amount": 6,
                }
            ],
        }
        self._json(
            200,
            {"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]},
        )

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _sample_png(sequence: int) -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("soak-sequence", str(sequence))
    Image.new("RGB", (32, 32), color=(sequence % 255, 80, 120)).save(
        output,
        format="PNG",
        pnginfo=metadata,
    )
    return output.getvalue()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    # Windows scanners, editors and live report readers can briefly open the target
    # without delete sharing. A telemetry checkpoint must never abort the reliability
    # exercise it is observing, so retry atomic replacement and retain a companion
    # snapshot if the target remains busy. A later checkpoint will promote normally.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary.write_text(body, encoding="utf-8")
    deadline = time.monotonic() + 10
    while True:
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                fallback = path.with_name(f"{path.stem}.latest{path.suffix}")
                fallback.write_text(body, encoding="utf-8")
                temporary.unlink(missing_ok=True)
                return
            time.sleep(0.1)


class SoakRun:
    def __init__(self, args: argparse.Namespace, root: Path) -> None:
        self.args = args
        self.root = root
        self.data_dir = args.data_dir.resolve()
        self.report_path = args.report.resolve()
        self.api_url = f"http://127.0.0.1:{args.port}/api/v1"
        self.model = FakeModelState()
        self.model_server = FakeModelServer(
            ("127.0.0.1", args.model_port), FakeModelHandler
        )
        self.model_server.state = self.model
        self.model_thread = threading.Thread(
            target=self.model_server.serve_forever,
            daemon=True,
        )
        self.supervisor: subprocess.Popen[bytes] | None = None
        self.client = httpx.Client(timeout=10, trust_env=False)
        self.pending: dict[str, str] = {}
        self.report: dict[str, object] = {
            "schema_version": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "requested_duration_seconds": args.duration_seconds,
            "passed": False,
            "submitted": 0,
            "completed": 0,
            "failure_events": 0,
            "retried": 0,
            "backups": 0,
            "supervisor_restarts": 0,
            "model_outages": 0,
            "health_samples": 0,
            "errors": [],
        }

    def start_supervisor(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(self.root / "apps" / "api" / "src"),
                "DOCUMENT_PIPELINE_MODEL_PROVIDER": "openai_compatible",
                "DOCUMENT_PIPELINE_MODEL_BASE_URL": f"http://127.0.0.1:{self.args.model_port}/v1",
                "DOCUMENT_PIPELINE_MODEL_NAME": "soak-model",
                "DOCUMENT_PIPELINE_MODEL_TIMEOUT_SECONDS": "5",
            }
        )
        self.supervisor = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "document_pipeline_api.launcher",
                "start",
                "--data-dir",
                str(self.data_dir),
                "--port",
                str(self.args.port),
                "--no-browser",
            ],
            cwd=self.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 45
        state_path = self.data_dir / "runtime" / "supervisor.json"
        while time.monotonic() < deadline:
            if self.supervisor.poll() is not None:
                raise RuntimeError(f"监督器启动失败：{self.supervisor.returncode}")
            try:
                health = self.client.get(f"{self.api_url}/health")
                if health.status_code == 200 and state_path.is_file():
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.2)
        raise RuntimeError("监督器未在 45 秒内就绪。")

    def stop_supervisor(self) -> None:
        if self.supervisor is None or self.supervisor.poll() is not None:
            return
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "document_pipeline_api.launcher",
                "stop",
                "--data-dir",
                str(self.data_dir),
            ],
            cwd=self.root,
            timeout=30,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(f"监督器安全停止失败：{result.returncode}")
        self.supervisor.wait(timeout=10)

    def upload(self, sequence: int) -> None:
        filename = f"soak-{sequence:06d}.png"
        response = self.client.post(
            f"{self.api_url}/tasks",
            files={"file": (filename, _sample_png(sequence), "image/png")},
            data={"template_mode": "invoice"},
        )
        response.raise_for_status()
        self.pending[response.json()["id"]] = filename
        self.report["submitted"] = int(self.report["submitted"]) + 1

    def poll_tasks(self, *, retry_failed: bool) -> None:
        for task_id, filename in list(self.pending.items()):
            response = self.client.get(
                f"{self.api_url}/tasks",
                params={"search": filename, "limit": 1},
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                raise RuntimeError(f"任务消失：{task_id}")
            status = rows[0]["status"]
            if status == "completed":
                self.pending.pop(task_id)
                self.report["completed"] = int(self.report["completed"]) + 1
            elif status == "failed" and retry_failed:
                retry = self.client.post(f"{self.api_url}/tasks/{task_id}/retry")
                retry.raise_for_status()
                self.report["retried"] = int(self.report["retried"]) + 1
            elif status == "failed":
                self.report["failure_events"] = int(self.report["failure_events"]) + 1

    def create_backup(self) -> None:
        response = self.client.post(f"{self.api_url}/backups")
        response.raise_for_status()
        self.report["backups"] = int(self.report["backups"]) + 1

    def checkpoint(self, started: float) -> None:
        self.report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        self.report["pending"] = len(self.pending)
        self.report["health_samples"] = int(self.report["health_samples"]) + 1
        self.report["model_responses"] = self.model.responses
        _atomic_json(self.report_path, self.report)

    def verify(self) -> None:
        database = self.data_dir / "document-pipeline.db"
        with sqlite3.connect(database) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            active = connection.execute(
                "SELECT count(*) FROM tasks WHERE status IN ('queued','processing','validating','paused')"
            ).fetchone()[0]
            duplicate_facts = connection.execute(
                "SELECT count(*) FROM (SELECT task_id FROM data_rows GROUP BY task_id HAVING count(*) > 1)"
            ).fetchone()[0]
            completed = connection.execute(
                "SELECT count(*) FROM tasks WHERE status = 'completed'"
            ).fetchone()[0]
        self.report["database_integrity"] = integrity
        self.report["foreign_key_violations"] = len(foreign_keys)
        self.report["active_tasks"] = active
        self.report["duplicate_fact_tasks"] = duplicate_facts
        self.report["database_completed_tasks"] = completed
        self.report["passed"] = (
            integrity == "ok"
            and not foreign_keys
            and active == 0
            and duplicate_facts == 0
            and completed == int(self.report["submitted"])
            and not self.pending
        )
        if not self.report["passed"]:
            raise RuntimeError("长稳最终一致性门禁未通过。")

    def run(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_thread.start()
        started = time.monotonic()
        deadline = started + self.args.duration_seconds
        next_task = started
        next_backup = started + self.args.backup_interval
        next_restart = started + self.args.restart_interval
        next_outage = started + self.args.outage_interval
        outage_end: float | None = None
        sequence = 0
        try:
            self.start_supervisor()
            while time.monotonic() < deadline:
                now = time.monotonic()
                if outage_end is not None and now >= outage_end:
                    self.model.available = True
                    outage_end = None
                    self.poll_tasks(retry_failed=True)
                if outage_end is None and now >= next_outage:
                    self.model.available = False
                    outage_end = now + self.args.outage_duration
                    next_outage = now + self.args.outage_interval
                    self.report["model_outages"] = int(self.report["model_outages"]) + 1
                if now >= next_task:
                    sequence += 1
                    self.upload(sequence)
                    next_task = now + self.args.task_interval
                self.poll_tasks(retry_failed=outage_end is None)
                if now >= next_backup:
                    self.create_backup()
                    next_backup = now + self.args.backup_interval
                if now >= next_restart and not self.pending:
                    self.stop_supervisor()
                    self.start_supervisor()
                    self.report["supervisor_restarts"] = int(self.report["supervisor_restarts"]) + 1
                    next_restart = now + self.args.restart_interval
                self.checkpoint(started)
                time.sleep(min(1, self.args.task_interval / 2))

            self.model.available = True
            drain_deadline = time.monotonic() + 120
            while self.pending and time.monotonic() < drain_deadline:
                self.poll_tasks(retry_failed=True)
                self.checkpoint(started)
                time.sleep(0.5)
            self.stop_supervisor()
            self.verify()
        except BaseException as error:
            self.report["errors"].append(f"{type(error).__name__}: {error}")
            raise
        finally:
            try:
                self.stop_supervisor()
            except BaseException as error:
                self.report["errors"].append(f"cleanup: {type(error).__name__}: {error}")
            self.model_server.shutdown()
            self.model_server.server_close()
            self.client.close()
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            _atomic_json(self.report_path, self.report)


def main() -> None:
    parser = argparse.ArgumentParser(description="ISSUE-068 supervised reliability soak")
    parser.add_argument("--duration-seconds", type=float, default=8 * 60 * 60)
    parser.add_argument("--task-interval", type=float, default=30)
    parser.add_argument("--backup-interval", type=float, default=15 * 60)
    parser.add_argument("--restart-interval", type=float, default=60 * 60)
    parser.add_argument("--outage-interval", type=float, default=30 * 60)
    parser.add_argument("--outage-duration", type=float, default=20)
    parser.add_argument("--port", type=int, default=18868)
    parser.add_argument("--model-port", type=int, default=18869)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--detach", action="store_true")
    args = parser.parse_args()
    if min(
        args.duration_seconds,
        args.task_interval,
        args.backup_interval,
        args.restart_interval,
        args.outage_interval,
        args.outage_duration,
    ) <= 0:
        parser.error("all duration and interval values must be positive")
    root = Path(__file__).resolve().parents[3]
    if args.detach:
        if os.name != "nt":
            parser.error("--detach is currently supported only on Windows")
        run_root = args.data_dir.resolve().parent
        run_root.mkdir(parents=True, exist_ok=True)
        child_arguments = [argument for argument in sys.argv[1:] if argument != "--detach"]
        stdout = (run_root / "stdout.log").open("ab")
        stderr = (run_root / "stderr.log").open("ab")
        try:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), *child_arguments],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=(
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                ),
                close_fds=True,
            )
        finally:
            stdout.close()
            stderr.close()
        _atomic_json(
            run_root / "runner.json",
            {
                "pid": process.pid,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "report": str(args.report.resolve()),
            },
        )
        print(f"detached soak pid={process.pid}")
        return
    SoakRun(args, root).run()


if __name__ == "__main__":
    main()
