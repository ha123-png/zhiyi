"""Create deterministic, isolated browser fixtures for the v0.6.0 deep data audit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.business_backup import inspect_business_backup


class OfflineFixtureModel:
    """A deterministic local model substitute; it never calls a network service."""

    model_name = "v0.6.0 深度数据审计合成夹具"

    def complete_text(self, prompt, result_type):
        marker = prompt.split("FIXTURE:", 1)[-1].splitlines()[0].strip()
        return result_type.model_validate({
            "header": {"title": marker, "subject": "隔离合成历史"},
            "items": [{"question": f"{marker} 问题", "answer": "受控答案", "reason": "本地夹具"}],
        })

    def extract_images(self, images, prompt, result_type):
        return self.complete_text(prompt, result_type)

    def extract_image(self, image, prompt, result_type):
        return self.complete_text(prompt, result_type)


class OfflineFileCollectionModel:
    """Fixture-only result for autonomous per-file collections; no network calls."""

    model_name = "v0.6.0 深度数据来源卡片合成夹具"

    def complete_text(self, prompt, result_type):
        marker = prompt.split("FIXTURE:", 1)[-1].splitlines()[0].strip()
        match = marker.rsplit("-", 1)[-1]
        return result_type.model_validate({
            "fields": [
                {"key": "source_code", "label": "来源编号", "section": "header", "value_type": "text"},
                {"key": "group", "label": "类别", "section": "header", "value_type": "text"},
                {"key": "record_code", "label": "记录号", "section": "item", "value_type": "text"},
                {"key": "amount", "label": "金额", "section": "item", "value_type": "number"},
            ],
            "header": {"source_code": marker, "group": "Group-" + ("A" if int(match) % 2 else "B")},
            "items": [{"record_code": f"{marker}-ROW", "amount": int(match)}],
        })

    def extract_images(self, images, prompt, result_type):
        return self.complete_text(prompt, result_type)

    def extract_image(self, image, prompt, result_type):
        return self.complete_text(prompt, result_type)


def write_import_workbook(path: Path) -> None:
    workbook = Workbook()
    orders = workbook.active
    orders.title = "订单"
    orders.append(["记录号", "金额", "有效", "备注"])
    orders.append(["DD-XLSX-ORDER-01", 0, False, "Excel 导入首行"])
    orders.append(["DD-XLSX-ORDER-02", 12.5, True, "含换行\n的文字"])
    inventory = workbook.create_sheet("库存")
    inventory.append(["记录号", "金额", "有效", "备注"])
    inventory.append(["DD-XLSX-STOCK-01", 7, True, "另一个业务工作表"])
    inventory.append(["DD-XLSX-STOCK-02", 3, False, "替代 Sheet 哨兵"])
    workbook.create_sheet("空白")
    with path.open("xb") as output:
        workbook.save(output)


def seed_history(data_dir: Path, files_dir: Path, report_path: Path) -> dict:
    filenames = [f"DD-HISTORY-{index:02d}.txt" for index in range(1, 13)]
    for path in [report_path, *(files_dir / name for name in filenames)]:
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
    settings = Settings(
        database_url=f"sqlite:///{(data_dir / 'document-pipeline.db').resolve()}",
        storage_dir=(data_dir / "uploads").resolve(),
        queue_enabled=False,
    )
    files_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"real_model_calls": 0, "tasks": []}
    with TestClient(create_app(settings)) as client:
        def req(method: str, route: str, **kwargs):
            response = client.request(method, "/api/v1" + route, **kwargs)
            response.raise_for_status()
            return response

        existing = {task["filename"] for task in req("GET", "/tasks").json()}
        conflicts = existing.intersection(filenames)
        if conflicts:
            raise RuntimeError(f"Fresh deep-data fixture directory unexpectedly contains {sorted(conflicts)}")

        template_name = "深度数据历史分页夹具"
        templates = req("GET", "/templates").json()
        template = next((item for item in templates if item["name"] == template_name), None)
        if template is None:
            template = req("POST", "/templates", json={
                "name": template_name,
                "description": "仅供隔离深度交互审计的离线合成记录。",
                "extra_instructions": "",
                "fields": [
                    {"key": "title", "label": "标题", "section": "header", "value_type": "text"},
                    {"key": "subject", "label": "科目", "section": "header", "value_type": "text"},
                    {"key": "question", "label": "问题", "section": "item", "value_type": "text"},
                    {"key": "answer", "label": "答案", "section": "item", "value_type": "text"},
                    {"key": "reason", "label": "说明", "section": "item", "value_type": "text"},
                ],
                "validation_rules": [],
                "deterministic_rules": [],
                "output_mapping": {},
            }).json()

        for index in range(1, 13):
            marker = f"DD-HISTORY-{index:02d}"
            filename = f"{marker}.txt"
            original = f"FIXTURE: {marker}\n隔离合成文件 {index}。\n".encode("utf-8")
            file_path = files_dir / filename
            with file_path.open("xb") as output:
                output.write(original)
            digest = hashlib.sha256(original).hexdigest()
            task = req("POST", "/tasks", files={"file": (filename, original, "text/plain")}, data={"template_id": template["id"]}).json()
            with client.app.state.session_factory() as session:
                extraction = process_task(session, settings, task["id"], client=OfflineFixtureModel())
            if extraction is None:
                raise RuntimeError(f"Synthetic extraction produced no result for {filename}")
            req("PUT", f"/tasks/{task['id']}/review", json={
                "expected_version": 0,
                "result": extraction.result.model_dump(),
            })
            downloaded = req("GET", f"/tasks/{task['id']}/file").content
            if hashlib.sha256(downloaded).hexdigest() != digest or downloaded != original:
                raise RuntimeError(f"Original fixture did not round-trip byte-for-byte: {filename}")
            report["tasks"].append({
                "task_id": task["id"],
                "filename": filename,
                "sha256": digest,
                "size_bytes": len(original),
                "template_id": template["id"],
            })

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    return report


def seed_file_collection(data_dir: Path, files_dir: Path, report_path: Path) -> dict:
    filenames = [f"DD-SOURCE-{index:02d}.txt" for index in range(1, 9)]
    for path in [report_path, *(files_dir / name for name in filenames)]:
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
    settings = Settings(
        database_url=f"sqlite:///{(data_dir / 'document-pipeline.db').resolve()}",
        storage_dir=(data_dir / "uploads").resolve(),
        queue_enabled=False,
    )
    files_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"real_model_calls": 0, "template_id": "builtin-autonomous", "tasks": []}
    with TestClient(create_app(settings)) as client:
        def req(method, route, **kwargs):
            response = client.request(method, "/api/v1" + route, **kwargs)
            response.raise_for_status()
            return response

        templates = req("GET", "/templates").json()
        autonomous = next((item for item in templates if item["engine_kind"] == "autonomous"), None)
        if autonomous is None or autonomous["id"] != report["template_id"]:
            raise RuntimeError("The isolated app did not expose the autonomous fixture template.")
        existing_names = {item["filename"] for item in req("GET", "/tasks?limit=1000").json()}
        conflicts = existing_names.intersection(filenames)
        if conflicts:
            raise RuntimeError(f"The isolated file-collection checkpoint already contains {sorted(conflicts)}")
        for index in range(1, 9):
            marker = f"DD-SOURCE-{index:02d}"
            filename = f"{marker}.txt"
            original = f"FIXTURE: {marker}\n隔离来源卡片 {index}；金额 {index}。\n".encode("utf-8")
            task = req("POST", "/tasks", files={"file": (filename, original, "text/plain")},
                       data={"template_id": autonomous["id"]}).json()
            with client.app.state.session_factory() as session:
                extraction = process_task(session, settings, task["id"], client=OfflineFileCollectionModel())
            if extraction is None:
                raise RuntimeError(f"Synthetic autonomous extraction produced no result for {filename}")
            downloaded = req("GET", f"/tasks/{task['id']}/file").content
            digest = hashlib.sha256(original).hexdigest()
            if downloaded != original or hashlib.sha256(downloaded).hexdigest() != digest:
                raise RuntimeError(f"Original source fixture did not round-trip byte-for-byte: {filename}")
            with (files_dir / filename).open("xb") as output:
                output.write(original)
            report["tasks"].append({"task_id": task["id"], "filename": filename,
                                    "sha256": digest, "size_bytes": len(original),
                                    "source_code": marker, "real_model_calls": 0})
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    return report


def inspect_backup(path: Path) -> dict:
    manifest = inspect_business_backup(path)
    return {
        "valid_archive_and_hashes": True,
        "format": manifest["format"],
        "database": manifest["database"],
        "file_count": len(manifest["files"]),
    }


def inspect_xlsx(path: Path) -> dict:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = []
        for sheet in workbook.worksheets:
            rows = [[cell for cell in row] for row in sheet.iter_rows(values_only=True)]
            sheets.append({"name": sheet.title, "rows": rows})
        return {"readable": True, "sheets": sheets}
    finally:
        workbook.close()


def write_business_invalid_backup(source: Path, destination: Path) -> dict:
    """Keep a valid ZIP and member digest while corrupting task-facing DB JSON."""
    if destination.exists():
        raise FileExistsError(destination)
    with zipfile.ZipFile(source, "r") as archive:
        members = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}
    manifest = json.loads(members["manifest.json"][1].decode("utf-8"))
    database_bytes = members["database.db"][1]
    # A snapshot copied from WAL can retain its WAL header, which SQLite cannot
    # deserialize directly into :memory:.  Own a fresh scratch directory so
    # SQLite can normalize it without touching old .work.db or sidecar files.
    with tempfile.TemporaryDirectory(prefix=f".{destination.name}.work-", dir=destination.parent) as scratch:
        work_db = Path(scratch) / "database.db"
        with work_db.open("xb") as output:
            output.write(database_bytes)
        connection = sqlite3.connect(work_db)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            row = connection.execute("SELECT id FROM tasks ORDER BY id LIMIT 1").fetchone()
            if row is None:
                raise RuntimeError("The isolated fixture database has no task for the corruption oracle.")
            connection.execute("UPDATE tasks SET file_name_json = ? WHERE id = ?", ("{", row[0]))
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError("The test mutation damaged SQLite structure rather than business JSON.")
        finally:
            connection.close()
        changed_database = work_db.read_bytes()

    manifest["database"]["size_bytes"] = len(changed_database)
    manifest["database"]["sha256"] = hashlib.sha256(changed_database).hexdigest()
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (info, payload) in members.items():
            if name == "manifest.json":
                continue
            archive.writestr(info, changed_database if name == "database.db" else payload)
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )
    # Verify the archive-level member digests before the browser attempts restore.
    verified = inspect_business_backup(destination)
    return {
        "valid_archive_and_hashes": True,
        "invalid_task_business_json": True,
        "format": verified["format"],
        "file_count": len(verified["files"]),
        "size_bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def check_integrity(data_dir: Path) -> dict:
    database = data_dir / "document-pipeline.db"
    connection = sqlite3.connect(database)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        row_count = connection.execute("SELECT COUNT(*) FROM data_rows").fetchone()[0]
        dump = "\n".join(connection.iterdump()).encode("utf-8")
    finally:
        connection.close()
    uploads_root = data_dir / "uploads"
    upload_manifest = []
    if uploads_root.exists():
        for path in sorted(uploads_root.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"Refusing symbolic link in isolated upload store: {path}")
            if path.is_file():
                payload = path.read_bytes()
                upload_manifest.append({
                    "path": path.relative_to(uploads_root).as_posix(),
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                })
    return {
        "integrity_check": result[0] if result else None,
        "task_count": task_count,
        "row_count": row_count,
        "database_logical_sha256": hashlib.sha256(dump).hexdigest(),
        "upload_file_count": len(upload_manifest),
        "uploads_files": upload_manifest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--files-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--inspect-backup", type=Path)
    parser.add_argument("--inspect-xlsx", type=Path)
    parser.add_argument("--corrupt-copy-from", type=Path)
    parser.add_argument("--output-backup", type=Path)
    parser.add_argument("--check-integrity", action="store_true")
    parser.add_argument("--seed-file-collection", action="store_true")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    files_dir = args.files_dir.resolve()
    report_path = args.report.resolve() if args.report else None
    project = Path(__file__).resolve().parents[1]
    paths = [data_dir, files_dir]
    paths.extend(path.resolve() for path in (args.report, args.inspect_backup, args.inspect_xlsx, args.corrupt_copy_from, args.output_backup) if path)
    for path in paths:
        if not path.is_relative_to(project / ".local"):
            parser.error("Deep-data fixtures and reports must stay inside .local.")
    if args.inspect_backup:
        print(json.dumps(inspect_backup(args.inspect_backup.resolve()), ensure_ascii=False))
        return
    if args.inspect_xlsx:
        print(json.dumps(inspect_xlsx(args.inspect_xlsx.resolve()), ensure_ascii=False))
        return
    if args.corrupt_copy_from:
        if not args.output_backup:
            parser.error("--corrupt-copy-from requires --output-backup.")
        print(json.dumps(write_business_invalid_backup(args.corrupt_copy_from.resolve(), args.output_backup.resolve()), ensure_ascii=False))
        return
    if args.check_integrity:
        print(json.dumps(check_integrity(data_dir), ensure_ascii=False))
        return
    if args.seed_file_collection:
        if not args.report:
            parser.error("--seed-file-collection requires --report.")
        report_path = args.report.resolve()
        print(json.dumps(seed_file_collection(data_dir, files_dir, report_path), ensure_ascii=False))
        return
    if not args.report:
        parser.error("--report is required when preparing fixtures.")
    report_path = args.report.resolve()
    outputs = [report_path, files_dir / "DD-multi-sheet-import.xlsx"]
    outputs.extend(files_dir / f"DD-HISTORY-{index:02d}.txt" for index in range(1, 13))
    for path in outputs:
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
    data_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)
    write_import_workbook(files_dir / "DD-multi-sheet-import.xlsx")
    print(json.dumps(seed_history(data_dir, files_dir, report_path), ensure_ascii=False))


if __name__ == "__main__":
    main()
