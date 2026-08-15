"""Native Windows shell for the local web application."""

from __future__ import annotations

import json
import ctypes
import traceback
import sys
import threading
import time
from urllib.parse import urlparse
from urllib.request import urlopen
from pathlib import Path

from document_pipeline_api.runtime_paths import default_data_dir

_ERROR_ALREADY_EXISTS = 183
_WINDOW_WIDTH = 1440
_WINDOW_HEIGHT = 920


class _DesktopApi:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        # pywebview exposes public bridge members to JavaScript recursively.
        # A public Window reference creates a cycle (api -> window -> api) and
        # can block the UI thread while the bridge is being registered.
        self._window: object | None = None
        self._settings_path = data_dir / "desktop-settings.json"

    def _configured_export_directory(self) -> Path:
        try:
            value = json.loads(self._settings_path.read_text(encoding="utf-8")).get(
                "export_directory"
            )
            if isinstance(value, str) and value.strip():
                return Path(value).expanduser().resolve()
        except (OSError, TypeError, json.JSONDecodeError):
            pass
        return (self.data_dir / "output").resolve()

    def get_export_directory(self) -> str:
        return str(self._configured_export_directory())

    def choose_export_directory(self) -> str | None:
        if self._window is None:
            return None
        import webview

        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        selected = Path(result[0] if isinstance(result, (list, tuple)) else result).resolve()
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._settings_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"export_directory": str(selected)}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self._settings_path)
        return str(selected)

    def _export_target(self, filename: str) -> Path:
        safe_name = Path(filename).name
        if not safe_name or safe_name != filename:
            raise ValueError("导出文件名无效。")
        directory = self._configured_export_directory()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / safe_name
        stem, suffix = target.stem, target.suffix
        index = 2
        while target.exists():
            target = directory / f"{stem} ({index}){suffix}"
            index += 1
        return target

    def export_template(self, filename: str, content: str) -> str:
        """把字段模板 JSON 写入桌面版默认导出目录。"""
        if not filename.lower().endswith(".json"):
            raise ValueError("字段模板只能导出为 JSON 文件。")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("字段模板内容无效或过大。")
        target = self._export_target(filename)
        target.write_text(content, encoding="utf-8")
        return str(target)

    def download_task_file(self, url: str, filename: str) -> str:
        """通过本地 API 下载任务原文件，避免依赖 WebView 浏览器下载。"""
        parsed = urlparse(url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("桌面版只允许从知意本地服务下载。")
        parts = parsed.path.rstrip("/").split("/")
        if len(parts) != 6 or parts[:4] != ["", "api", "v1", "tasks"] or parts[-1] != "file":
            raise ValueError("原文件下载地址无效。")
        target = self._export_target(filename)
        with urlopen(url, timeout=60) as response, target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        return str(target)

    def export_table(self, url: str, filename: str) -> str:
        parsed = urlparse(url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("桌面版只允许从知意本地服务导出。")
        is_table_export = "/export." in parsed.path or "/export-views." in parsed.path
        if not parsed.path.startswith("/api/v1/tables/") or not is_table_export:
            raise ValueError("导出地址无效。")
        target = self._export_target(filename)
        with urlopen(url, timeout=60) as response, target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        return str(target)


class _DesktopInstance:
    """Process-wide Windows mutex acquired before starting the supervisor."""

    def __init__(self) -> None:
        self.handle: int | None = None
        self.already_running = False

    def __enter__(self) -> "_DesktopInstance":
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            kernel32.SetLastError(0)
            self.handle = kernel32.CreateMutexW(None, False, "Local\\ZhiyiDesktopWindow")
            if not self.handle:
                raise ctypes.WinError()
            self.already_running = kernel32.GetLastError() == _ERROR_ALREADY_EXISTS
        return self

    def __exit__(self, *_args: object) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)


def _application_icon() -> Path | None:
    # Do not put the source-tree fallback inside getattr(): Python evaluates
    # default arguments eagerly, so a shallow frozen path raised IndexError
    # even though PyInstaller had provided _MEIPASS.
    frozen_root = getattr(sys, "_MEIPASS", None)
    root = Path(frozen_root) if frozen_root else Path(__file__).resolve().parents[4]
    candidate = root / "desktop" / "app.ico"
    return candidate if candidate.is_file() else None


def _focus_existing_window(title: str = "知意") -> bool:
    """Restore and focus an existing native window without opening a browser."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    handle = user32.FindWindowW(None, title)
    if not handle:
        return False
    user32.ShowWindow(handle, 9)  # SW_RESTORE
    return bool(user32.SetForegroundWindow(handle))


def _show_desktop_error(message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "知意启动失败", 0x10)


def _center_window(window: object, webview: object) -> None:
    """Center the native window before its first visible paint."""
    if sys.platform == "win32":
        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("size", ctypes.c_ulong), ("monitor", Rect),
                ("work", Rect), ("flags", ctypes.c_ulong),
            ]

        user32 = ctypes.windll.user32
        handle = user32.FindWindowW(None, "知意")
        bounds = Rect()
        info = MonitorInfo()
        info.size = ctypes.sizeof(MonitorInfo)
        monitor = user32.MonitorFromWindow(handle, 2) if handle else 0
        if handle and user32.GetWindowRect(handle, ctypes.byref(bounds)) and monitor:
            if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                width = bounds.right - bounds.left
                height = bounds.bottom - bounds.top
                x = info.work.left + max(0, (info.work.right - info.work.left - width) // 2)
                y = info.work.top + max(0, (info.work.bottom - info.work.top - height) // 2)
                user32.SetWindowPos(handle, 0, x, y, 0, 0, 0x0001 | 0x0004)
                return
    screens = getattr(webview, "screens", ())
    if screens:
        screen = screens[0]
        width = int(getattr(window, "width", 1440))
        height = int(getattr(window, "height", 920))
        x = int(getattr(screen, "x", 0)) + max(0, (int(screen.width) - width) // 2)
        y = int(getattr(screen, "y", 0)) + max(0, (int(screen.height) - height) // 2)
        window.move(x, y)


def _initial_window_position(width: int, height: int) -> tuple[int, int] | None:
    """Compute a centered position before WebView creates its native handle."""
    if sys.platform != "win32":
        return None

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    work = Rect()
    # SPI_GETWORKAREA excludes the taskbar, unlike raw screen dimensions.
    if not ctypes.windll.user32.SystemParametersInfoW(
        0x0030, 0, ctypes.byref(work), 0
    ):
        return None
    return (
        work.left + max(0, (work.right - work.left - width) // 2),
        work.top + max(0, (work.bottom - work.top - height) // 2),
    )


def _wait_for_desktop_url(
    data_dir: Path,
    supervisor: threading.Thread,
    errors: list[BaseException],
    timeout: float = 45,
) -> str:
    state_path = data_dir / "runtime" / "supervisor.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if errors:
            raise RuntimeError(str(errors[0])) from errors[0]
        if not supervisor.is_alive():
            raise RuntimeError("知意后台服务未能启动，请运行诊断工具查看日志。")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            port = int(state["app_port"])
            if 1 <= port <= 65535:
                return f"http://127.0.0.1:{port}/"
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise RuntimeError("知意启动超过 45 秒，请运行诊断工具查看日志。")


def _existing_desktop_url(data_dir: Path) -> str | None:
    try:
        state = json.loads(
            (data_dir / "runtime" / "supervisor.json").read_text(encoding="utf-8")
        )
        port = int(state["app_port"])
        return f"http://127.0.0.1:{port}/" if 1 <= port <= 65535 else None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def run_desktop(data_dir: Path | None = None, explicit_port: int | None = None) -> None:
    """Start API/worker services and host the UI in a native WebView2 window."""
    active_data_dir = (data_dir or default_data_dir()).resolve()
    with _DesktopInstance() as instance:
        if instance.already_running:
            # The primary process may still be creating its window. Never keep
            # a second GUI process alive while waiting: an immediate click can
            # focus an existing window, and an early click simply returns while
            # the primary window finishes appearing.
            _focus_existing_window()
            return
        _run_primary_desktop(active_data_dir, explicit_port)


def _run_primary_desktop(active_data_dir: Path, explicit_port: int | None) -> None:
    from document_pipeline_api.supervisor import run_supervisor

    shutdown = threading.Event()
    errors: list[BaseException] = []

    def supervise() -> None:
        try:
            run_supervisor(
                active_data_dir,
                explicit_port,
                open_browser=False,
                stop_event=shutdown,
            )
        except BaseException as error:  # noqa: BLE001 - forwarded to the GUI thread
            errors.append(error)

    supervisor = threading.Thread(
        target=supervise,
        name="document-pipeline-supervisor",
        daemon=False,
    )
    supervisor.start()
    try:
        url = _wait_for_desktop_url(active_data_dir, supervisor, errors)
    except RuntimeError:
        existing_url = _existing_desktop_url(active_data_dir)
        if existing_url and _focus_existing_window():
            supervisor.join(timeout=2)
            return
        raise
    try:
        import webview

        initial_position = _initial_window_position(_WINDOW_WIDTH, _WINDOW_HEIGHT)
        position_options = (
            {"x": initial_position[0], "y": initial_position[1]}
            if initial_position else {}
        )
        desktop_api = _DesktopApi(active_data_dir)
        window = webview.create_window(
            "知意",
            url=url,
            width=_WINDOW_WIDTH,
            height=_WINDOW_HEIGHT,
            min_size=(1080, 680),
            resizable=True,
            background_color="#f8fafc",
            text_select=True,
            js_api=desktop_api,
            **position_options,
        )
        if window is None:
            raise RuntimeError("无法创建知意桌面窗口。")
        desktop_api._window = window
        window.events.closed += shutdown.set
        window.events.before_show += lambda: _center_window(window, webview)
        icon = _application_icon()
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(active_data_dir / "webview"),
            icon=str(icon) if icon else None,
        )
    except BaseException:  # noqa: BLE001 - even runtime-loader exits need a browser fallback
        logs = active_data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "desktop.log").write_text(traceback.format_exc(), encoding="utf-8")
        _show_desktop_error(
            "知意桌面窗口无法启动。请运行“诊断知意”并查看 desktop.log。"
        )
        raise
    finally:
        shutdown.set()
        supervisor.join(timeout=20)
        if supervisor.is_alive():
            raise RuntimeError("知意后台服务未能在窗口关闭后安全退出。")
