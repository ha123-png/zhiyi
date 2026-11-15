"""Stop and maintenance behavior with owned fake processes, never sockets."""

import io
import json
import threading
from types import SimpleNamespace

import pytest

from document_pipeline_api import business_backup, supervisor


class GuardedEvent(threading.Event):
    def __init__(self):
        super().__init__()
        self.waits = 0
        self.on_wait = None

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits > 8:
            raise AssertionError("supervisor ignored the synthetic wakeup")
        if self.on_wait:
            callback, self.on_wait = self.on_wait, None
            callback()
        return super().wait(0)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    state = SimpleNamespace(
        stop=GuardedEvent(), mode=None, launches=[], trace=[], states=0,
        control=None, worker_polls=0, handler_waiting=threading.Event(),
        handler_resume=threading.Event(), handler_thread=None, handler_status=None,
        handler_error=None, failure_repaired=False, pending_timer=None,
    )

    class GatedLock:
        def __init__(self, lock): self.lock = lock
        def __enter__(self):
            if state.mode == "late_admission" and threading.current_thread().name == "late-handler":
                state.handler_waiting.set()
                if not state.handler_resume.wait(3):
                    raise AssertionError("late handler was not released")
            return self.lock.__enter__()
        def __exit__(self, *args): return self.lock.__exit__(*args)

    class Control:
        def __init__(self, *_):
            self.server_port = 12345
            self.maintenance_event = threading.Event()
            state.control = self

        @property
        def restore_lock(self): return self._restore_lock
        @restore_lock.setter
        def restore_lock(self, lock): self._restore_lock = GatedLock(lock)

        def serve_forever(self): pass
        def shutdown(self):
            if state.mode == "late_admission" and state.handler_thread:
                state.handler_resume.set()
                state.handler_thread.join(timeout=3)
                if state.handler_thread.is_alive():
                    raise AssertionError("late handler did not finish")
            state.trace.append("control_shutdown")
        def server_close(self): state.trace.append("control_closed")

    class Job:
        def __enter__(self): return self
        def __exit__(self, *_): state.trace.append("job_closed")
        def assign(self, _handle):
            if state.mode == "assign_failure":
                raise OSError("synthetic job assignment failure")

    class Process:
        def __init__(self, mode):
            self.mode = mode
            self.pid = 12345 + len(state.launches)
            self._handle = 123
            self.returncode = None

        def poll(self):
            if self.mode == "worker" and state.mode == "worker_stop":
                state.worker_polls += 1
                if state.worker_polls == 1:
                    state.stop.set()
                else:
                    raise AssertionError("heartbeat wait ignored shutdown")
            if (self.mode == "api" and state.mode in {"failure_grace", "failure_no_request"}
                    and state.states and not state.failure_repaired):
                self.returncode = 12
                return self.returncode
            return None

        def wait(self, timeout):
            state.trace.append("wait_" + self.mode)
            return 0

        def kill(self):
            if state.mode != "assign_failure":
                raise AssertionError("unexpected kill")
            state.trace.append("kill_" + self.mode)

    def spawn(command, **_kwargs):
        mode = "worker" if "worker" in command else "api"
        if state.mode == "preset_stop":
            raise AssertionError("a pre-cancelled launch started a child")
        state.launches.append(mode)
        if mode == "worker" and state.mode in {"restore", "restore_stop", "restore_immediate_stop", "clear", "late_admission", "failure_grace", "failure_no_request"}:
            heartbeat = tmp_path / "runtime" / "worker-heartbeat.json"
            heartbeat.write_text("{}", encoding="utf-8")
        return Process(mode)

    class FakePopen:
        def __new__(cls, command, **kwargs): return spawn(command, **kwargs)
        @classmethod
        def __class_getitem__(cls, _item): return cls

    class HealthResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass

    health_calls = []
    def health(*_args, **_kwargs):
        health_calls.append(True)
        if state.mode == "health_stop":
            if len(health_calls) == 1:
                state.stop.set()
                raise OSError("synthetic unavailable service")
            raise AssertionError("health wait ignored shutdown")
        return HealthResponse()

    class ImmediateTimer:
        def __init__(self, _delay, callback): self.callback = callback
        def start(self):
            if state.mode == "failure_grace":
                state.pending_timer = self.callback
            else:
                self.callback()

    def request_maintenance(name):
        handler = object.__new__(supervisor._ControlHandler)
        handler.server = state.control
        handler.path = "/clear-data" if name == supervisor._CLEAR_DATA_SENTINEL else "/restore"
        body = json.dumps({"name": name}).encode()
        handler.headers = {"Authorization": "Bearer " + state.control.token, "Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        statuses = []
        handler.send_response = statuses.append
        handler.end_headers = lambda: None
        handler.send_error = statuses.append
        handler.do_POST()
        return statuses

    def request_late():
        try:
            state.handler_status = request_maintenance("snapshot.zip")
        except BaseException as error:
            state.handler_error = type(error).__name__

    def publish(_path, _payload):
        state.states += 1
        if state.mode == "failure_no_request":
            return

        def after_ready():
            if state.mode == "late_admission":
                state.handler_thread = threading.Thread(target=request_late, name="late-handler")
                state.handler_thread.start()
                assert state.handler_waiting.wait(3)
                state.stop.set()
            else:
                statuses = request_maintenance(supervisor._CLEAR_DATA_SENTINEL if state.mode == "clear" else "snapshot.zip")
                assert statuses == [202]
                if state.mode == "restore_immediate_stop":
                    state.stop.set()

        if state.states == 1:
            state.stop.on_wait = after_ready
        else:
            if state.mode == "failure_grace":
                state.pending_timer()
                assert not state.control.maintenance_event.is_set()
            state.stop.set()

    def restore(_path, name):
        state.trace.append("restore_" + name)
        if state.mode == "restore_stop":
            state.stop.set()
        if state.mode == "failure_grace":
            state.failure_repaired = True

    monkeypatch.setattr(supervisor, "_ControlServer", Control)
    monkeypatch.setattr(supervisor.threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(supervisor, "KillOnCloseJob", Job)
    monkeypatch.setattr(supervisor.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(supervisor, "urlopen", health)
    monkeypatch.setattr(supervisor, "resolve_app_port", lambda *_: 12345)
    monkeypatch.setattr(business_backup, "recover_interrupted_restore", lambda *_: None)
    monkeypatch.setattr(supervisor, "_write_state", publish)
    monkeypatch.setattr(supervisor, "_seal_stopped_edit_sessions", lambda *_: state.trace.append("sealed"))
    monkeypatch.setattr(supervisor, "_perform_supervised_restore", restore)
    monkeypatch.setattr(supervisor, "_perform_supervised_clear_data", lambda *_: state.trace.append("cleared"))
    return state


def test_preexisting_stop_does_not_launch_or_clear_the_stop(harness, tmp_path):
    harness.mode = "preset_stop"
    harness.stop.set()
    supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.stop.is_set() and not harness.launches


@pytest.mark.parametrize("mode,expected", [("health_stop", ["api"]), ("worker_stop", ["api", "worker"])])
def test_startup_wait_honors_stop_and_cleans_up_its_children(harness, tmp_path, mode, expected):
    harness.mode = mode
    supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.stop.is_set() and harness.launches == expected and harness.states == 0
    assert harness.trace.index("job_closed") < harness.trace.index("wait_api") < harness.trace.index("sealed")
    assert [item.removeprefix("wait_") for item in harness.trace if item.startswith("wait_")] == expected


@pytest.mark.parametrize("mode", ["restore", "clear"])
def test_maintenance_still_restarts_without_consuming_external_stop(harness, tmp_path, mode):
    harness.mode = mode
    supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.launches == ["api", "worker", "api", "worker"]
    assert harness.states == 2 and harness.stop.is_set()
    assert ("cleared" if mode == "clear" else "restore_snapshot.zip") in harness.trace
    assert harness.trace.count("sealed") == 2


def test_stop_during_restore_is_not_cleared_or_restarted(harness, tmp_path):
    harness.mode = "restore_stop"
    supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.launches == ["api", "worker"] and harness.states == 1
    assert "restore_snapshot.zip" in harness.trace and harness.stop.is_set()


def test_accepted_restore_is_completed_when_stop_follows_immediately(harness, tmp_path):
    harness.mode = "restore_immediate_stop"
    supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.launches == ["api", "worker"] and harness.states == 1
    assert "restore_snapshot.zip" in harness.trace and harness.stop.is_set()


def test_restore_handler_arriving_after_shutdown_is_not_accepted(harness, tmp_path):
    harness.mode = "late_admission"
    supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.handler_error is None
    assert harness.handler_status == [409]
    assert "restore_snapshot.zip" not in harness.trace


def test_failed_job_assignment_terminates_and_waits_for_owned_child(harness, tmp_path):
    harness.mode = "assign_failure"
    with pytest.raises(OSError, match="synthetic job assignment failure"):
        supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.launches == ["api"]
    assert harness.trace.index("kill_api") < harness.trace.index("wait_api")
    assert "job_closed" in harness.trace


def test_accepted_restore_survives_child_exit_before_maintenance_timer(harness, tmp_path):
    harness.mode = "failure_grace"
    supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.pending_timer is not None
    assert "restore_snapshot.zip" in harness.trace
    assert harness.launches == ["api", "worker", "api", "worker"]
    assert harness.trace.index("wait_api") < harness.trace.index("sealed") < harness.trace.index("restore_snapshot.zip")


def test_unexpected_child_exit_without_maintenance_is_reported(harness, tmp_path):
    harness.mode = "failure_no_request"
    with pytest.raises(RuntimeError, match="意外退出"):
        supervisor.run_supervisor(tmp_path, open_browser=False, stop_event=harness.stop)
    assert harness.trace.index("job_closed") < harness.trace.index("wait_api") < harness.trace.index("sealed")


@pytest.mark.parametrize("route", ["restore", "stop"])
def test_control_stop_and_maintenance_have_distinct_signals(monkeypatch, route):
    class ImmediateTimer:
        def __init__(self, _delay, callback): self.callback = callback
        def start(self): self.callback()

    control = SimpleNamespace(
        token="synthetic", stop_event=threading.Event(), maintenance_event=threading.Event(),
        restore_lock=threading.Lock(), restore_name=None, accepting_maintenance=True,
        maintenance_token=None,
    )
    handler = object.__new__(supervisor._ControlHandler)
    handler.server = control
    handler.path = "/" + route
    body = b'{"name":"snapshot.zip"}'
    handler.headers = {"Authorization": "Bearer synthetic", "Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    statuses = []
    handler.send_response = statuses.append
    handler.end_headers = lambda: None
    handler.send_error = lambda status: statuses.append(status)
    monkeypatch.setattr(supervisor.threading, "Timer", ImmediateTimer)
    handler.do_POST()
    assert statuses == ([202] if route == "restore" else [204])
    assert control.stop_event.is_set() == (route == "stop")
    assert control.maintenance_event.is_set() == (route == "restore")


def test_old_maintenance_timer_cannot_wake_a_later_request_for_the_same_backup(monkeypatch):
    callbacks = []

    class DeferredTimer:
        def __init__(self, _delay, callback): self.callback = callback
        def start(self): callbacks.append(self.callback)

    monkeypatch.setattr(supervisor.threading, "Timer", DeferredTimer)
    control = SimpleNamespace(
        token="synthetic", stop_event=threading.Event(), maintenance_event=threading.Event(),
        restore_lock=threading.Lock(), restore_name=None, accepting_maintenance=True,
        maintenance_token=None,
    )

    def request_restore():
        handler = object.__new__(supervisor._ControlHandler)
        handler.server = control
        handler.path = "/restore"
        body = b'{"name":"snapshot.zip"}'
        handler.headers = {"Authorization": "Bearer synthetic", "Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        statuses = []
        handler.send_response = statuses.append
        handler.end_headers = lambda: None
        handler.send_error = statuses.append
        handler.do_POST()
        assert statuses == [202]

    request_restore()
    with control.restore_lock:
        control.restore_name = None
        control.maintenance_token = None
        control.accepting_maintenance = True
    request_restore()
    callbacks[0]()
    assert not control.maintenance_event.is_set()
    callbacks[1]()
    assert control.maintenance_event.is_set()


def test_restore_result_does_not_claim_a_restart_before_it_occurs(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "snapshot.dpbak").write_bytes(b"fixture")
    monkeypatch.setattr(business_backup, "restore_business_backup", lambda *_: tmp_path / "rollback")
    supervisor._perform_supervised_restore(tmp_path, "snapshot.dpbak")
    result = json.loads((tmp_path / "runtime" / "restore-result.json").read_text(encoding="utf-8"))
    assert result["state"] == "succeeded"
    assert not any(word in result["message"] for word in ("重启", "重新启动"))


def test_clear_result_does_not_claim_a_restart_before_it_occurs(tmp_path, monkeypatch):
    from document_pipeline_api.services import clear_data
    monkeypatch.setattr(clear_data, "clear_local_data", lambda *_: {"state": "succeeded"})
    supervisor._perform_supervised_clear_data(tmp_path)
    result = json.loads((tmp_path / "runtime" / "clear-data-result.json").read_text(encoding="utf-8"))
    assert result["state"] == "succeeded"
    assert not any(word in result["message"] for word in ("重启", "重新启动"))
