import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from document_pipeline_api.desktop import _DesktopApi
from document_pipeline_api.services.native_files import consume_import_ticket

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native picker capability")


def test_picker_issues_only_selected_files_without_confirmation_or_raw_paths(monkeypatch, tmp_path):
    source = tmp_path / "native-picked.txt"
    source.write_text("native selection", encoding="utf-8")
    calls = []
    def dialog(kind, **kwargs):
        calls.append((kind, kwargs))
        return [str(source)]
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(OPEN_DIALOG="open"))
    api = _DesktopApi(tmp_path)
    api._window = SimpleNamespace(create_file_dialog=dialog)
    files = api.choose_import_files()
    assert len(calls) == 1 and calls[0][0] == "open"
    assert calls[0][1]["allow_multiple"]
    assert len(files) == 1 and files[0]["name"] == source.name
    assert "path" not in files[0]
    assert str(source) not in str(files)
    assert source.exists()
    with consume_import_ticket(tmp_path, files[0]["token"]) as (handle, origin):
        assert handle.read().decode() == "native selection"
        assert Path(origin["path"]) == source


def test_cancelled_picker_does_not_issue_authority(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(OPEN_DIALOG="open"))
    api = _DesktopApi(tmp_path)
    api._window = SimpleNamespace(create_file_dialog=lambda *a, **k: None)
    assert api.choose_import_files() == []
    assert not (tmp_path / "native-imports").exists()
