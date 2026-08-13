import os
from pathlib import Path
import sys


def api_resource_dir() -> Path:
    configured = os.getenv("DOCUMENT_PIPELINE_API_RESOURCE_DIR")
    if configured:
        return Path(configured).resolve()
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "api_runtime"
    return Path(__file__).resolve().parents[2]


def bundled_web_dir() -> Path | None:
    configured = os.getenv("DOCUMENT_PIPELINE_WEB_DIR")
    if configured:
        return Path(configured).resolve()
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "web"
    return None


def default_data_dir() -> Path:
    configured = os.getenv("DOCUMENT_PIPELINE_DATA_DIR")
    if configured:
        return Path(configured).resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("Windows 用户数据目录不可用，程序无法安全保存数据。")
        root = Path(local_app_data)
        current = root / "Zhiyi"
        legacy = root / "DocumentPipeline"
        if not current.exists() and legacy.is_dir():
            try:
                legacy.replace(current)
            except OSError:
                # A running legacy version may still hold files open. Keep using the
                # existing data rather than presenting the user with an empty product.
                return legacy
        return current
    return Path("data")
