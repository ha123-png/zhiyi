import threading

import pytest

from document_pipeline_api import desktop, supervisor


@pytest.mark.parametrize("payload", ["[]", "null"])
def test_non_object_desktop_settings_uses_default_export_directory(tmp_path, payload):
    path = tmp_path / "desktop-settings.json"
    path.write_text(payload, encoding="utf-8")

    assert desktop._DesktopApi(tmp_path).get_export_directory() == str((tmp_path / "output").resolve())
    assert path.read_text(encoding="utf-8") == payload


@pytest.mark.parametrize("focus_existing", [False, True])
def test_startup_wait_failure_stops_owned_supervisor_before_returning(tmp_path, monkeypatch, focus_existing):
    started = threading.Event()
    stopped = threading.Event()
    owned = {}

    def run_supervisor(*args, stop_event, **kwargs):
        owned["stop"] = stop_event
        owned["thread"] = threading.current_thread()
        started.set()
        try:
            assert stop_event.wait(timeout=10)
        finally:
            stopped.set()

    def fail_wait(*args):
        assert started.wait(timeout=2)
        raise RuntimeError("synthetic startup timeout")

    monkeypatch.setattr(supervisor, "run_supervisor", run_supervisor)
    monkeypatch.setattr(desktop, "_wait_for_desktop_url", fail_wait)
    monkeypatch.setattr(desktop, "_existing_desktop_url", lambda *_: "http://127.0.0.1:12345/")
    monkeypatch.setattr(desktop, "_focus_existing_window", lambda *_: focus_existing)
    try:
        if focus_existing:
            desktop._run_primary_desktop(tmp_path, None)
        else:
            with pytest.raises(RuntimeError, match="synthetic startup timeout"):
                desktop._run_primary_desktop(tmp_path, None)
        assert stopped.is_set()
        assert not owned["thread"].is_alive()
    finally:
        # Ensure even the old implementation cannot leak a test thread.
        if "stop" in owned:
            owned["stop"].set()
            owned["thread"].join(timeout=2)
