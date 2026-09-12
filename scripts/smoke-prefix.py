"""Two explicitly authorized synthetic model calls; no connection probe."""
from argparse import ArgumentParser
from datetime import datetime
import json
from pathlib import Path
from fastapi.testclient import TestClient
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.model_providers.openai_compatible import OpenAICompatibleProvider
from document_pipeline_api.services.extraction import process_task


def main():
    parser = ArgumentParser()
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--partial-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / ".local/tests" / ("prefix-live-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    settings = Settings(database_url=f"sqlite:///{root / 'live.db'}", storage_dir=root / "uploads", model_provider="openai_compatible",
        model_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model_name="qwen3.6-flash", model_reasoning_effort=None)
    provider = OpenAICompatibleProvider(settings.model_base_url, settings.model_name,
        api_key=args.key_file.read_text(encoding="utf-8-sig").strip(), reasoning_effort=None, timeout_seconds=90)
    report = {"model": settings.model_name, "calls": 0, "cases": []}
    try:
        with TestClient(create_app(settings)) as client:
            def req(method, path, **kwargs):
                response = client.request(method, "/api/v1" + path, **kwargs)
                response.raise_for_status()
                return response
            template = req("POST", "/templates", json={"name": "合成植物观察", "fields": [
                {"key": "title", "label": "标题", "section": "header"},
                {"key": "summary", "label": "摘要", "section": "header"}], "behavior": {"suggest_filename": True}}).json()
            for limited, filename in [(False, "知意合成测试-植物观察记录.txt"), (True, "1001656.txt")]:
                if args.partial_only and not limited:
                    continue
                req("PUT", "/system/settings", json={"image_convert": True, "office_convert": True,
                    "allow_limited_input": limited, "input_text_limit": 128})
                content = "合成测试材料，不包含真实用户信息。\n标题：植物观察记录\n观察：向日葵幼苗在一周内长出两片新叶。每天记录叶片数量和光照时间。\n" + "普通观察记录。" * 100 + "\n尾部校验标记：不得推测未读取内容。"
                task = req("POST", "/tasks", files={"file": (filename, content.encode(), "text/plain")}, data={"template_id": template["id"]}).json()
                with client.app.state.session_factory() as session:
                    report["calls"] += 1
                    result = process_task(session, settings, task["id"], client=provider)
                    stored = session.get(TaskRecord, task["id"])
                    if result is None:
                        report["cases"].append({"limited": limited, "success": False, "code": stored.failure_code, "message": stored.failure_message})
                        continue
                    name = result.file_name.suggested_filename
                    assert name == filename if not limited else name != filename
                    assert result.input_scope.coverage == ("partial" if limited else "complete")
                    assert "file_name_advice" not in result.result.model_dump()
                    report["cases"].append({"limited": limited, "success": True, "name": name, "scope": result.input_scope.model_dump(), "result": result.result.model_dump()})
                assert req("GET", f"/tasks/{task['id']}/file").content == content.encode()
    finally:
        provider.close()
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"calls": report["calls"], "passed": sum(case["success"] for case in report["cases"]), "report": str(root / "report.json")}))

if __name__ == "__main__":
    main()
