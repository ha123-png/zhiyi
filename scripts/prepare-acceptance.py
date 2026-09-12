"""Prepare only the dedicated development acceptance directory, while stopped."""
from argparse import ArgumentParser
from pathlib import Path
import runpy

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.supervisor import SingleInstance


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    data = root / ".local/acceptance-user"
    marker = root / ".local/acceptance-setup-complete"
    if marker.exists():
        print("Acceptance setup already completed; preserving current settings and cleared state.")
        return
    lock = SingleInstance(data / "runtime/instance.lock")
    try:
        lock.__enter__()
    except RuntimeError:
        print("Acceptance instance already running; setup left unchanged.")
        return
    try:
        settings = Settings(database_url=f"sqlite:///{data / 'document-pipeline.db'}", storage_dir=data / "uploads", queue_enabled=False)
        with TestClient(create_app(settings)) as client:
            profiles = client.get("/api/v1/models/profiles").json()
            # Keep all user changes on subsequent starts. Do not recreate cleared data here.
            if len(profiles) == 1 and profiles[0]["id"] == "default-local-qwen35-4b" and profiles[0]["version"] == 1 and args.key_file and args.key_file.is_file():
                response = client.post("/api/v1/models/profiles", json={
                    "name": "验收云模型（合成文件）", "provider": "openai_compatible",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model_name": "qwen3.6-flash",
                    "api_key": args.key_file.read_text(encoding="utf-8-sig").strip(),
                    "context_length": 8192, "timeout_seconds": 90, "multimodal": True,
                    "acknowledge_remote_data_transfer": True,
                })
                response.raise_for_status()
                profile = response.json()
                client.post(f"/api/v1/models/profiles/{profile['id']}/activate", json={"version": 1, "acknowledge_remote_data_transfer": True}).raise_for_status()
            existing = client.get("/api/v1/templates").json()
            if not any(item["name"] == "活动元信息验收" for item in existing):
                client.post("/api/v1/templates", json={"name": "活动元信息验收",
                    "description": "只记录活动文件的名称和归档编号，不提取所有活动明细。",
                    "fields": [{"key": "title", "label": "文档名称", "section": "header"}, {"key": "archive_id", "label": "归档编号", "section": "header"}],
                    "behavior": {"requires_complete_input": False, "presentation": {"mode": "card", "title_field": "header.title"}}}).raise_for_status()
            if not any(item["name"] == "数学错题验收" for item in existing):
                client.post("/api/v1/templates", json={"name": "数学错题验收",
                    "description": "每道题一条明细，科目属于整份文件。",
                    "fields": [{"key": "subject", "label": "科目", "section": "header"},
                               {"key": "question", "label": "题目", "section": "item"},
                               {"key": "answer", "label": "正确答案", "section": "item"},
                               {"key": "reason", "label": "错因", "section": "item"}],
                    "behavior": {"suggest_filename": True, "requires_complete_input": True,
                        "presentation": {"mode": "card", "title_field": "item.question", "primary_fields": ["header.subject", "item.reason"], "collapsed_fields": ["item.answer"]}}}).raise_for_status()
        fixtures_dir = root / ".local/acceptance-files"
        if not fixtures_dir.exists():
            runpy.run_path(str(root / "scripts/smoke-upgrade.py"))["fixtures"](fixtures_dir)
        marker.write_text("Initial acceptance setup completed. Do not reapply after data clearing.\n", encoding="utf-8")
        print(f"Acceptance fixtures: {fixtures_dir}")
    finally:
        lock.__exit__(None, None, None)


if __name__ == "__main__":
    main()
