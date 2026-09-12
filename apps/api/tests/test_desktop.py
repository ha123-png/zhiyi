import json
import os
import sqlite3
import threading
import sys
from types import SimpleNamespace
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from document_pipeline_api.desktop import (
    _DesktopApi,
    _application_icon,
    _existing_desktop_url,
    _wait_for_desktop_url,
    run_desktop,
)


def test_open_export_folder_is_read_only_and_missing_copy_does_not_reappear(monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    copy = external / "copy.png"
    copy.write_bytes(b"external-user-owned-copy")
    database = root / "document-pipeline.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, export_state_json TEXT)")
        connection.execute("INSERT INTO tasks VALUES (?, ?)", ("task-1", json.dumps({"status": "completed", "actual_path": str(copy)})))
    before = database.read_bytes()
    opened = []
    monkeypatch.setattr(os, "startfile", lambda path, operation: opened.append((path, operation)), raising=False)
    api = _DesktopApi(root)
    assert api.open_export_folder("task-1") == str(external)
    assert opened == [(str(external), "explore")]
    assert copy.read_bytes() == b"external-user-owned-copy"
    copy.rename(external / "user-renamed.png")
    with pytest.raises(ValueError, match="移动、改名或删除"):
        api.open_export_folder("task-1")
    assert len(opened) == 1
    assert not copy.exists()
    assert database.read_bytes() == before
    with pytest.raises(ValueError, match="没有已完成"):
        api.open_export_folder("task-1' OR 1=1 --")


def test_open_export_folder_reports_shell_permission_failure(monkeypatch, tmp_path):
    target = tmp_path / "copy.png"
    target.write_bytes(b"safe")
    with sqlite3.connect(tmp_path / "document-pipeline.db") as connection:
        connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, export_state_json TEXT)")
        connection.execute("INSERT INTO tasks VALUES (?, ?)", ("one", json.dumps({"status": "completed", "actual_path": str(target)})))
    def denied(*args):
        raise PermissionError("synthetic refusal")
    monkeypatch.setattr(os, "startfile", denied, raising=False)
    with pytest.raises(ValueError, match="内部原件预览不受影响"):
        _DesktopApi(tmp_path).open_export_folder("one")
    assert target.read_bytes() == b"safe"


def test_template_folder_picker_does_not_change_default_export_directory(monkeypatch, tmp_path: Path) -> None:
    selected = tmp_path / "chosen"
    selected.mkdir()
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(FOLDER_DIALOG="folder"))
    api = _DesktopApi(tmp_path / "data")
    api._window = SimpleNamespace(create_file_dialog=lambda kind: [str(selected)])
    before = api.get_export_directory()
    assert api.choose_folder() == str(selected.resolve())
    assert api.get_export_directory() == before
    assert not api._settings_path.exists()
    assert api.choose_export_directory() == str(selected.resolve())
    assert api.get_export_directory() == str(selected.resolve())


def test_desktop_export_defaults_to_output_and_avoids_overwrite(
    monkeypatch, tmp_path: Path
) -> None:
    api = _DesktopApi(tmp_path)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int) -> bytes:
            if hasattr(self, "done"):
                return b""
            self.done = True
            return b"xlsx"

    monkeypatch.setattr("document_pipeline_api.desktop.urlopen", lambda *_a, **_k: Response())
    first = Path(api.export_table(
        "http://127.0.0.1:8765/api/v1/tables/t1/export.xlsx", "台账.xlsx"
    ))
    second = Path(api.export_table(
        "http://127.0.0.1:8765/api/v1/tables/t1/export.xlsx", "台账.xlsx"
    ))
    views = Path(api.export_table(
        "http://127.0.0.1:8765/api/v1/tables/t1/export-views.xlsx", "台账-分Sheet.xlsx"
    ))

    assert first == tmp_path / "output" / "台账.xlsx"
    assert second == tmp_path / "output" / "台账 (2).xlsx"
    assert views == tmp_path / "output" / "台账-分Sheet.xlsx"


def test_desktop_exports_template_json_to_output(tmp_path: Path) -> None:
    api = _DesktopApi(tmp_path)

    first = Path(api.export_template("成绩表.template.json", '{"name":"成绩表"}'))
    second = Path(api.export_template("成绩表.template.json", '{"name":"成绩表"}'))

    assert first == tmp_path / "output" / "成绩表.template.json"
    assert second == tmp_path / "output" / "成绩表.template (2).json"
    assert first.read_text(encoding="utf-8") == '{"name":"成绩表"}'


def test_manual_download_never_overwrites_a_file_created_during_download(monkeypatch, tmp_path):
    api = _DesktopApi(tmp_path)
    target = tmp_path / "output" / "table.xlsx"
    target.parent.mkdir()
    existing_partial = target.with_name(".table.xlsx.partial")
    existing_partial.write_bytes(b"unrelated partial file")

    def response(*args, **kwargs):
        target.write_bytes(b"user file created while download started")
        return BytesIO(b"downloaded workbook")

    monkeypatch.setattr("document_pipeline_api.desktop.urlopen", response)
    with pytest.raises(ValueError, match="同名"):
        api.export_table("http://127.0.0.1:8811/api/v1/tables/one/export.xlsx", "table.xlsx")
    assert target.read_bytes() == b"user file created while download started"
    assert existing_partial.read_bytes() == b"unrelated partial file"
    assert set(target.parent.iterdir()) == {target, existing_partial}


def test_manual_template_export_never_overwrites_racing_target(monkeypatch, tmp_path):
    api = _DesktopApi(tmp_path)
    target = tmp_path / "template.json"
    target.write_bytes(b"user template")
    monkeypatch.setattr(api, "_export_target", lambda name: target)
    with pytest.raises(ValueError, match="同名"):
        api.export_template("template.json", '{"name":"new"}')
    assert target.read_bytes() == b"user template"


def test_desktop_downloads_original_file_through_local_api(
    monkeypatch, tmp_path: Path
) -> None:
    api = _DesktopApi(tmp_path)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int) -> bytes:
            if hasattr(self, "done"):
                return b""
            self.done = True
            return b"original"

    monkeypatch.setattr("document_pipeline_api.desktop.urlopen", lambda *_a, **_k: Response())
    target = Path(api.download_task_file(
        "http://127.0.0.1:8765/api/v1/tasks/task-1/file", "原文件.pdf"
    ))

    assert target == tmp_path / "output" / "原文件.pdf"
    assert target.read_bytes() == b"original"

    with pytest.raises(ValueError, match="下载地址无效"):
        api.download_task_file(
            "http://127.0.0.1:8765/api/v1/tables/t1/export.xlsx", "越权.xlsx"
        )


def test_desktop_export_translates_http_and_connection_errors(
    monkeypatch, tmp_path: Path
) -> None:
    api = _DesktopApi(tmp_path)
    url = "http://127.0.0.1:8765/api/v1/tables/t1/export-views.xlsx"

    def reject_with_detail(*_args, **_kwargs):
        raise HTTPError(
            url,
            422,
            "Unprocessable Entity",
            {},
            BytesIO(json.dumps({"detail": "当前数据表还没有分 Sheet 视图。"}).encode()),
        )

    monkeypatch.setattr("document_pipeline_api.desktop.urlopen", reject_with_detail)
    with pytest.raises(ValueError, match="当前数据表还没有分 Sheet 视图"):
        api.export_table(url, "台账-分Sheet.xlsx")
    assert not list((tmp_path / "output").glob("*.partial"))

    monkeypatch.setattr(
        "document_pipeline_api.desktop.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("refused")),
    )
    with pytest.raises(ValueError, match="无法连接知意本地服务"):
        api.export_table(url, "台账-分Sheet.xlsx")


def test_desktop_bridge_does_not_expose_native_window(tmp_path: Path) -> None:
    api = _DesktopApi(tmp_path)
    api._window = object()

    assert "window" not in vars(api)
    assert all(not key.startswith("window") for key in vars(api))
    # pywebview recursively exposes public object methods (including Path.unlink/rename).
    assert all(name.startswith("_") for name in vars(api))
    public = {name for name in dir(api) if not name.startswith("_")}
    assert public == {"get_export_directory", "choose_export_directory", "choose_folder",
                      "open_export_folder", "export_template", "download_task_file", "export_table"}


def test_secondary_desktop_instance_focuses_existing_window(monkeypatch, tmp_path: Path) -> None:
    class ExistingInstance:
        already_running = True

        def __init__(self, title):
            assert title == "知意"

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    focused: list[bool] = []
    monkeypatch.setattr("document_pipeline_api.desktop._DesktopInstance", ExistingInstance)
    monkeypatch.setattr(
        "document_pipeline_api.desktop._focus_existing_window",
        lambda title: focused.append(title == "知意") or True,
    )
    monkeypatch.setattr(
        "document_pipeline_api.desktop._run_primary_desktop",
        lambda *_args: pytest.fail("secondary instance started the supervisor"),
    )

    run_desktop(tmp_path)

    assert focused == [True]


def test_desktop_mutex_separates_acceptance_from_formal_window():
    from document_pipeline_api.desktop import _DesktopInstance

    formal = _DesktopInstance()
    acceptance = _DesktopInstance("知意 · 升级验收")
    assert formal._mutex_name == "Local\\ZhiyiDesktopWindow"
    assert acceptance._mutex_name != formal._mutex_name
    assert acceptance._mutex_name == _DesktopInstance("知意 · 升级验收")._mutex_name


def test_application_icon_uses_frozen_root_without_evaluating_source_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    icon = tmp_path / "desktop" / "app.ico"
    icon.parent.mkdir()
    icon.write_bytes(b"icon")
    monkeypatch.setattr("document_pipeline_api.desktop.sys._MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(
        "document_pipeline_api.desktop.__file__",
        "C:/shallow/desktop.py",
    )

    assert _application_icon() == icon


def test_wait_for_desktop_url_reads_supervisor_port(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "supervisor.json").write_text(
        json.dumps({"app_port": 8812}),
        encoding="utf-8",
    )
    alive = threading.Thread(target=lambda: threading.Event().wait(0.2))
    alive.start()
    try:
        assert _wait_for_desktop_url(tmp_path, alive, [], timeout=0.1) == (
            "http://127.0.0.1:8812/"
        )
    finally:
        alive.join()


def test_wait_for_desktop_url_surfaces_supervisor_failure(tmp_path: Path) -> None:
    stopped = threading.Thread(target=lambda: None)
    stopped.start()
    stopped.join()
    error = RuntimeError("后台启动失败")

    with pytest.raises(RuntimeError, match="后台启动失败"):
        _wait_for_desktop_url(tmp_path, stopped, [error], timeout=0.1)


def test_existing_desktop_url_reads_running_instance(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "supervisor.json").write_text(
        json.dumps({"app_port": 8899}),
        encoding="utf-8",
    )

    assert _existing_desktop_url(tmp_path) == "http://127.0.0.1:8899/"


def test_initial_window_position_is_unavailable_off_windows(monkeypatch) -> None:
    monkeypatch.setattr("document_pipeline_api.desktop.sys.platform", "linux")
    from document_pipeline_api.desktop import _initial_window_position

    assert _initial_window_position(1440, 920) is None


@pytest.mark.parametrize("window_title", ["知意", "知意 · 升级验收"])
def test_desktop_window_closes_supervisor(monkeypatch, tmp_path: Path, window_title: str) -> None:
    closed_handlers: list[object] = []
    before_show_handlers: list[object] = []
    calls: dict[str, object] = {}

    class ClosedEvent:
        def __iadd__(self, handler):
            closed_handlers.append(handler)
            return self

    class Events:
        closed = ClosedEvent()

        class BeforeShowEvent:
            def __iadd__(self, handler):
                before_show_handlers.append(handler)
                return self

        before_show = BeforeShowEvent()

    class Window:
        events = Events()
        width = 1440
        height = 920

        def move(self, x: int, y: int) -> None:
            calls["move"] = (x, y)

    class FakeWebview:
        class Screen:
            x = 0
            y = 0
            width = 1920
            height = 1080

        screens = [Screen()]

        @staticmethod
        def create_window(title: str, **options: object) -> Window:
            calls["window"] = (title, options)
            return Window()

        @staticmethod
        def start(**options: object) -> None:
            calls["start"] = options
            before_show_handlers[0]()
            closed_handlers[0]()

    class PrimaryInstance:
        already_running = False

        def __init__(self, title):
            assert title == window_title

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def supervisor(
        data_dir: Path,
        explicit_port: int | None,
        *,
        open_browser: bool,
        stop_event: threading.Event,
    ) -> None:
        calls["supervisor"] = (data_dir, explicit_port, open_browser)
        stop_event.wait(1)

    monkeypatch.setitem(__import__("sys").modules, "webview", FakeWebview)
    monkeypatch.setattr("document_pipeline_api.desktop.sys.platform", "test")
    monkeypatch.setattr("document_pipeline_api.desktop._DesktopInstance", PrimaryInstance)
    monkeypatch.setattr(
        "document_pipeline_api.supervisor.run_supervisor",
        supervisor,
    )
    monkeypatch.setattr(
        "document_pipeline_api.desktop._wait_for_desktop_url",
        lambda *_args, **_kwargs: "http://127.0.0.1:8877/",
    )

    run_desktop(tmp_path, 8877, window_title=window_title)

    assert calls["supervisor"] == (tmp_path.resolve(), 8877, False)
    title, window_options = calls["window"]
    assert title == window_title
    assert window_options["url"] == "http://127.0.0.1:8877/"
    assert window_options["min_size"] == (1080, 680)
    assert calls["move"] == (240, 80)
    assert calls["start"]["gui"] == "edgechromium"
    assert calls["start"]["private_mode"] is False
