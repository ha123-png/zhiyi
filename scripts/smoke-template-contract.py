"""One real synthetic generation request, with persisted output and explicit intent checks."""
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import json

from fastapi.testclient import TestClient
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_providers.openai_compatible import OpenAICompatibleProvider


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / ".local/template-contract-live"
    root.mkdir(parents=True, exist_ok=True)
    ledger = root / "generation.json"
    state = json.loads(ledger.read_text(encoding="utf-8")) if ledger.exists() else {"requests": []}

    def record():
        temp = ledger.with_suffix(".tmp")
        temp.write_bytes(json.dumps(state, ensure_ascii=False, indent=2).encode())
        temp.replace(ledger)

    requirement = "创建通用会议纪要模板。只要四个每份文件一次的文本字段，名称依次为：会议名称、日期、主要决定、后续行动。四个字段类型全部为文本，包括日期。默认卡片阅读。不生成示例或校验规则。"
    original = OpenAICompatibleProvider._post_completion

    def counted(provider, payload, result_type):
        if state["requests"]:
            raise RuntimeError("This request was already dispatched; inspect the ledger instead of repeating it.")
        state["requests"].append({"time": datetime.now(timezone.utc).isoformat(), "model": provider.model_name, "status": "dispatched"})
        record()
        result = original(provider, payload, result_type)
        state["requests"][0]["status"] = "returned"
        state["raw_output"] = result.model_dump(mode="json")
        record()
        return result

    if "draft" not in state:
        if state["requests"]:
            raise RuntimeError("A dispatched request exists. No automatic repeated model call is allowed.")
        settings = Settings(database_url=f"sqlite:///{root / 'document-pipeline.db'}", storage_dir=root / "uploads", queue_enabled=False)
        with TestClient(create_app(settings)) as client, patch.object(OpenAICompatibleProvider, "_post_completion", counted):
            profile = client.post("/api/v1/models/profiles", json={"name": "生成需求遵循验收", "provider": "openai_compatible", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model_name": "qwen3.6-flash", "timeout_seconds": 90, "reasoning_effort": None, "api_key": args.key_file.read_text(encoding="utf-8-sig").strip(), "acknowledge_remote_data_transfer": True})
            profile.raise_for_status()
            response = client.post("/api/v1/templates/generate", data={"profile_id": profile.json()["id"], "requirement": requirement, "with_examples": "false", "with_rules": "false"})
            response.raise_for_status()
            state["draft"] = response.json()
            record()
    draft = state["draft"]
    checks = {
        "four_named_fields_in_order": [f["label"] for f in draft["fields"]] == ["会议名称", "日期", "主要决定", "后续行动"],
        "all_text_including_date": all(f["value_type"] == "text" for f in draft["fields"]),
        "all_header": all(f["section"] == "header" for f in draft["fields"]),
        "card_by_explicit_request": draft["behavior"]["presentation"]["mode"] == "card",
        "no_examples": all(not f["example"] for f in draft["fields"]),
        "no_rules": not draft["rule_suggestions"],
        "no_silent_fallback": not draft.get("warnings"),
        "no_filename_permission": draft["behavior"]["suggest_filename"] is False,
    }
    state.update(requirement=requirement, checks=checks, passed=all(checks.values()))
    record()
    print(json.dumps({"passed": state["passed"], "requests": len(state["requests"]), "checks": checks}))
    if not state["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
