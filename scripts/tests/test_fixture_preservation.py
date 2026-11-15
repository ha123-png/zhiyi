"""Synthetic preservation checks for reruns of local acceptance helpers."""

import importlib.util
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing, nullcontext
from types import SimpleNamespace
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_deep_data():
    script = ROOT / "scripts/prepare-v060-deep-data.py"
    spec = importlib.util.spec_from_file_location("deep_data_preservation", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_helper(tmp_path, name):
    workspace = tmp_path / "workspace"
    script = workspace / "scripts" / name
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / name, script)
    return workspace, script


def run_until_first_fixture_write(workspace, script, target, args, method):
    harness = r'''
import runpy, sys
from pathlib import Path
script, target, method, *args = sys.argv[1:]
original = getattr(Path, method)
def probe(self, *values, **kwargs):
    if self == Path(target):
        original(self, *values, **kwargs)
        raise RuntimeError("stopped after synthetic sentinel was touched")
    return original(self, *values, **kwargs)
setattr(Path, method, probe)
sys.argv = [script, *args]
runpy.run_path(script, run_name="__main__")
'''
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(ROOT / "apps/api/src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-c", harness, str(script), str(target), method, *args],
                          cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_preview_rejects_existing_fixture_before_touching_it(tmp_path):
    workspace, script = copy_helper(tmp_path, "prepare-preview-acceptance.py")
    run = workspace / ".local/run"
    target = run / "fixtures/pdf-10.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing preview sentinel")
    result = run_until_first_fixture_write(workspace, script, target, ["--root", str(run)], "write_bytes")
    assert result.returncode != 0
    error_line = result.stderr.strip().splitlines()[-1]
    assert error_line.startswith("FileExistsError:"), result.stderr
    assert repr(str(run)) in error_line, result.stderr
    assert target.read_bytes() == b"existing preview sentinel"


@pytest.mark.parametrize("outside", [False, True])
def test_source_fixture_refuses_untrusted_existing_file_or_outside_workspace(tmp_path, outside):
    workspace, script = copy_helper(tmp_path, "prepare-source-acceptance.py")
    base = tmp_path / "outside" if outside else workspace / ".local/run"
    target = base / "files/数学练习.txt"
    target.parent.mkdir(parents=True)
    target.write_text("existing synthetic sentinel", encoding="utf-8")
    result = run_until_first_fixture_write(workspace, script, target,
        ["--data-dir", str(base / "data"), "--files-dir", str(base / "files")], "write_text")
    assert result.returncode != 0
    reason = ("Source-fixture directories must stay inside this workspace .local" if outside
              else "Refusing incomplete or linked source-fixture checkpoint")
    assert result.stderr.strip().splitlines()[-1] == f"RuntimeError: {reason}", result.stderr
    assert target.read_text(encoding="utf-8") == "existing synthetic sentinel"


def build_source_checkpoint(data, files, stored_state="valid"):
    data.mkdir(parents=True)
    files.mkdir(parents=True)
    names = ["数学练习.txt", "同名/数学练习.txt", "数学练习.png", "数学练习.pdf", "数学练习.docx", "数学练习.xlsx"]
    mime = ["text/plain", "text/plain", "image/png", "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]
    report = []
    with closing(sqlite3.connect(data / "document-pipeline.db")) as connection, connection:
        connection.execute("CREATE TABLE templates (id TEXT PRIMARY KEY, name TEXT)")
        connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, filename TEXT, sha256 TEXT, template_id TEXT, "
                           "content_type TEXT, size_bytes INTEGER, storage_path TEXT, internal_storage_json TEXT)")
        connection.execute("INSERT INTO templates VALUES (?, ?)", ("template", "原件与阅读验收（模拟输出）"))
        for index, name in enumerate(names):
            path = files / name
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = f"checkpoint-{index}".encode()
            path.write_bytes(payload)
            task_id = f"task-{index}"
            storage_name = f"{task_id}{path.suffix}"
            original = data / "uploads" / storage_name
            original.parent.mkdir(parents=True, exist_ok=True)
            if index != 2 or stored_state != "missing":
                original.write_bytes(b"changed original" if index == 2 and stored_state == "changed" else payload)
            connection.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, path.name, hashlib.sha256(payload).hexdigest(), "template", mime[index], len(payload), storage_name, None))
            report.append({"task_id": task_id, "file": path.name, "original_verified": True})
    (data / "source-fixtures-report.json").write_text(json.dumps(report), encoding="utf-8")
    return names


@pytest.mark.parametrize("case", ["checkpoint", "moving", "corrupt_delete", "corrupt_wal"])
def test_fixture_connections_are_explicitly_closed(tmp_path, monkeypatch, case):
    connections = []
    connect = sqlite3.connect
    class TrackedConnection(sqlite3.Connection):
        explicitly_closed = False
        def close(self):
            self.explicitly_closed = True
            super().close()
    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs, factory=TrackedConnection)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    try:
        if case == "checkpoint":
            build_source_checkpoint(tmp_path / "data", tmp_path / "files")
        elif case == "moving":
            test_source_checkpoint_reuses_verified_previous_original_during_move(tmp_path, monkeypatch)
        else:
            test_corrupt_backup_does_not_touch_existing_work_database_or_sidecars(
                tmp_path, "WAL" if case == "corrupt_wal" else "DELETE")
        assert connections
        assert all(connection.explicitly_closed for connection in connections)
    finally:
        for connection in connections:
            if not connection.explicitly_closed:
                connection.close()


@pytest.mark.parametrize("case", ["preview", "source_existing", "source_outside", "protocol_existing"])
def test_preservation_refusals_reject_unrelated_child_failure(tmp_path, monkeypatch, case):
    copy = copy_helper
    def copy_with_import_failure(*args):
        workspace, script = copy(*args)
        assert script.resolve().is_relative_to(tmp_path.resolve())
        script.write_text('raise ImportError("synthetic unrelated child import failure")\n', encoding="utf-8")
        return workspace, script
    monkeypatch.setitem(globals(), "copy_helper", copy_with_import_failure)
    with pytest.raises(AssertionError, match="synthetic unrelated child import failure"):
        if case == "preview":
            test_preview_rejects_existing_fixture_before_touching_it(tmp_path)
        elif case.startswith("source_"):
            test_source_fixture_refuses_untrusted_existing_file_or_outside_workspace(
                tmp_path, case == "source_outside")
        else:
            test_protocol_existing_run_does_not_overwrite_old_evidence(tmp_path)


@pytest.mark.parametrize("tampered", [False, True])
def test_source_complete_checkpoint_reused_only_when_report_and_bytes_match(tmp_path, tampered):
    workspace, script = copy_helper(tmp_path, "prepare-source-acceptance.py")
    data, files = workspace / ".local/run/data", workspace / ".local/run/files"
    names = build_source_checkpoint(data, files)
    if tampered:
        (files / names[2]).write_bytes(b"changed after checkpoint")
    tracked = [data / "document-pipeline.db", data / "source-fixtures-report.json", *(files / name for name in names)]
    before = [path.read_bytes() for path in tracked]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(ROOT / "apps/api/src")
    result = subprocess.run([sys.executable, str(script), "--data-dir", str(data), "--files-dir", str(files)],
                            cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert (result.returncode != 0) == tampered, result.stderr
    if not tampered:
        assert json.loads(result.stdout)["reused_checkpoint"] is True
    assert [path.read_bytes() for path in tracked] == before


@pytest.mark.parametrize("stored_state", ["missing", "changed"])
def test_source_checkpoint_rejects_unavailable_or_changed_managed_original(tmp_path, stored_state):
    workspace, script = copy_helper(tmp_path, "prepare-source-acceptance.py")
    data, files = workspace / ".local/run/data", workspace / ".local/run/files"
    build_source_checkpoint(data, files, stored_state)
    before = {path.relative_to(data): path.read_bytes() for path in data.rglob("*") if path.is_file()}
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONPATH=str(ROOT / "apps/api/src"))
    result = subprocess.run([sys.executable, str(script), "--data-dir", str(data), "--files-dir", str(files)],
                            cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode != 0
    assert {path.relative_to(data): path.read_bytes() for path in data.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
@pytest.mark.parametrize("predicate", ["is_symlink", "is_junction"])
def test_source_checkpoint_rejects_linked_sqlite_sidecars(tmp_path, monkeypatch, suffix, predicate):
    module = load_script("source_checkpoint_sidecar", ROOT / "scripts/prepare-source-acceptance.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / ".local/run/data")
    monkeypatch.setattr(module, "FILES", tmp_path / ".local/run/files")
    build_source_checkpoint(module.DATA, module.FILES)
    target = module.DATA / ("document-pipeline.db" + suffix)
    original = getattr(Path, predicate)
    monkeypatch.setattr(Path, predicate, lambda path: path == target or original(path))
    with pytest.raises(RuntimeError):
        module.main()


def test_source_checkpoint_reuses_verified_previous_original_during_move(tmp_path, monkeypatch):
    module = load_script("source_moving_checkpoint", ROOT / "scripts/prepare-source-acceptance.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / ".local/run/data")
    monkeypatch.setattr(module, "FILES", tmp_path / ".local/run/files")
    build_source_checkpoint(module.DATA, module.FILES)
    with closing(sqlite3.connect(module.DATA / "document-pipeline.db")) as connection, connection:
        connection.execute("UPDATE tasks SET storage_path = ?, internal_storage_json = ? WHERE id = ?",
                           ("templates/fixture/task-2.png", json.dumps({"previous_path": "task-2.png"}), "task-2"))
    before = {path.relative_to(module.DATA): path.read_bytes() for path in module.DATA.rglob("*") if path.is_file()}
    module.main()
    assert {path.relative_to(module.DATA): path.read_bytes() for path in module.DATA.rglob("*") if path.is_file()} == before


def test_source_checkpoint_reads_wal_without_mutating_original_files(tmp_path, monkeypatch):
    module = load_script("source_wal_checkpoint", ROOT / "scripts/prepare-source-acceptance.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / ".local/run/data")
    monkeypatch.setattr(module, "FILES", tmp_path / ".local/run/files")
    build_source_checkpoint(module.DATA, module.FILES)
    connection = sqlite3.connect(module.DATA / "document-pipeline.db")
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        connection.execute("UPDATE tasks SET filename = filename WHERE id = 'task-0'")
        connection.commit()
        before = {path.relative_to(module.DATA): path.read_bytes() for path in module.DATA.rglob("*") if path.is_file()}
        assert Path("document-pipeline.db-wal") in before
        module.main()
        assert {path.relative_to(module.DATA): path.read_bytes() for path in module.DATA.rglob("*") if path.is_file()} == before
    finally:
        connection.close()


def test_source_checkpoint_reads_closed_wal_database_without_new_sidecars(tmp_path, monkeypatch):
    module = load_script("source_closed_wal_checkpoint", ROOT / "scripts/prepare-source-acceptance.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / ".local/run/data")
    monkeypatch.setattr(module, "FILES", tmp_path / ".local/run/files")
    build_source_checkpoint(module.DATA, module.FILES)
    connection = sqlite3.connect(module.DATA / "document-pipeline.db")
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        connection.execute("UPDATE tasks SET filename = filename WHERE id = 'task-0'")
        connection.commit()
    finally:
        connection.close()
    before = {path.relative_to(module.DATA): path.read_bytes() for path in module.DATA.rglob("*") if path.is_file()}
    assert Path("document-pipeline.db-wal") not in before
    module.main()
    assert {path.relative_to(module.DATA): path.read_bytes() for path in module.DATA.rglob("*") if path.is_file()} == before


def test_source_checkpoint_rejects_inconsistent_database_copy(tmp_path, monkeypatch):
    module = load_script("source_copy_guard", ROOT / "scripts/prepare-source-acceptance.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / ".local/run/data")
    monkeypatch.setattr(module, "FILES", tmp_path / ".local/run/files")
    build_source_checkpoint(module.DATA, module.FILES)
    before = {path.relative_to(module.DATA): path.read_bytes() for path in module.DATA.rglob("*") if path.is_file()}
    copy = module.shutil.copyfileobj
    def interrupted_copy(source, target):
        copy(source, target)
        target.write(b"synthetic mismatch")
    monkeypatch.setattr(module.shutil, "copyfileobj", interrupted_copy)
    with pytest.raises(RuntimeError, match="unverified"):
        module.main()
    assert {path.relative_to(module.DATA): path.read_bytes() for path in module.DATA.rglob("*") if path.is_file()} == before


def test_source_fresh_fixture_outputs_are_created_without_a_service(tmp_path, monkeypatch):
    module = load_script("source_fixture_preservation", ROOT / "scripts/prepare-source-acceptance.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / ".local/run/data")
    monkeypatch.setattr(module, "FILES", tmp_path / ".local/run/files")
    monkeypatch.setattr(module, "create_app", lambda _: object())
    class StopBeforeService(Exception):
        pass
    def stop(_):
        raise StopBeforeService()
    monkeypatch.setattr(module, "TestClient", stop)
    with pytest.raises(StopBeforeService):
        module.main()
    files = [module.FILES / name for name in module.FIXTURE_NAMES]
    assert all(path.is_file() and path.stat().st_size for path in files)
    assert files[2].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert files[3].read_bytes().startswith(b"%PDF")
    assert zipfile.is_zipfile(files[4]) and zipfile.is_zipfile(files[5])


def test_source_refuses_nonempty_data_without_complete_checkpoint(tmp_path, monkeypatch):
    module = load_script("source_untrusted_data", ROOT / "scripts/prepare-source-acceptance.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / ".local/run/data")
    monkeypatch.setattr(module, "FILES", tmp_path / ".local/run/files")
    module.DATA.mkdir(parents=True)
    sentinel = module.DATA / "unrelated.txt"
    sentinel.write_bytes(b"preserve unrelated data")
    with pytest.raises(RuntimeError, match="non-empty"):
        module.main()
    assert sentinel.read_bytes() == b"preserve unrelated data"
    assert not module.FILES.exists()


def test_source_rejects_nested_junction_before_first_fixture(tmp_path, monkeypatch):
    module = load_script("source_junction_guard", ROOT / "scripts/prepare-source-acceptance.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / ".local/run/data")
    monkeypatch.setattr(module, "FILES", tmp_path / ".local/run/files")
    nested = module.FILES / "同名"
    nested.mkdir(parents=True)
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == nested or original(path))
    with pytest.raises(RuntimeError, match="linked"):
        module.main()
    assert not (module.FILES / "数学练习.txt").exists()


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_corrupt_backup_does_not_touch_existing_work_database_or_sidecars(tmp_path, journal_mode):
    module = load_deep_data()
    original_db = tmp_path / "source.sqlite"
    with closing(sqlite3.connect(original_db)) as connection, connection:
        connection.execute(f"PRAGMA journal_mode={journal_mode}")
        connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, file_name_json TEXT)")
        connection.execute("INSERT INTO tasks VALUES ('synthetic-task', '{}')")
        connection.commit()
        snapshot = tmp_path / "snapshot.sqlite"
        with closing(sqlite3.connect(snapshot)) as destination, destination:
            connection.backup(destination)
    raw = snapshot.read_bytes()
    source = tmp_path / "source.dpbak"
    manifest = {"format": "document-pipeline-backup-v1", "credentials_included": False,
                "database": {"archive_path": "database.db", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}, "files": []}
    with zipfile.ZipFile(source, "x") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("database.db", raw)
    destination = tmp_path / "output.dpbak"
    work = destination.with_name(destination.name + ".work.db")
    sentinels = [work, Path(str(work) + "-wal"), Path(str(work) + "-shm")]
    for index, path in enumerate(sentinels):
        path.write_bytes(f"preserve-{index}".encode())
    assert module.inspect_business_backup(source)["format"] == manifest["format"]
    result = module.write_business_invalid_backup(source, destination)
    assert result["invalid_task_business_json"]
    assert [path.read_bytes() for path in sentinels] == [b"preserve-0", b"preserve-1", b"preserve-2"]
    with zipfile.ZipFile(destination) as archive, closing(sqlite3.connect(":memory:")) as connection, connection:
        connection.deserialize(archive.read("database.db"))
        assert connection.execute("SELECT file_name_json FROM tasks").fetchone() == ("{",)


def test_history_duplicate_is_checked_before_overwriting_fixture(tmp_path, monkeypatch):
    module = load_deep_data()
    files = tmp_path / "files"
    files.mkdir()
    sentinel = files / "DD-HISTORY-01.txt"
    sentinel.write_bytes(b"existing history sentinel")
    class Response:
        def __init__(self, value): self.value = value
        def raise_for_status(self): pass
        def json(self): return self.value
    class Client:
        def __init__(self, _): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def request(self, _method, route, **_):
            return Response([{"name": "深度数据历史分页夹具", "id": "synthetic-template"}]
                            if route.endswith("/templates") else [{"filename": sentinel.name}])
    monkeypatch.setattr(module, "create_app", lambda *_: object())
    monkeypatch.setattr(module, "TestClient", Client)
    with pytest.raises((RuntimeError, FileExistsError)):
        module.seed_history(tmp_path / "data", files, tmp_path / "report.json")
    assert sentinel.read_bytes() == b"existing history sentinel"


@pytest.mark.parametrize("existing", ["workbook", "report"])
def test_deep_data_rejects_existing_output_before_writing(tmp_path, monkeypatch, existing):
    module = load_deep_data()
    project = tmp_path / "workspace"
    monkeypatch.setattr(module, "__file__", str(project / "scripts/prepare-v060-deep-data.py"))
    files, data = project / ".local/run/files", project / ".local/run/data"
    files.mkdir(parents=True)
    report = project / ".local/run/report.json"
    sentinel = files / "DD-multi-sheet-import.xlsx" if existing == "workbook" else report
    sentinel.write_bytes(b"existing output sentinel")
    monkeypatch.setattr(module, "write_import_workbook", lambda path: path.write_bytes(b"overwritten workbook"))
    monkeypatch.setattr(module, "seed_history", lambda _data, _files, path: path.write_bytes(b"overwritten report"))
    monkeypatch.setattr(sys, "argv", ["prepare-v060-deep-data.py", "--data-dir", str(data), "--files-dir", str(files), "--report", str(report)])
    with pytest.raises(FileExistsError):
        module.main()
    assert sentinel.read_bytes() == b"existing output sentinel"


def test_deep_data_second_stage_preserves_existing_checkpoint_and_adds_new_sources(tmp_path, monkeypatch):
    module = load_deep_data()
    data, files = tmp_path / "data", tmp_path / "files"
    data.mkdir()
    files.mkdir()
    old = data / "existing-checkpoint.txt"
    old.write_bytes(b"prior checkpoint")
    report = tmp_path / "source-card-fixtures.json"
    uploaded = {}
    class Response:
        def __init__(self, value=None, content=b""):
            self.value, self.content = value, content
        def raise_for_status(self): pass
        def json(self): return self.value
    class Client:
        def __init__(self, _):
            self.app = SimpleNamespace(state=SimpleNamespace(session_factory=lambda: nullcontext(object())))
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def request(self, method, route, **kwargs):
            if route.endswith("/templates"):
                return Response([{"id": "builtin-autonomous", "engine_kind": "autonomous"}])
            if route.endswith("/tasks?limit=1000"):
                return Response([])
            if method == "POST" and route.endswith("/tasks"):
                name, payload, _ = kwargs["files"]["file"]
                task_id = f"task-{len(uploaded) + 1}"
                uploaded[task_id] = payload
                return Response({"id": task_id, "filename": name})
            if route.endswith("/file"):
                return Response(content=uploaded[route.split("/")[-2]])
            raise AssertionError(route)
    monkeypatch.setattr(module, "create_app", lambda _: object())
    monkeypatch.setattr(module, "TestClient", Client)
    monkeypatch.setattr(module, "process_task", lambda *_args, **_kwargs: object())
    result = module.seed_file_collection(data, files, report)
    assert len(result["tasks"]) == len(uploaded) == 8
    assert old.read_bytes() == b"prior checkpoint"
    assert all((files / item["filename"]).read_bytes() == uploaded[item["task_id"]] for item in result["tasks"])
    assert json.loads(report.read_text(encoding="utf-8")) == result


def test_deep_data_workbook_is_readable_and_never_overwrites(tmp_path):
    module = load_deep_data()
    workbook = tmp_path / "workbook.xlsx"
    module.write_import_workbook(workbook)
    before = workbook.read_bytes()
    assert [sheet["name"] for sheet in module.inspect_xlsx(workbook)["sheets"]] == ["订单", "库存", "空白"]
    with pytest.raises(FileExistsError):
        module.write_import_workbook(workbook)
    assert workbook.read_bytes() == before


def test_protocol_existing_run_does_not_overwrite_old_evidence(tmp_path):
    workspace, script = copy_helper(tmp_path, "verify-product-protocol-r3.py")
    evidence = workspace / ".local/v050-product-r3-protocol/cap-evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"old protocol evidence")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env.pop("R3_DATA_DIR", None)
    result = subprocess.run([sys.executable, str(script)], cwd=workspace, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode != 0
    error_line = result.stderr.strip().splitlines()[-1]
    assert error_line.startswith("{"), result.stderr
    rejection = json.loads(error_line)
    assert rejection["error_type"] == "RuntimeError", rejection
    assert rejection["error"] == f"Refusing to reuse or overwrite existing isolated data directory: {evidence.parent}", rejection
    assert evidence.read_bytes() == b"old protocol evidence"


def test_protocol_new_checkpoint_uses_own_evidence_path(tmp_path):
    workspace, script = copy_helper(tmp_path, "verify-product-protocol-r3.py")
    child = workspace / ".local/v050-product-r3-protocol/new-run"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["R3_DATA_DIR"] = str(child)
    code = "import runpy,sys; d=runpy.run_path(sys.argv[1]); print(d['EVIDENCE_PATH'])"
    result = subprocess.run([sys.executable, "-c", code, str(script)], cwd=workspace, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == child / "cap-evidence.json"


def test_protocol_startup_failure_writes_only_new_run_evidence(tmp_path, monkeypatch):
    workspace, script = copy_helper(tmp_path, "verify-product-protocol-r3.py")
    parent = workspace / ".local/v050-product-r3-protocol"
    parent.mkdir(parents=True)
    old = parent / "cap-evidence.json"
    old.write_bytes(b"previous evidence")
    child = parent / "new-run"
    monkeypatch.setenv("R3_DATA_DIR", str(child))
    module = load_script("protocol_fixture_preservation", script)
    write_bytes = Path.write_bytes
    def fail_on_fixture(path, value):
        if path.name == "r3-offline-acceptance.txt":
            raise OSError("synthetic setup interruption")
        return write_bytes(path, value)
    monkeypatch.setattr(Path, "write_bytes", fail_on_fixture)
    assert module.main() == 1
    assert old.read_bytes() == b"previous evidence"
    saved = json.loads((child / "cap-evidence.json").read_text(encoding="utf-8"))
    assert saved["summary"]["status"] == "failed"
    assert saved["summary"]["error"] == "synthetic setup interruption"
