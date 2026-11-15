"""Create a fresh, isolated product workspace with synthetic file-linked data.

No production database, model key or installed application is accessed. Extraction
is explicitly simulated; API edits and revisions use the real product services.
"""
from contextlib import nullcontext
from unittest.mock import patch
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.services.extraction import process_task


class SyntheticModel:
    model_name = "合成样本（非 AI 提取）"

    def __init__(self, value):
        self.value = value

    def complete_text(self, prompt, result_type):
        return result_type.model_validate(self.value)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    allowed = Path(__file__).resolve().parents[1] / ".local"
    if not root.is_relative_to(allowed):
        parser.error("Synthetic data must stay in this workspace's .local directory")
    if (root / "document-pipeline.db").exists():
        parser.error("Use a new directory; existing workspaces are not overwritten")
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(database_url=f"sqlite:///{root / 'document-pipeline.db'}", storage_dir=root / "uploads", queue_enabled=False)
    fields = [{"key": key, "label": label, "section": section, "value_type": kind} for key, label, section, kind in [
        ("customer", "客户", "header", "text"), ("date", "采购日期", "header", "date"), ("order_id", "单号", "header", "text"),
        ("item", "项目", "item", "text"), ("quantity", "数量", "item", "number"), ("unit_price", "单价", "item", "number"), ("amount", "金额", "item", "number")]]
    report = {"synthetic": True, "model_calls": 0, "simulated_provider_calls": 0, "files": []}
    with TestClient(create_app(settings)) as client:
        def api(method, path, **kwargs):
            response = client.request(method, "/api/v1" + path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None

        template = api("POST", "/templates", json={"name": "采购记录", "description": "合成样本，用于核对界面与数据链路。", "fields": fields,
            "behavior": {"presentation": {"mode": "card"}}})
        for index, (customer, count) in enumerate([("南山文创", 4), ("青禾工作室", 3), ("星河书店", 2), ("滨江文化中心", 5),
            ("用于核对长文件名不会挤压按钮和状态标签的合成文化有限公司", 20), ("千条明细样本", 1000), ("万条明细样本", 10000)], 1):
            header = {"customer": customer, "date": "2026-09-20", "order_id": f"CG-20260920-{index:03}"}
            items = [{"item": ("展览手册", "帆布袋", "导视印刷", "旧版展签")[n % 4] + (f" {n + 1}" if count > 20 else ""),
                "quantity": n % 100 + 1, "unit_price": 28, "amount": (n % 100 + 1) * 28} for n in range(count)]
            value = {"header": header, "items": items}
            # Keep the source honest and readable. The whole original is preserved;
            # preview and model requests independently enforce their own budgets.
            original = ("合成测试资料，无真实业务数据。\n" + json.dumps(value, ensure_ascii=False, indent=2)).encode()
            task = api("POST", "/tasks", files={"file": (f"{customer}·采购单.txt", original, "text/plain")}, data={"template_id": template["id"]})
            # This fixture performs no inference: do not create model usage records
            # that the product dashboard could mistake for real provider calls.
            with client.app.state.session_factory() as session, patch(
                "document_pipeline_api.services.model_usage.model_call", lambda *args, **kwargs: nullcontext()
            ):
                result = process_task(session, settings, task["id"], client=SyntheticModel(value))
                report["simulated_provider_calls"] += 1
            if result is None:
                raise RuntimeError(f"Synthetic extraction failed for fixture {index}")
            current = api("GET", f"/tasks/{task['id']}/current-details")
            if index == 1:
                api("PATCH", f"/tasks/{task['id']}/current", json={"expected_version": current["version"], "header_changes": {"customer": "南山文化"}})
                current = api("GET", f"/tasks/{task['id']}/current-details")
                api("PATCH", f"/tasks/{task['id']}/current", json={"expected_version": current["version"], "header_changes": {"customer": "南山文创"},
                    "detail_changes": {str(current["items"][1]["id"]): {"quantity": 200, "amount": 5600}}, "deleted_ids": [current["items"][3]["id"]]})
            current = api("GET", f"/tasks/{task['id']}/current-details")
            assert current["total"] == count - (1 if index == 1 else 0)
            stored = client.get(f"/api/v1/tasks/{task['id']}/file")
            assert stored.status_code == 200 and sha256(stored.content).digest() == sha256(original).digest()
            report["files"].append({"task_id": task["id"], "filename": task["filename"], "details": current["total"], "original_sha256": sha256(original).hexdigest()})
        report["tables"] = [{"id": table["id"], "name": table["name"]} for table in api("GET", "/tables")]
    (root / "fixtures.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"data_dir": str(root), "files": len(report["files"]), "model_calls": 0, "simulated_provider_calls": report["simulated_provider_calls"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
