import os
from pathlib import Path
import sys
import threading
import json

import pytest

from document_pipeline_api.launcher import build_parser, configure_runtime_data, main, run_worker
from document_pipeline_api.supervisor import (
    SingleInstance,
    _ControlHandler,
    _ControlServer,
    _wait_for_instance_release,
    request_restore,
    request_stop,
)


def test_launcher_gives_api_and_worker_the_same_absolute_data_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "user-data"
    monkeypatch.delenv("DOCUMENT_PIPELINE_DATA_DIR", raising=False)
    monkeypatch.delenv("DOCUMENT_PIPELINE_QUEUE_DB", raising=False)
    monkeypatch.delenv("DOCUMENT_PIPELINE_WORKER_HEARTBEAT", raising=False)

    configure_runtime_data(data_dir)

    assert Path(os.environ["DOCUMENT_PIPELINE_DATA_DIR"]) == data_dir.resolve()
    assert Path(os.environ["DOCUMENT_PIPELINE_QUEUE_DB"]) == data_dir.resolve() / "queue.db"
    assert Path(os.environ["DOCUMENT_PIPELINE_WORKER_HEARTBEAT"]) == (
        data_dir.resolve() / "runtime" / "worker-heartbeat.json"
    )


def test_worker_uses_fixed_low_latency_queue_polling(monkeypatch) -> None:
    class Consumer:
        def run(self) -> None:
            calls.append("run")

    calls: list[object] = []

    def create_consumer(**options: object) -> Consumer:
        calls.append(options)
        return Consumer()

    monkeypatch.setattr(
        "document_pipeline_api.worker.huey.create_consumer",
        create_consumer,
    )

    run_worker()

    assert calls == [
        {
            "workers": 1,
            "worker_type": "thread",
            "initial_delay": 0.01,
            "max_delay": 0.01,
            "backoff": 1.0,
        },
        "run",
    ]


def test_no_browser_flag_is_available_for_headless_packaged_smoke() -> None:
    arguments = build_parser().parse_args(["start", "--no-browser"])

    assert arguments.no_browser is True


def test_single_instance_lock_rejects_a_second_supervisor(tmp_path: Path) -> None:
    lock_path = tmp_path / "instance.lock"

    with SingleInstance(lock_path):
        with pytest.raises(RuntimeError, match="已经在运行"):
            with SingleInstance(lock_path):
                pass


def test_stop_waits_until_the_supervisor_releases_its_instance_lock(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "instance.lock"

    with SingleInstance(lock_path):
        assert not _wait_for_instance_release(lock_path, timeout=0.01)

    assert _wait_for_instance_release(lock_path, timeout=0.01)


def test_stop_waits_for_supervisor_state_after_api_startup_race(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "user-data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    server = _ControlServer(("127.0.0.1", 0), _ControlHandler)
    server.token = "x" * 32
    server.stop_event = threading.Event()
    server.restore_lock = threading.Lock()
    server.restore_name = None
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    lock_ready = threading.Event()

    def delayed_supervisor_state() -> None:
        with SingleInstance(runtime_dir / "instance.lock"):
            lock_ready.set()
            server.stop_event.wait(0.1)
            (runtime_dir / "supervisor.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "control_port": server.server_port,
                        "token": server.token,
                    }
                ),
                encoding="utf-8",
            )
            server.stop_event.wait(2)

    supervisor_thread = threading.Thread(target=delayed_supervisor_state)
    supervisor_thread.start()
    assert lock_ready.wait(1)

    class FakeProcessExitWait:
        def __init__(self, _pid: int) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def wait(self, _timeout_seconds: float) -> bool:
            return True

    monkeypatch.setattr(
        "document_pipeline_api.supervisor.ProcessExitWait",
        FakeProcessExitWait,
    )
    try:
        assert request_stop(data_dir)
    finally:
        server.stop_event.set()
        supervisor_thread.join(timeout=2)
        server.shutdown()
        server.server_close()


def test_restore_request_is_authenticated_and_delivered_to_supervisor(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "user-data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    server = _ControlServer(("127.0.0.1", 0), _ControlHandler)
    server.token = "x" * 32
    server.stop_event = threading.Event()
    server.restore_lock = threading.Lock()
    server.restore_name = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (runtime_dir / "supervisor.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "control_port": server.server_port,
                "token": server.token,
            }
        ),
        encoding="utf-8",
    )
    try:
        assert request_restore(data_dir, "20260810T000000000000Z.dpbak")
        assert server.restore_name == "20260810T000000000000Z.dpbak"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mode", ["backup", "restore"])
def test_business_data_operations_are_rejected_while_supervisor_lock_is_held(
    mode: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "user-data"
    configure_runtime_data(data_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "document-pipeline",
            mode,
            "--data-dir",
            str(data_dir),
            "--archive",
            str(tmp_path / "business.dpbak"),
        ],
    )

    with SingleInstance(data_dir / "runtime" / "instance.lock"):
        with pytest.raises(RuntimeError, match="已经在运行"):
            main()
