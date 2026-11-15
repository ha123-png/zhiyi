from pathlib import Path
import json
import hashlib
import os
import sqlite3
import stat
import subprocess
import sys
from uuid import uuid4
import warnings
import zipfile

from alembic import command
from fastapi.testclient import TestClient
import pytest

from document_pipeline_api.business_backup import (
    BusinessBackupError,
    create_business_backup,
    inspect_business_backup,
    restore_business_backup,
)
from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.main import create_app
from document_pipeline_api.migrations import _alembic_config
from document_pipeline_api.models import TaskRecord
from image_test_data import PNG_BYTES


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'document-pipeline.db'}",
        storage_dir=tmp_path / "uploads",
    )


def _upload(client: TestClient, content: bytes = b"portable-original") -> str:
    content = PNG_BYTES + content
    response = client.post(
        "/api/v1/tasks",
        data={"template_mode": "smart"},
        files={"file": ("private-name.png", content, "image/png")},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_backup_is_portable_verified_and_excludes_runtime_secrets(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        task_id = _upload(client)
        with client.app.state.session_factory() as session:
            task = session.get(TaskRecord, task_id)
            assert task is not None
            assert task.storage_path == f"{task_id}.png"

    output = tmp_path / "exports" / "business.dpbak"
    created = create_business_backup(settings, output)
    manifest = inspect_business_backup(created)

    assert created == output.resolve()
    assert manifest["credentials_included"] is False
    assert manifest["files"] == [
        {
            "task_id": task_id,
            "archive_path": f"files/{task_id}.png",
            "size_bytes": len(PNG_BYTES + b"portable-original"),
            "sha256": manifest["files"][0]["sha256"],
            "content_type": "image/png",
        }
    ]
    assert str(tmp_path) not in str(manifest)
    with zipfile.ZipFile(created) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "database.db",
            f"files/{task_id}.png",
        }
        assert archive.read(f"files/{task_id}.png") == PNG_BYTES + b"portable-original"
        extracted_db = tmp_path / "snapshot.db"
        extracted_db.write_bytes(archive.read("database.db"))
    with sqlite3.connect(extracted_db) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT storage_path FROM tasks WHERE id = ?", (task_id,)
        ).fetchone() == (f"{task_id}.png",)


def test_missing_or_changed_original_aborts_without_partial_backup(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        task_id = _upload(client)
    (settings.storage_dir / f"{task_id}.png").write_bytes(b"changed")
    output = tmp_path / "broken.dpbak"

    with pytest.raises(BusinessBackupError, match="摘要"):
        create_business_backup(settings, output)

    assert not output.exists()
    assert not output.with_name(f"{output.name}.partial").exists()


def test_staged_migration_failure_preserves_live_data_and_reports_supervised_failure(tmp_path, monkeypatch):
    from document_pipeline_api import business_backup, supervisor

    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        task_id = _upload(client)
    original = settings.storage_dir / f"{task_id}.png"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    archive = create_business_backup(settings, backup_dir / "old.dpbak")
    database = tmp_path / "document-pipeline.db"
    before_db = hashlib.sha256(database.read_bytes()).hexdigest()
    before_original = original.read_bytes()

    def migration_failed(_engine):
        raise RuntimeError("synthetic staging migration failure")

    monkeypatch.setattr(business_backup, "upgrade_database", migration_failed)
    with pytest.raises(BusinessBackupError, match="备份数据库升级失败"):
        restore_business_backup(settings, archive, tmp_path)
    supervisor._perform_supervised_restore(tmp_path, archive.name)
    payload = json.loads((tmp_path / "runtime" / "restore-result.json").read_text(encoding="utf-8"))
    assert payload["state"] == "failed"
    assert "备份数据库升级失败" in payload["message"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before_db
    assert original.read_bytes() == before_original


def test_backup_inspection_rejects_path_traversal_and_duplicate_members(
    tmp_path: Path,
) -> None:
    traversal = tmp_path / "traversal.dpbak"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../outside", b"bad")
        archive.writestr("manifest.json", b"{}")
        archive.writestr("database.db", b"bad")
    with pytest.raises(BusinessBackupError, match="不安全路径"):
        inspect_business_backup(traversal)

    duplicate = tmp_path / "duplicate.dpbak"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr("manifest.json", b"{}")
            archive.writestr("database.db", b"bad")
    with pytest.raises(BusinessBackupError, match="重复成员"):
        inspect_business_backup(duplicate)


def test_backup_inspection_rejects_zip_symlink_member(tmp_path: Path) -> None:
    symlink = tmp_path / "symlink.dpbak"
    with zipfile.ZipFile(symlink, "w") as archive:
        info = zipfile.ZipInfo("outside-link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"../../outside")

    with pytest.raises(BusinessBackupError, match="符号链接"):
        inspect_business_backup(symlink)


def test_backup_inspection_enforces_aggregate_expanded_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import document_pipeline_api.business_backup as backup_module

    database = b"d" * 80
    manifest = {
        "format": backup_module.BACKUP_FORMAT,
        "credentials_included": False,
        "database": {
            "archive_path": "database.db",
            "size_bytes": len(database),
            "sha256": hashlib.sha256(database).hexdigest(),
        },
        "files": [],
    }
    manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    assert len(manifest_bytes) + len(database) > 256
    monkeypatch.setattr(backup_module, "MAX_BACKUP_CONTENT_BYTES", 256)
    oversized = tmp_path / "expanded-size-limit.dpbak"
    with zipfile.ZipFile(oversized, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("database.db", database)

    with pytest.raises(BusinessBackupError, match="总大小超过安全上限"):
        inspect_business_backup(oversized)


def test_backup_rejects_non_uuid_task_identity(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        _upload(client)
        with client.app.state.session_factory() as session:
            task = session.query(TaskRecord).one()
            task.id = "../unsafe"
            session.commit()
    with pytest.raises(BusinessBackupError, match="UUID"):
        create_business_backup(settings, tmp_path / f"{uuid4()}.dpbak")


def test_restore_relocates_files_and_preserves_previous_data_as_rollback(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_settings = _settings(source_dir)
    with TestClient(create_app(source_settings)) as client:
        source_task_id = _upload(client, b"source-business-file")
    archive = create_business_backup(source_settings, tmp_path / "portable.dpbak")

    target_dir = tmp_path / "target"
    target_settings = _settings(target_dir)
    with TestClient(create_app(target_settings)) as client:
        old_task_id = _upload(client, b"old-target-file")
    (target_dir / "queue.db").write_bytes(b"old-queue")
    (target_dir / "queue.db-wal").write_bytes(b"old-queue-wal")
    (target_dir / "queue.db-shm").write_bytes(b"old-queue-shm")

    rollback = restore_business_backup(target_settings, archive, target_dir)

    assert (rollback / "document-pipeline.db").is_file()
    assert (rollback / "uploads" / f"{old_task_id}.png").read_bytes() == (
        PNG_BYTES + b"old-target-file"
    )
    assert (rollback / "queue.db").read_bytes() == b"old-queue"
    assert (rollback / "queue.db-wal").read_bytes() == b"old-queue-wal"
    assert (rollback / "queue.db-shm").read_bytes() == b"old-queue-shm"
    assert not (target_dir / "queue.db").exists()
    assert not (target_dir / "queue.db-wal").exists()
    assert not (target_dir / "queue.db-shm").exists()
    assert (target_dir / "uploads" / f"{source_task_id}.png").read_bytes() == (
        PNG_BYTES + b"source-business-file"
    )
    with TestClient(create_app(target_settings)) as client:
        tasks = client.get("/api/v1/tasks").json()
        assert [task["id"] for task in tasks] == [source_task_id]
        original = client.get(f"/api/v1/tasks/{source_task_id}/file")
        assert original.status_code == 200
        assert original.content == PNG_BYTES + b"source-business-file"
        with client.app.state.session_factory() as session:
            restored = session.get(TaskRecord, source_task_id)
            assert restored is not None
            assert restored.storage_path == f"{source_task_id}.png"


def test_restore_disables_bindings_and_requires_new_export_confirmation(tmp_path: Path) -> None:
    from document_pipeline_api.schemas.file_export import TaskExportState
    source_settings = _settings(tmp_path / "source")
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "user-file.txt"
    sentinel.write_text("belongs to user", encoding="utf-8")
    with TestClient(create_app(source_settings)) as client:
        template = client.get("/api/v1/templates").json()[0]
        url = f"/api/v1/templates/{template['id']}/local-export"
        assert client.put(url, json={"expected_revision": 0, "enabled": True, "parent_path": str(external)}).status_code == 200
        task_id = _upload(client)
        with client.app.state.session_factory() as session:
            task = session.get(TaskRecord, task_id)
            task.export_state_json = TaskExportState(status="exporting", destination=str(external / "发票"), binding_revision=1).model_dump_json()
            session.commit()
    archive = create_business_backup(source_settings, tmp_path / "portable.dpbak")
    target_dir = tmp_path / "target"
    target_settings = _settings(target_dir)
    with TestClient(create_app(target_settings)):
        pass
    restore_business_backup(target_settings, archive, target_dir)
    with TestClient(create_app(target_settings)) as client:
        binding = client.get(url).json()
        assert binding["enabled"] is False
        assert 2 < binding["revision"] < 2**53
        with client.app.state.session_factory() as session:
            state = session.get(TaskRecord, task_id).file_export
            assert state.status == "needs_rebind"
            assert state.destination == str(external / "发票")
            assert state.error_code == "backup_restored"
    assert list(external.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "belongs to user"


def test_restore_install_failure_rolls_back_exact_previous_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import document_pipeline_api.business_backup as backup_module

    source_dir = tmp_path / "source"
    source_settings = _settings(source_dir)
    with TestClient(create_app(source_settings)) as client:
        _upload(client, b"source")
    archive = create_business_backup(source_settings, tmp_path / "restore-fail.dpbak")

    target_dir = tmp_path / "target"
    target_settings = _settings(target_dir)
    with TestClient(create_app(target_settings)) as client:
        old_task_id = _upload(client, b"old-still-safe")
    (target_dir / "queue.db").write_bytes(b"old-queue")
    real_move = backup_module._move_path
    move_count = 0

    def fail_once_on_stage_upload(source: Path, destination: Path) -> None:
        nonlocal move_count
        move_count += 1
        if move_count == 5:
            raise OSError("injected restore interruption")
        real_move(source, destination)

    monkeypatch.setattr(backup_module, "_move_path", fail_once_on_stage_upload)
    with pytest.raises(BusinessBackupError, match="已回滚"):
        restore_business_backup(target_settings, archive, target_dir)

    assert (target_dir / "queue.db").read_bytes() == b"old-queue"
    assert (target_dir / "uploads" / f"{old_task_id}.png").read_bytes() == (
        PNG_BYTES + b"old-still-safe"
    )
    with sqlite3.connect(target_dir / "document-pipeline.db") as connection:
        assert connection.execute("SELECT id FROM tasks").fetchone() == (old_task_id,)


def test_hard_exit_after_database_install_recovers_and_retains_staging_with_note(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_settings = _settings(source_dir)
    with TestClient(create_app(source_settings)) as client:
        new_task_id = _upload(client, b"new-generation")
    archive = create_business_backup(source_settings, tmp_path / "new-generation.dpbak")

    target_dir = tmp_path / "target"
    target_settings = _settings(target_dir)
    with TestClient(create_app(target_settings)) as client:
        old_task_id = _upload(client, b"old-generation")
    old_original = (target_settings.storage_dir / f"{old_task_id}.png").read_bytes()

    child_code = r"""
import os
import sys
from pathlib import Path

from document_pipeline_api.config import Settings
import document_pipeline_api.business_backup as backup

data_dir = Path(sys.argv[1])
archive = Path(sys.argv[2])
settings = Settings(
    database_url=f"sqlite:///{data_dir / 'document-pipeline.db'}",
    storage_dir=data_dir / "uploads",
    queue_enabled=False,
)
move_path = backup._move_path

def stop_after_installing_new_database(source, destination):
    move_path(source, destination)
    if source.name == "database.db" and destination == data_dir / "document-pipeline.db":
        os._exit(73)

backup._move_path = stop_after_installing_new_database
backup.restore_business_backup(settings, archive, data_dir)
os._exit(74)
"""
    api_dir = Path(__file__).resolve().parents[1]
    child_tmp = tmp_path / "child-tmp"
    child_tmp.mkdir()
    child_env = os.environ.copy()
    child_env["DOCUMENT_PIPELINE_DATA_DIR"] = str(tmp_path / "child-default-data")
    child_env["PYTHONPATH"] = os.pathsep.join(
        [str(api_dir / "src"), str(api_dir / "tests"), child_env.get("PYTHONPATH", "")]
    )
    child_env["TMP"] = child_env["TEMP"] = str(child_tmp)
    stopped = subprocess.run(
        [sys.executable, "-c", child_code, str(target_dir), str(archive)],
        cwd=api_dir,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert stopped.returncode == 73, stopped.stderr[-2000:]

    journal = target_dir / "runtime" / "restore-pending.json"
    state = json.loads(journal.read_text(encoding="utf-8"))
    staging = target_dir / "runtime" / state["staging"]
    staged_original = staging / "uploads" / f"{new_task_id}.png"
    assert staged_original.read_bytes() == PNG_BYTES + b"new-generation"

    with TestClient(create_app(target_settings)) as client:
        tasks = client.get("/api/v1/tasks").json()
        assert [task["id"] for task in tasks] == [old_task_id]
        assert client.get(f"/api/v1/tasks/{old_task_id}/file").content == old_original

    assert not journal.exists()
    assert not staging.exists()
    rollback = target_dir / "backups" / state["rollback"]
    preserved_original = rollback / "interrupted-staging" / "uploads" / f"{new_task_id}.png"
    assert preserved_original.read_bytes() == PNG_BYTES + b"new-generation"
    note = json.loads((rollback / "restore-recovery.json").read_text(encoding="utf-8"))
    assert note["state"] == "previous_data_restored"
    assert "仍占用磁盘空间" in note["explanation"]


def test_restore_preserves_unsafe_integer_exactly_and_fences_old_row_version(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        from document_pipeline_api.services.data_tables import create_table_from_import

        with client.app.state.session_factory() as session:
            table = create_table_from_import(
                session, "精确整数恢复", [{"金额": 9007199254740993}],
            )
            table_id = table.id
        table_url = f"/api/v1/tables/{table_id}"
        before = client.get(table_url).json()["rows"][0]
        assert before["values"]["import_1"] == "9007199254740993"
        archive = create_business_backup(settings, tmp_path / "exact-integer.dpbak")
        changed = client.patch(
            f"{table_url}/rows/{before['id']}",
            json={
                "expected_version": before["version"],
                "changes": {"import_1": "9007199254740995"},
            },
        )
        assert changed.status_code == 200, changed.text

    restore_business_backup(settings, archive, tmp_path)
    with TestClient(create_app(settings)) as client:
        restored = client.get(table_url).json()["rows"][0]
        assert restored["values"]["import_1"] == "9007199254740993"
        assert restored["version"] > before["version"]


def test_restore_upgrades_a_portable_0009_database_before_install(tmp_path: Path) -> None:
    source_dir = tmp_path / "old-source"
    source_settings = _settings(source_dir)
    source_dir.mkdir()
    source_settings.storage_dir.mkdir()
    task_id = str(uuid4())
    original = b"old-version-portable-file"
    original_path = source_settings.storage_dir / f"{task_id}.png"
    original_path.write_bytes(original)
    import hashlib

    digest = hashlib.sha256(original).hexdigest()
    engine = build_engine(source_settings.database_url)
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0009_extraction_evidence")
        connection.exec_driver_sql(
            """
            INSERT INTO tasks (
                id, filename, content_type, size_bytes, page_count, sha256,
                storage_path, template_mode, status, attempt_count,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                task_id,
                "old-private-name.png",
                "image/png",
                len(original),
                1,
                digest,
                # Legacy schema import remains testable with an explicitly
                # managed reference; live backup never follows absolute paths.
                f"{task_id}.png",
                "smart",
                "queued",
                0,
            ),
        )
    engine.dispose()
    archive = create_business_backup(source_settings, tmp_path / "old.dpbak")

    target_dir = tmp_path / "new-target"
    target_settings = _settings(target_dir)
    restore_business_backup(target_settings, archive, target_dir)

    with sqlite3.connect(target_dir / "document-pipeline.db") as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "0047_live_table_merge",
        )
        assert connection.execute(
            "SELECT storage_path FROM tasks WHERE id = ?", (task_id,)
        ).fetchone() == (f"{task_id}.png",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert (target_dir / "uploads" / f"{task_id}.png").read_bytes() == original


def test_backup_preserves_selected_template_version_and_restoration_log(tmp_path: Path):
    settings = _settings(tmp_path / "source")
    with TestClient(create_app(settings)) as client:
        body = {"name": "可恢复模板", "fields": [{"key": "title", "label": "标题"}]}
        template = client.post("/api/v1/templates", json=body).json()
        url = f"/api/v1/templates/{template['id']}"
        assert client.put(url, json={**body, "description": "第二版", "expected_version": 1}).status_code == 200
        assert client.post(f"{url}/versions/1/restore", json={"expected_version": 2}).status_code == 200
        log = client.get(f"{url}/restorations").json()
    archive = create_business_backup(settings, tmp_path / "restore-history.dpbak")
    destination = tmp_path / "destination"
    target = _settings(destination)
    restore_business_backup(target, archive, destination)
    with TestClient(create_app(target)) as client:
        assert client.get(url).json()["version"] == 1
        assert client.get(f"{url}/restorations").json() == log
        assert [v["version"] for v in client.get(f"{url}/versions").json()] == [2, 1]
