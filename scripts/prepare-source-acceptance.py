"""Create isolated, source-linked synthetic records using an explicit fake model."""
from pathlib import Path
import argparse
import json
import hashlib
import zipfile
from PIL import Image, ImageDraw
from openpyxl import Workbook
from fastapi.testclient import TestClient
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.services.extraction import process_task

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".local/acceptance-rework"
FILES = ROOT / ".local/acceptance-rework-files"

class FixtureModel:
    model_name = "合成验收输出（非真实 AI）"
    def complete_text(self, prompt, result_type):
        return result_type.model_validate({"header": {"title": "来源完整的阅读样例", "subject": "合成验收"}, "items": [
            {"question": "计算 7 + 8", "answer": "15", "reason": "把加法看成减法。"},
            {"question": "计算 6 × 9", "answer": "54", "reason": "乘法口诀记错。"}]})
    def extract_images(self, images, prompt, result_type):
        return self.complete_text(prompt, result_type)
    def extract_image(self, image, prompt, result_type):
        return self.complete_text(prompt, result_type)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    FILES.mkdir(parents=True, exist_ok=True)
    content = "合成验收：计算 7 + 8 = 15；计算 6 × 9 = 54。"
    (FILES / "数学练习.txt").write_text(content, encoding="utf-8")
    (FILES / "同名").mkdir(exist_ok=True)
    (FILES / "同名/数学练习.txt").write_text(content + "第二份同名文件。", encoding="utf-8")
    image = Image.new("RGB", (640, 800), "white")
    ImageDraw.Draw(image).text((40, 60), "SYNTHETIC: 7 + 8 = 15; 6 x 9 = 54", fill="black", font_size=24)
    image.save(FILES / "数学练习.png")
    image.save(FILES / "数学练习.pdf", "PDF")
    book = Workbook()
    book.active.append(["题目", "答案"])
    book.active.append(["7 + 8", 15])
    book.active.append(["6 x 9", 54])
    book.save(FILES / "数学练习.xlsx")
    with zipfile.ZipFile(FILES / "数学练习.docx", "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{content}</w:t></w:r></w:p></w:body></w:document>')
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    settings = Settings(database_url=f"sqlite:///{DATA / 'document-pipeline.db'}", storage_dir=DATA / "uploads", queue_enabled=False)
    report = []
    with TestClient(create_app(settings)) as client:
        def req(method, path, **kwargs):
            response = client.request(method, "/api/v1" + path, **kwargs)
            response.raise_for_status()
            return response
        templates = req("GET", "/templates").json()
        template = next((t for t in templates if t["name"] == "原件与阅读验收（模拟输出）"), None)
        if not template:
            template = req("POST", "/templates", json={"name": "原件与阅读验收（模拟输出）", "description": "真实上传与来源关联；提取结果为合成模拟，不是真实 AI 证据。", "fields": [
                {"key": key, "label": label, "section": section} for key, label, section in [
                    ("title", "标题", "header"), ("subject", "科目", "header"), ("question", "题目", "item"), ("answer", "正确答案", "item"), ("reason", "错因", "item")]],
                "behavior": {"presentation": {"mode": "card", "title_field": "item.question", "primary_fields": ["item.answer", "item.reason"]}}}).json()
        existing_tasks = req("GET", "/tasks").json()
        existing = {t["sha256"] for t in existing_tasks}
        names = {t["filename"] for t in existing_tasks}
        for suffix, mime in [("txt", "text/plain"), ("txt-copy", "text/plain"), ("png", "image/png"), ("pdf", "application/pdf"), ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")]:
            file = FILES / ("同名/数学练习.txt" if suffix == "txt-copy" else f"数学练习.{suffix}")
            if (suffix != "txt-copy" and file.name in names) or hashlib.sha256(file.read_bytes()).hexdigest() in existing:
                continue
            task = req("POST", "/tasks", files={"file": (file.name, file.read_bytes(), mime)}, data={"template_id": template["id"]}).json()
            with client.app.state.session_factory() as session:
                result = process_task(session, settings, task["id"], client=FixtureModel())
            assert result is not None, task["id"]
            req("PUT", f"/tasks/{task['id']}/review", json={"expected_version": 0, "result": result.result.model_dump()})
            original = req("GET", f"/tasks/{task['id']}/file").content
            assert original == file.read_bytes()
            report.append({"task_id": task["id"], "file": str(file), "original_verified": True})
        report = []
        for task in req("GET", "/tasks").json():
            if task["template_id"] != template["id"]:
                continue
            content = req("GET", f"/tasks/{task['id']}/file").content
            assert hashlib.sha256(content).hexdigest() == task["sha256"]
            report.append({"task_id": task["id"], "file": task["filename"], "original_verified": True})
    (DATA / "source-fixtures-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": len(report), "real_model_calls": 0, "data": str(DATA)}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--files-dir", type=Path, default=FILES)
    arguments = parser.parse_args()
    DATA = arguments.data_dir.resolve()
    FILES = arguments.files_dir.resolve()
    main()
