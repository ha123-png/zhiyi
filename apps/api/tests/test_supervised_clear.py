"""Real Windows child-process lifecycle; all data is synthetic and isolated."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.supervisor import request_stop
from document_pipeline_api.windows_job import ProcessExitWait
from image_test_data import PNG_BYTES


@pytest.mark.skipif(os.name != "nt", reason="Windows Job lifecycle")
@pytest.mark.parametrize("hold_original", [False, True])
def test_supervisor_stops_children_clears_and_restarts_with_external_files_preserved(tmp_path, hold_original):
    root = tmp_path / "isolated-data"
    settings = Settings(database_url=f"sqlite:///{root / 'document-pipeline.db'}", storage_dir=root / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as client:
        task = client.post("/api/v1/tasks", files={"file": ("synthetic.png", PNG_BYTES, "image/png")}).json()
        with client.app.state.session_factory() as session:
            session.get(TaskRecord, task["id"]).status = "paused"
            session.commit()
    retained = [tmp_path / "external" / "copy.png", root / "backups" / "user.dpbak", root / "models" / "model.gguf"]
    for path in retained:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic-user-owned-file")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("DOCUMENT_PIPELINE_")}
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    environment["DOCUMENT_PIPELINE_MODEL_BASE_URL"] = "http://127.0.0.1:1/v1"
    log_path = tmp_path / "supervisor.log"
    original_handle = next((root / "uploads").rglob("*.png")).open("rb") if hold_original else None
    child_handles = []
    with log_path.open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "document_pipeline_api.launcher", "start", "--data-dir", str(root), "--port", str(port), "--no-browser"], env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                def wait_ready(previous_pids=None):
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        assert process.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
                        try:
                            state = json.loads((root / "runtime" / "supervisor.json").read_text())
                            health = client.get("/api/v1/health")
                            if health.status_code == 200 and (previous_pids is None or state["child_pids"] != previous_pids):
                                return state
                        except (OSError, ValueError, httpx.HTTPError):
                            pass
                        time.sleep(0.1)
                    pytest.fail("Isolated supervisor did not become ready")
                before = wait_ready()
                child_handles.extend(ProcessExitWait(pid) for pid in before["child_pids"])
                response = client.post("/api/v1/system/admin/clear-data", json={"confirm_text": "清除全部本地数据"})
                assert response.status_code == 200, response.text
                assert response.json()["scheduled"] is True
                after = wait_ready(before["child_pids"])
                assert after["pid"] == before["pid"]
                assert all(handle.wait(0) for handle in child_handles)
                state = client.get("/api/v1/system/admin/clear-data").json()
                if hold_original:
                    assert state["incomplete"] is True
                    assert state["result"]["state"] == "failed"
                    assert client.post("/api/v1/tasks", files={"file": ("blocked.png", PNG_BYTES, "image/png")}).status_code == 503
                    original_handle.close()
                    child_handles.extend(ProcessExitWait(pid) for pid in after["child_pids"])
                    retry = client.post("/api/v1/system/admin/clear-data", json={"confirm_text": "清除全部本地数据"})
                    assert retry.status_code == 200, retry.text
                    wait_ready(after["child_pids"])
                    assert all(handle.wait(0) for handle in child_handles)
                    state = client.get("/api/v1/system/admin/clear-data").json()
                assert state["incomplete"] is False, log_path.read_text(encoding="utf-8", errors="replace")
                assert state["result"]["state"] == "succeeded"
                assert client.get("/api/v1/tasks").json() == []
                assert not list((root / "uploads").rglob("*.png"))
                for path in retained:
                    assert path.read_bytes() == b"synthetic-user-owned-file"
        finally:
            for handle in child_handles:
                handle.close()
            if original_handle:
                original_handle.close()
            if process.poll() is None:
                request_stop(root)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
