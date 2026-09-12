"""Live synthetic unresolved-match -> template -> scope consent -> copy journey."""
from argparse import ArgumentParser
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_providers import build_model_provider
from document_pipeline_api.services.extraction import process_task


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--case", choices=("unmatched", "ambiguous"), default="unmatched")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = root / ".local/tests" / ("smart-live-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    run.mkdir(parents=True)
    source = "文档名称：校园科学展览\n" + "\n".join(f"第{i}条活动记录：学生观察植物生长并记下日期。" for i in range(1000)) + "\n归档编号：ZX-0908\n"
    if args.case == "ambiguous":
        source = "参加班级：一班、二班。此次活动由两个班级共同举办，记录同时属于两个班级。\n" + source
    path = run / "未说明归属的活动记录.txt"
    path.write_text(source, encoding="utf-8")
    external = run / "external"
    external.mkdir()
    settings = Settings(database_url=f"sqlite:///{run / 'smoke.db'}", storage_dir=run / "uploads",
        model_provider="openai_compatible", model_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen3.6-flash", model_reasoning_effort=None, model_timeout_seconds=60)
    key = args.key_file.read_text(encoding="utf-8-sig").strip()
    provider = build_model_provider(replace(settings, model_api_key=key), timeout_seconds=60)
    report = {"success": False, "case": args.case, "stages": []}
    try:
        with TestClient(create_app(settings)) as client:
            def req(method, url, **kwargs):
                response = client.request(method, "/api/v1" + url, **kwargs)
                response.raise_for_status()
                return response

            for item in req("GET", "/templates").json():
                req("PUT", f"/templates/{item['id']}/smart-pool", json={"in_smart_pool": False})
            choices = []
            for owner in ("一班", "二班"):
                template = req("POST", "/templates", json={"name": owner + "活动记录",
                    "description": f"仅收集明确属于{owner}的活动记录。如果文档没有班级信息，不能推定属于本班。",
                    "fields": [{"key": "title", "label": "文档名称", "section": "header"}, {"key": "archive_id", "label": "归档编号", "section": "header"}],
                    "behavior": {"requires_complete_input": True, "presentation": {"mode": "card"}}}).json()
                choices.append(template)
                req("PUT", f"/templates/{template['id']}/smart-pool", json={"in_smart_pool": True})
                req("PUT", f"/templates/{template['id']}/local-export", json={"enabled": True, "expected_revision": 0, "parent_path": str(external)})
            req("PUT", "/system/settings", json={"office_convert": True, "image_convert": True, "allow_limited_input": True})
            task = req("POST", "/tasks", files={"file": (path.name, path.read_bytes(), "text/plain")}).json()

            def process():
                with client.app.state.session_factory() as session:
                    return process_task(session, settings, task["id"], client=provider)

            def state():
                return next(t for t in req("GET", "/tasks").json() if t["id"] == task["id"])

            result = process()
            waiting = state()
            report["stages"].append({"stage": "match", "task": waiting})
            assert result is None and waiting["pending_reason"] == "template", "Unspecified class must require template choice"
            if args.case == "ambiguous":
                assert len(waiting["candidate_templates"]) == 2, "Both classes must remain available as ambiguous candidates"
            assert waiting["template_id"] is None
            assert not list(external.iterdir()), "Copy published before semantic classification"
            assert waiting["match_scope"]["coverage"] == "partial"
            req("POST", f"/tasks/{task['id']}/template", json={"template_id": choices[0]["id"]})
            result = process()
            assert result is not None
            assert result.input_scope.coverage == "partial"
            assert result.result.header["archive_id"] == "ZX-0908"
            assert not list(external.iterdir()), "Copy published before review confirmation"
            req("POST", f"/tasks/{task['id']}/confirm", json={"expected_review_version": 0})
            completed = state()
            assert completed["status"] == "completed"
            assert completed["file_export"]["status"] == "completed"
            assert Path(completed["file_export"]["actual_path"]).read_bytes() == path.read_bytes()
            assert req("GET", f"/tasks/{task['id']}/file").content == path.read_bytes()
            report.update(success=True, task_id=task["id"], final_task=completed, result=result.result.model_dump())
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error).replace(key, "[REDACTED]")[:2000])
        report["cause"] = str(error.__cause__).replace(key, "[REDACTED]")[:2000] if error.__cause__ else None
    finally:
        provider.close()
        (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"success": report["success"], "report": str(run / "report.json"), "error": report.get("error")}, ensure_ascii=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
