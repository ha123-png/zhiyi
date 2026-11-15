"""Duplicate starts must not change the running instance's saved port."""

import json

import pytest

from document_pipeline_api import business_backup, supervisor


def test_busy_instance_does_not_probe_or_rewrite_saved_port(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config = runtime / "app-config.json"
    config.write_bytes(b'{"port":8765}\n')
    original = config.read_bytes()
    original_mtime = config.stat().st_mtime_ns
    probes = []
    class BusyLock:
        def __init__(self, path):
            assert path == runtime / "instance.lock"
        def __enter__(self):
            raise RuntimeError("synthetic instance already running")
        def __exit__(self, *_):
            raise AssertionError("An unacquired lock must not be released")
    monkeypatch.setattr(supervisor, "SingleInstance", BusyLock)
    monkeypatch.setattr(supervisor, "_port_in_use", lambda port: probes.append(port) or port == 8765)
    with pytest.raises(RuntimeError, match="already running"):
        supervisor.run_supervisor(tmp_path, open_browser=False)
    assert config.read_bytes() == original and config.stat().st_mtime_ns == original_mtime
    assert probes == []


def test_actual_instance_lock_preserves_port_on_duplicate_start(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config = runtime / "app-config.json"
    config.write_bytes(b'{"port":8765}\n')
    probes = []
    monkeypatch.setattr(supervisor, "_port_in_use", lambda port: probes.append(port) or port == 8765)
    with supervisor.SingleInstance(runtime / "instance.lock"):
        with pytest.raises(RuntimeError, match="已经在运行"):
            supervisor.run_supervisor(tmp_path, open_browser=False)
        assert config.read_bytes() == b'{"port":8765}\n'
        assert probes == []
    # A rejected second start must not leave an acquired lock behind.
    with supervisor.SingleInstance(runtime / "instance.lock"):
        pass


def test_port_selection_is_inside_owned_lock_and_failure_releases_it(tmp_path, monkeypatch):
    state = {"owned": False, "released": False}
    class OwnedLock:
        def __init__(self, _path): pass
        def __enter__(self):
            state["owned"] = True
            return self
        def __exit__(self, *_):
            state["owned"] = False
            state["released"] = True
    def port_in_use(port):
        assert state["owned"], "port selection must hold the instance lock"
        return port == 8765
    def stop_before_start(_data):
        raise RuntimeError("synthetic stop before service startup")
    monkeypatch.setattr(supervisor, "SingleInstance", OwnedLock)
    monkeypatch.setattr(supervisor, "_port_in_use", port_in_use)
    monkeypatch.setattr(business_backup, "recover_interrupted_restore", stop_before_start)
    with pytest.raises(RuntimeError, match="before service startup"):
        supervisor.run_supervisor(tmp_path, open_browser=False)
    assert state == {"owned": False, "released": True}
    assert json.loads((tmp_path / "runtime/app-config.json").read_text(encoding="utf-8"))["port"] == 8766


def test_explicit_occupied_port_releases_its_lock_without_changing_config(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config = runtime / "app-config.json"
    config.write_bytes(b'{"port":8765}\n')
    state = {"owned": False, "released": False}
    class OwnedLock:
        def __init__(self, _path): pass
        def __enter__(self):
            state["owned"] = True
            return self
        def __exit__(self, *_):
            state["owned"] = False
            state["released"] = True
    monkeypatch.setattr(supervisor, "SingleInstance", OwnedLock)
    monkeypatch.setattr(supervisor, "_port_in_use", lambda _port: True)
    with pytest.raises(RuntimeError, match="已被占用"):
        supervisor.run_supervisor(tmp_path, explicit_port=8765, open_browser=False)
    assert state == {"owned": False, "released": True}
    assert config.read_bytes() == b'{"port":8765}\n'
