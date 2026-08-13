"""真实 LM Studio 生命周期冒烟；默认只读，--exercise 会恢复测试前服务状态。"""

from __future__ import annotations

import argparse
import json

from document_pipeline_api.model_providers.local_model_manager import LmStudioManager


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--model-match", default="qwen3.5-4b")
    parser.add_argument("--exercise", action="store_true")
    args = parser.parse_args()

    manager = LmStudioManager(args.base_url)
    before = manager.server_status()
    report: dict[str, object] = {"before": before, "cli_found": manager._lms is not None}
    if not args.exercise:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if manager._lms is not None else 2

    smoke_identifier = "document-pipeline-runtime-smoke"
    loaded_by_test = False
    exit_code = 0
    try:
        started = manager.start_server()
        report["start"] = started
        if not started.get("ok"):
            exit_code = 3
        else:
            models = manager.list_models()
            report["model_count"] = len(models)
            model_key = next(
                (item for item in models if args.model_match.lower() in item.lower()),
                None,
            )
            if model_key is None:
                report["error"] = f"没有找到包含 {args.model_match} 的本地模型。"
                exit_code = 4
            else:
                loaded = manager.loaded_models()
                if not any(args.model_match.lower() in item.lower() for item in loaded):
                    action = manager.load_model(
                        model_key,
                        context_length=4096,
                        identifier=smoke_identifier,
                    )
                    report["load"] = action
                    if not action.get("ok"):
                        exit_code = 5
                    else:
                        loaded_by_test = True
                report["ready"] = manager.server_status()
    finally:
        if loaded_by_test:
            report["cleanup_unload"] = manager.unload_model(smoke_identifier)
        if not before.get("running"):
            report["cleanup_stop"] = manager.stop_server()
    report["exit_code"] = exit_code
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
