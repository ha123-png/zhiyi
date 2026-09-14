from unittest.mock import Mock

import pytest

from document_pipeline_api import installation_processes as processes


def test_already_terminated_child_is_not_an_upgrade_failure(monkeypatch):
    kernel = Mock()
    kernel.OpenProcess.side_effect = [None, 42]
    kernel.WaitForSingleObject.return_value = 0
    monkeypatch.setattr(processes.ctypes, "get_last_error", lambda: 5, raising=False)
    assert processes._open_live_process(kernel, 123) is None
    kernel.CloseHandle.assert_called_once_with(42)


def test_live_access_denied_process_is_still_rejected(monkeypatch):
    kernel = Mock()
    kernel.OpenProcess.side_effect = [None, 42]
    kernel.WaitForSingleObject.return_value = 258
    monkeypatch.setattr(processes.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(processes.ctypes, "WinError", lambda code: OSError(code, "denied"), raising=False)
    with pytest.raises(OSError):
        processes._open_live_process(kernel, 123)
    kernel.CloseHandle.assert_called_once_with(42)


def test_child_disappearing_from_snapshot_is_harmless(monkeypatch):
    kernel = Mock()
    kernel.OpenProcess.return_value = None
    monkeypatch.setattr(processes.ctypes, "get_last_error", lambda: 87, raising=False)
    assert processes._open_live_process(kernel, 123) is None
    kernel.CloseHandle.assert_not_called()
