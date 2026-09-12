"""Add deterministic reading fixtures to the isolated acceptance API; no AI calls."""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


BASE = os.environ.get("ZHIYI_ACCEPTANCE_API", "http://127.0.0.1:8811/api/v1")
NAME = "卡片阅读压力验收（合成）"


def request(path, body=None):
    payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    with urlopen(Request(BASE + path, data=payload, headers={"Content-Type": "application/json"}), timeout=20) as response:
        return json.load(response)


def main():
    folder = Path(os.environ.get("ZHIYI_FIXTURE_DIR", str(Path(__file__).resolve().parents[1] / ".local" / "acceptance-files")))
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        {"title": "短内容：一道加法练习", "answer": "15", "reason": "把加法看成减法。"},
        {"title": "长内容：阅读完整推导", "answer": "先明确条件，再分步推导。\n" * 1100 + "压力测试命中词：守恒条件。\n最后检查边界。", "reason": "正文末尾放置搜索目标，列表应显示命中片段。"},
        {"title": "多字段：一份研究记录", **{f"detail_{i}": f"第{i}项观察：" + "这是一段用于排版的合成记录。" * 20 for i in range(17)}},
        {"title": "空值：仅有标题", "answer": None, "reason": ""},
        {"title": "第二页：超长标题" + "用于检查截断与换行" * 50, "answer": "短答案仍然清楚可见。"},
        {"title": "第二页：普通笔记", "answer": "正常记录与压力样本采用相同尺寸。"},
    ]
    fixture = folder / "卡片阅读压力样本.json"
    if not fixture.exists():
        fixture.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [{"key": key, "label": label, "section": "header", "value_type": "text"} for key, label in
              [("title", "标题"), ("answer", "正文 / 答案"), ("reason", "说明")] + [(f"detail_{i}", f"观察 {i + 1}") for i in range(17)]]
    templates = request("/templates?include_inactive=true")
    template = next((item for item in templates if item["name"] == NAME), None)
    if template is None:
        template = request("/templates", {"name": NAME, "description": "仅用于阅读排版验收，无真实业务内容。", "fields": fields,
            "behavior": {"presentation": {"mode": "card", "title_field": "header.title", "primary_fields": ["header.answer", "header.reason"]}}})
    tables = request("/tables")
    table = next((item for item in tables if item["name"] == NAME), None)
    if table is None:
        table = request("/tables", {"name": NAME, "template_key": template["id"]})
        for row in rows:
            request(f"/tables/{table['id']}/rows", {"values": row})
    print(json.dumps({"table_id": table["id"], "fixture": str(fixture), "model_calls": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
