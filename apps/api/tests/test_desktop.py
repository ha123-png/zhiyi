import json
import threading
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


def test_secondary_desktop_instance_focuses_existing_window(monkeypatch, tmp_path: Path) -> None:
    class ExistingInstance:
        already_running = True

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    focused: list[bool] = []
    monkeypatch.setattr("document_pipeline_api.desktop._DesktopInstance", ExistingInstance)
    monkeypatch.setattr(
        "document_pipeline_api.desktop._focus_existing_window",
        lambda: focused.append(True) or True,
    )
    monkeypatch.setattr(
        "document_pipeline_api.desktop._run_primary_desktop",
        lambda *_args: pytest.fail("secondary instance started the supervisor"),
    )

    run_desktop(tmp_path)

    assert focused == [True]


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


def test_desktop_window_closes_supervisor(monkeypatch, tmp_path: Path) -> None:
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

    run_desktop(tmp_path, 8877)

    assert calls["supervisor"] == (tmp_path.resolve(), 8877, False)
    title, window_options = calls["window"]
    assert title == "知意"
    assert window_options["url"] == "http://127.0.0.1:8877/"
    assert window_options["min_size"] == (1080, 680)
    assert calls["move"] == (240, 80)
    assert calls["start"]["gui"] == "edgechromium"
    assert calls["start"]["private_mode"] is False
