"""Create isolated, source-linked synthetic records using an explicit fake model."""
from pathlib import Path
import argparse
from contextlib import closing
import json
import hashlib
import shutil
import sqlite3
import tempfile
import zipfile
from PIL import Image, ImageDraw
from openpyxl import Workbook
from fastapi.testclient import TestClient
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.storage_paths import resolve_original_reference, task_storage_name

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".local/acceptance-rework"
FILES = ROOT / ".local/acceptance-rework-files"
FIXTURE_NAMES = ("数学练习.txt", "同名/数学练习.txt", "数学练习.png",
                 "数学练习.pdf", "数学练习.docx", "数学练习.xlsx")

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


def is_link(path):
    return path.is_symlink() or path.is_junction()


def read_checkpoint_rows(database):
    # SQLite may create -wal/-shm even when opening the original with mode=ro.
    # Query an owned copy, including any committed WAL, and refuse a changing
    # source pair so a concurrent writer cannot yield a mixed checkpoint.
    wal = database.with_name(database.name + "-wal")
    with tempfile.TemporaryDirectory(prefix=".source-checkpoint-", dir=database.parent.parent) as scratch:
        scratch_dir = Path(scratch)
        sources = [database] + ([wal] if wal.is_file() else [])
        fingerprints = {}
        for source in sources:
            target = scratch_dir / source.name
            with source.open("rb") as original, target.open("xb") as copy:
                shutil.copyfileobj(original, copy)
            with target.open("rb") as copied:
                fingerprints[source] = hashlib.file_digest(copied, "sha256").hexdigest()
        if wal.is_file() != (wal in fingerprints):
            raise ValueError("checkpoint WAL changed while copying")
        for source, digest in fingerprints.items():
            if is_link(source) or not source.is_file():
                raise ValueError("checkpoint database path changed while copying")
            with source.open("rb") as original:
                if hashlib.file_digest(original, "sha256").hexdigest() != digest:
                    raise ValueError("checkpoint database changed while copying")
        copied_database = scratch_dir / database.name
        with closing(sqlite3.connect(copied_database.as_uri() + "?mode=ro", uri=True)) as connection:
            return connection.execute(
                "SELECT tasks.id, tasks.filename, tasks.sha256, tasks.content_type, "
                "tasks.size_bytes, tasks.storage_path, tasks.internal_storage_json FROM tasks "
                "JOIN templates ON tasks.template_id = templates.id WHERE templates.name = ?",
                ("原件与阅读验收（模拟输出）",),
            ).fetchall()


def existing_checkpoint():
    """Reuse only a complete, report-backed synthetic fixture set unchanged."""
    files = [FILES / name for name in FIXTURE_NAMES]
    report_path = DATA / "source-fixtures-report.json"
    database = DATA / "document-pipeline.db"
    candidates = [*files, report_path, database,
                  DATA / "document-pipeline.db-wal", DATA / "document-pipeline.db-shm"]
    if not any(path.exists() or is_link(path) for path in candidates):
        return None
    if (is_link(FILES / "同名") or is_link(DATA / "uploads") or
            any(is_link(path) for path in candidates) or
            any(not path.is_file() for path in [*files, report_path, database])):
        raise RuntimeError("Refusing incomplete or linked source-fixture checkpoint")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, list):
            raise ValueError("report is not a list")
        reported = {item["task_id"] for item in report if item.get("original_verified") is True}
        rows = read_checkpoint_rows(database)
        needed = {(file.name, hashlib.sha256(file.read_bytes()).hexdigest()) for file in files}
        verified = set()
        for task_id, name, digest, content_type, size_bytes, storage_path, storage_json in rows:
            if task_id not in reported or (name, digest) not in needed:
                continue
            metadata = json.loads(storage_json) if storage_json else {}
            if not isinstance(metadata, dict):
                raise ValueError("invalid managed-original metadata")
            original = resolve_original_reference(DATA / "uploads", storage_path,
                task_storage_name(task_id, content_type), metadata.get("previous_path"))
            if not original.is_file() or original.stat().st_size != size_bytes:
                raise ValueError("managed original is missing or has changed size")
            with original.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != digest:
                    raise ValueError("managed original does not match its task digest")
            verified.add((name, digest))
        if len(needed) != len(files) or not needed.issubset(verified):
            raise ValueError("fixture bytes do not match the verified task records")
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        raise RuntimeError("Refusing unverified source-fixture checkpoint") from error
    return report


def main():
    local_root = (ROOT / ".local").resolve()
    for path in (DATA, FILES):
        if path == local_root or not path.is_relative_to(local_root):
            raise RuntimeError("Source-fixture directories must stay inside this workspace .local")
    if DATA == FILES or DATA in FILES.parents or FILES in DATA.parents:
        raise RuntimeError("Source-fixture data and files directories must be separate")
    report = existing_checkpoint()
    if report is not None:
        print(json.dumps({"prepared": len(report), "real_model_calls": 0, "data": str(DATA), "reused_checkpoint": True}))
        return
    if DATA.exists() and any(DATA.iterdir()):
        raise RuntimeError("Refusing an existing non-empty data directory without a verified checkpoint")
    DATA.mkdir(parents=True, exist_ok=True)
    FILES.mkdir(parents=True, exist_ok=True)
    if is_link(FILES / "同名"):
        raise RuntimeError("Refusing linked fixture directory")
    content = "合成验收：计算 7 + 8 = 15；计算 6 × 9 = 54。"
    with (FILES / "数学练习.txt").open("x", encoding="utf-8") as output:
        output.write(content)
    (FILES / "同名").mkdir(exist_ok=True)
    with (FILES / "同名/数学练习.txt").open("x", encoding="utf-8") as output:
        output.write(content + "第二份同名文件。")
    image = Image.new("RGB", (640, 800), "white")
    ImageDraw.Draw(image).text((40, 60), "SYNTHETIC: 7 + 8 = 15; 6 x 9 = 54", fill="black", font_size=24)
    with (FILES / "数学练习.png").open("xb") as output:
        image.save(output, "PNG")
    with (FILES / "数学练习.pdf").open("xb") as output:
        image.save(output, "PDF")
    book = Workbook()
    book.active.append(["题目", "答案"])
    book.active.append(["7 + 8", 15])
    book.active.append(["6 x 9", 54])
    with (FILES / "数学练习.xlsx").open("xb") as output:
        book.save(output)
    with zipfile.ZipFile(FILES / "数学练习.docx", "x") as archive:
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
    with (DATA / "source-fixtures-report.json").open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({"prepared": len(report), "real_model_calls": 0, "data": str(DATA)}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--files-dir", type=Path, default=FILES)
    arguments = parser.parse_args()
    DATA = arguments.data_dir.resolve()
    FILES = arguments.files_dir.resolve()
    main()
