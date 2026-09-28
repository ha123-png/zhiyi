"""Identity outside the restorable database, shared by long-lived capabilities."""
from pathlib import Path
from uuid import uuid4
import json
import time


def dataset_generation(data_dir: Path) -> str:
    path = data_dir / "runtime" / "dataset-generation.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["generation"]
    except FileNotFoundError:
        return "initial"


def advance_dataset(data_dir: Path) -> int:
    from document_pipeline_api.services.clear_data import _write_json
    path = data_dir / "runtime" / "dataset-generation.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    epoch = max(time.time_ns() // 1000, previous.get("revision_epoch", 0) + 1)
    if epoch >= 2**53 - 1_000_000:
        raise RuntimeError("数据版本已超过可安全表示的范围。")
    _write_json(path, {"generation": str(uuid4()), "revision_epoch": epoch})
    return epoch


def require_dataset_available(data_dir: Path) -> None:
    for name in ("restore-pending.json", "clear-data-pending.json"):
        if (data_dir / "runtime" / name).exists():
            raise RuntimeError("数据维护尚未完成，请完成恢复或清除后再继续。")
