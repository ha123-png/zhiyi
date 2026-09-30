from __future__ import annotations

import copy
from contextlib import redirect_stdout
from contextlib import contextmanager
from io import BytesIO
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


HELPER_PATH = Path(__file__).resolve().parents[1] / "verify-packaged-business.py"
SPEC = importlib.util.spec_from_file_location("verify_packaged_business", HELPER_PATH)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def _closed_sqlite(path: Path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class CandidateManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dist = self.root / "dist"
        self.onedir = self.dist / "Zhiyi"
        self.resources = self.dist / "resources"
        self.onedir.mkdir(parents=True)
        self.resources.mkdir()
        (self.onedir / "Zhiyi.exe").write_bytes(b"candidate-supervisor")
        (self.onedir / "ZhiyiCLI.exe").write_bytes(b"candidate-cli")
        (self.onedir / "_internal.dll").write_bytes(b"bound-payload")
        source = {
            "schema_version": 1,
            "git_revision": "a" * 40,
            "source_dirty": True,
            "files": [{"path": "VERSION", "size_bytes": 5, "sha256": "b" * 64}],
        }
        self.project = self.root / "project"
        for relative, content in {
            "packaging/installer.nsi": b"candidate installer source",
            "packaging/app.ico": b"candidate app icon",
            "packaging/uninstall.ico": b"candidate uninstall icon",
            "packaging/write-uninstall-list.ps1": b"candidate uninstall include generator",
        }.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            source["files"].append({
                "path": relative, "size_bytes": len(content), "sha256": _digest(content),
            })
        source_path = self.resources / "SOURCE_INPUTS.json"
        source_path.write_text(json.dumps(source), encoding="utf-8")
        payload_files = []
        for path in sorted(self.onedir.rglob("*")):
            if path.is_file():
                raw = path.read_bytes()
                payload_files.append({
                    "path": path.relative_to(self.onedir).as_posix(),
                    "size_bytes": len(raw),
                    "sha256": _digest(raw),
                })
        payload = {
            "schema_version": 1,
            "scope": "Every file under the onedir payload root",
            "file_count": len(payload_files),
            "files": payload_files,
        }
        payload_path = self.dist / "PAYLOAD_MANIFEST.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        release = {
            "schema_version": 2,
            "product": "Document Pipeline",
            "version": "0.6.0",
            "git_revision": source["git_revision"],
            "source_dirty": source["source_dirty"],
            "candidate_kind": "local-engineering-candidate",
            "source_manifest_sha256": _digest(source_path.read_bytes()),
            "payload_manifest_sha256": _digest(payload_path.read_bytes()),
            "artifacts": [
                {
                    "path": f"Zhiyi/{name}",
                    "size_bytes": (self.onedir / name).stat().st_size,
                    "sha256": _digest((self.onedir / name).read_bytes()),
                }
                for name in ("Zhiyi.exe", "ZhiyiCLI.exe")
            ],
        }
        (self.dist / "RELEASE_MANIFEST.json").write_text(json.dumps(release), encoding="utf-8")
        self.payload = payload
        self.payload_path = payload_path

    def tearDown(self):
        self.temp.cleanup()

    def test_candidate_manifest_binds_source_cli_and_every_payload_file(self):
        result = helper.verify_candidate(self.dist, "0.6.0")
        self.assertEqual(result["payload_file_count"], 3)
        self.assertEqual(result["git_revision"], "a" * 40)
        self.assertTrue(result["source_dirty"])

        installed = self.root / "installed"
        shutil.copytree(self.onedir, installed)
        (installed / "Uninstall.exe").write_bytes(b"NSIS-owned-file")
        self.assertTrue(helper.verify_payload_root(
            installed, self.payload, allowed_extra={"Uninstall.exe"},
        )["verified"])

    def test_candidate_refuses_wrong_version_and_payload_drift(self):
        with self.assertRaises(helper.VerificationError):
            helper.verify_candidate(self.dist, "0.5.0")
        (self.onedir / "_internal.dll").write_bytes(b"changed-after-build")
        with self.assertRaisesRegex(helper.VerificationError, "payload content differs"):
            helper.verify_candidate(self.dist, "0.6.0")

    def test_smoke_installer_inputs_are_bound_to_the_candidate_source_snapshot(self):
        required = [
            "packaging/installer.nsi",
            "packaging/app.ico",
            "packaging/uninstall.ico",
            "packaging/write-uninstall-list.ps1",
        ]
        result = helper.verify_build_sources(self.dist, self.project, required)
        self.assertTrue(result["verified"])
        self.assertEqual([item["path"] for item in result["files"]], required)

        report = self.root / "source-binding-report.json"
        arguments = [
            "verify-build-sources", "--dist-root", str(self.dist),
            "--project-root", str(self.project), "--report", str(report),
        ]
        for relative in required:
            arguments.extend(["--path", relative])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(helper.main(arguments), 0)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "passed")

        (self.project / "packaging/installer.nsi").write_bytes(b"different installer")
        with self.assertRaisesRegex(helper.VerificationError, "build source differs"):
            helper.verify_build_sources(self.dist, self.project, required)


class FakeApi:
    def __init__(self, data_root: Path, state_path: Path):
        self.data_root = data_root
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({
            "pid": 4567, "app_port": 18765, "child_pids": [5678],
        }), encoding="utf-8")
        self.host = "127.0.0.1"
        self.port = 18765
        self.api_calls = []
        self.task_id = "synthetic-task"
        self.table_id = "synthetic-table"
        self.zero_key = "import_2"
        self.amount_key = "import_1"
        self.note_key = "import_3"
        self.scenario_key = "import_0"
        self.rows = {
            101: {"id": 101, "version": 1, "values": {
                self.scenario_key: "release-zero", self.amount_key: 0,
                self.note_key: "initial zero",
            }},
            102: {"id": 102, "version": 1, "values": {
                self.scenario_key: "release-negative", self.amount_key: -12.5,
                self.note_key: "initial negative",
            }},
        }
        self.columns = [
            {"label": "Scenario", "key": self.scenario_key},
            {"label": "Amount", "key": self.amount_key},
            {"label": "Note", "key": self.note_key},
        ]
        self.histories = {
            101: [{"version": 1, "operation": "import_create", "before": None,
                   "after": copy.deepcopy(self.rows[101]["values"]), "editor": "import"}],
            102: [{"version": 1, "operation": "import_create", "before": None,
                   "after": copy.deepcopy(self.rows[102]["values"]), "editor": "import"}],
        }
        self.backup_name = "20260930T123456000000Z.dpbak"
        self.backup_bytes = b"synthetic-backup-archive"
        self.backup_state = None
        self.offline_once = False
        self.restore_state = None

    def multipart(self, path, *, filename, media_type, content, fields=None):
        self.api_calls.append(("multipart", path))
        if path == "/api/v1/tasks":
            self.assert_original = content == helper.ORIGINAL_BYTES
            return {"id": self.task_id, "status": "paused"}
        if path == "/api/v1/tables/import-new":
            self.assert_business_import = (
                filename == "release-lifecycle-business.xlsx"
                and media_type == helper.BUSINESS_XLSX_MEDIA_TYPE
                and content == helper.BUSINESS_XLSX_BYTES
            )
            return {"id": self.table_id}
        raise AssertionError(path)

    def request(self, method, path, *, body=None, content_type=None, binary=False):
        self.api_calls.append((method, path))
        if binary and path == f"/api/v1/tasks/{self.task_id}/file":
            return helper.ORIGINAL_BYTES
        raise AssertionError((method, path))

    def json(self, method, path, body=None):
        self.api_calls.append((method, path))
        if method == "GET" and path == "/api/v1/health":
            if self.offline_once:
                self.offline_once = False
                raise helper.VerificationError("API GET /api/v1/health failed: synthetic restart")
            return {"status": "ok", "version": "0.6.0"}
        if method == "POST" and path == "/api/v1/queue/pause":
            return {"paused": True}
        if method == "GET" and path == f"/api/v1/tables/{self.table_id}?page=1&page_size=100":
            return self.detail()
        if method == "POST" and path == "/api/v1/backups":
            (self.data_root / "backups").mkdir(parents=True, exist_ok=True)
            (self.data_root / "backups" / self.backup_name).write_bytes(self.backup_bytes)
            self.backup_state = (copy.deepcopy(self.rows), copy.deepcopy(self.histories))
            return {"name": self.backup_name, "size_bytes": len(self.backup_bytes)}
        if method == "GET" and path == "/api/v1/backups":
            return [{"name": self.backup_name}]
        if method == "GET" and path == f"/api/v1/tables/{self.table_id}/rows/101/revisions":
            return copy.deepcopy(self.histories[101])
        if method == "PATCH" and path.startswith(f"/api/v1/tables/{self.table_id}/rows/"):
            row_id = int(path.rsplit("/", 1)[1])
            row = self.rows[row_id]
            if body["expected_version"] != row["version"]:
                raise AssertionError("stale expected version")
            before = copy.deepcopy(row["values"])
            row["values"].update(body["changes"])
            row["version"] += 1
            self.histories[row_id].append({
                "version": row["version"], "operation": "table_edit",
                "before": before, "after": copy.deepcopy(row["values"]),
                "editor": body.get("editor", "local-user"),
            })
            return copy.deepcopy(row)
        if method == "POST" and path == f"/api/v1/tables/{self.table_id}/rows":
            row_id = 103
            self.rows[row_id] = {"id": row_id, "version": 1, "values": copy.deepcopy(body["values"])}
            self.histories[row_id] = [{"version": 1, "operation": "row_create", "before": None,
                                       "after": copy.deepcopy(body["values"]), "editor": body["editor"]}]
            return copy.deepcopy(self.rows[row_id])
        if method == "POST" and path == f"/api/v1/backups/{self.backup_name}/restore":
            self.rows, self.histories = copy.deepcopy(self.backup_state)
            self.restore_state = "succeeded"
            return {"scheduled": True, "restart_required": True}
        if method == "GET" and path == "/api/v1/backups/status":
            return {"restore_state": self.restore_state}
        raise AssertionError((method, path, body))

    def detail(self):
        return {
            "id": self.table_id,
            "row_count": len(self.rows),
            "columns": copy.deepcopy(self.columns),
            "rows": [copy.deepcopy(row) for row in self.rows.values()],
        }


class BusinessOracleTests(unittest.TestCase):
    def test_windows_short_image_path_matches_its_existing_long_path(self):
        import os
        if os.name != "nt":
            self.skipTest("Windows 8.3 executable aliases only")
        import ctypes
        from ctypes import wintypes
        with tempfile.TemporaryDirectory(prefix="release executable path ") as temporary:
            executable = Path(temporary) / "candidate application.exe"
            executable.write_bytes(b"candidate executable")
            get_short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
            get_short_path.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
            get_short_path.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            length = get_short_path(str(executable), buffer, len(buffer))
            self.assertGreater(length, 0)
            self.assertLess(length, len(buffer))
            short = buffer.value
            long = str(executable.resolve(strict=True))
            if os.path.normcase(short) == os.path.normcase(long):
                self.skipTest("Filesystem did not provide a distinct 8.3 alias")
            self.assertEqual(helper._normalized_executable_path(short),
                             helper._normalized_executable_path(long))

    def test_prepare_refuses_another_existing_executable_before_api_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "Zhiyi.exe"
            foreign = root / "other" / "Zhiyi.exe"
            foreign.parent.mkdir()
            candidate.write_bytes(b"candidate executable")
            foreign.write_bytes(b"another executable")
            state_path = root / "data" / "runtime" / "supervisor.json"
            api = FakeApi(root / "data", state_path)
            snapshot = self._runtime_snapshot(foreign)
            with self.assertRaisesRegex(helper.VerificationError, "does not match candidate executable"):
                helper.prepare_business(api, "0.6.0", state_path, 4567, candidate,
                                        root / "checkpoint.json", runtime_snapshot=snapshot)
            self.assertEqual(api.api_calls, [])

    @staticmethod
    def _runtime_snapshot(candidate_executable: Path, *, port_owner=5678, api_parent=4567):
        return {
            "port_owners": [port_owner],
            "processes": [
                {"process_id": 4567, "parent_process_id": 1,
                 "executable_path": str(candidate_executable)},
                {"process_id": 5678, "parent_process_id": api_parent,
                 "executable_path": str(candidate_executable)},
            ],
        }

    def test_prepare_mutate_supervised_restore_and_offline_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            state_path = data_root / "runtime" / "supervisor.json"
            checkpoint_path = root / "checkpoint.json"
            candidate_executable = root / "Zhiyi.exe"
            candidate_executable.write_bytes(b"candidate executable")
            runtime_snapshot = self._runtime_snapshot(candidate_executable)
            api = FakeApi(data_root, state_path)

            prepared = helper.prepare_business(
                api, "0.6.0", state_path, 4567, candidate_executable, checkpoint_path,
                runtime_snapshot=runtime_snapshot,
            )
            self.assertEqual(prepared["exact_amounts"], [0, -12.5])
            self.assertTrue(api.assert_original)
            self.assertTrue(api.assert_business_import)
            verified = helper.verify_business(
                api, "0.6.0", state_path, 4567, candidate_executable, checkpoint_path,
                runtime_snapshot=runtime_snapshot,
            )
            self.assertEqual(verified["baseline"]["row_count"], 2)

            restore_probe_calls = 0

            def restore_runtime_probe(port, supervisor_pid, api_pid):
                nonlocal restore_probe_calls
                if api.restore_state != "succeeded":
                    return runtime_snapshot
                restore_probe_calls += 1
                supervisor_record = runtime_snapshot["processes"][0]
                if restore_probe_calls == 1:
                    # The old child exited before supervisor.json published its replacement.
                    return {"port_owners": [api_pid], "processes": [supervisor_record]}
                if restore_probe_calls == 2:
                    # The probe already read old state; publish the new child for the next attempt.
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    state["child_pids"] = [5688]
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                return {
                    "port_owners": [5688],
                    "processes": [
                        supervisor_record,
                        {"process_id": 5688, "parent_process_id": 4567,
                         "executable_path": str(candidate_executable)},
                    ],
                }

            with patch.object(helper, "_windows_runtime_snapshot", side_effect=restore_runtime_probe):
                restored = helper.restore_business(
                    api, "0.6.0", state_path, 4567, candidate_executable, checkpoint_path,
                    runtime_snapshot=None,
                )
            self.assertFalse(restored["offline_observed"])
            self.assertTrue(restored["api_process_restarted"])
            self.assertEqual(restored["identity"]["supervisor_pid"], 4567)
            self.assertEqual(restored["restored_identity"]["supervisor_pid"], 4567)
            self.assertEqual(restored["identity"]["api_pid"], 5678)
            self.assertEqual(restored["restored_identity"]["api_pid"], 5688)
            self.assertEqual(restore_probe_calls, 3)
            self.assertEqual(restored["pre_restore_row_count"], 3)
            self.assertEqual(restored["restored"]["row_count"], 2)

            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            (data_root / "uploads").mkdir(parents=True)
            (data_root / "uploads" / f"{api.task_id}.txt").write_bytes(helper.ORIGINAL_BYTES)
            (data_root / "backups" / checkpoint["backup_name"]).write_bytes(api.backup_bytes)
            database = data_root / "document-pipeline.db"
            with _closed_sqlite(database) as connection:
                connection.executescript("""
                    CREATE TABLE data_tables (id TEXT PRIMARY KEY);
                    CREATE TABLE data_rows (id INTEGER, table_id TEXT, task_id TEXT, item_index INTEGER, row_json TEXT);
                    CREATE TABLE data_row_revisions (id INTEGER, row_id INTEGER, table_id TEXT, task_id TEXT,
                        version INTEGER, operation TEXT, before_json TEXT, after_json TEXT, editor TEXT);
                    CREATE TABLE tasks (id TEXT PRIMARY KEY, storage_path TEXT, sha256 TEXT);
                """)
                connection.execute("INSERT INTO data_tables VALUES (?)", (checkpoint["table_id"],))
                for row_id, row in api.rows.items():
                    values = copy.deepcopy(row["values"])
                    values["__row_group"] = "test-hidden-group"
                    connection.execute("INSERT INTO data_rows VALUES (?, ?, NULL, ?, ?)",
                        (row_id, checkpoint["table_id"], row_id - 100, json.dumps(values)))
                for revision_id, revision in enumerate(api.histories[101], 1):
                    before = revision["before"]
                    after = revision["after"]
                    connection.execute("INSERT INTO data_row_revisions VALUES (?, 101, ?, NULL, ?, ?, ?, ?, ?)",
                        (revision_id, checkpoint["table_id"], revision["version"], revision["operation"],
                         json.dumps(before) if before is not None else None,
                         json.dumps(after) if after is not None else None, revision["editor"]))
                connection.execute("INSERT INTO tasks VALUES (?, ?, ?)",
                    (api.task_id, f"{api.task_id}.txt", checkpoint["original_sha256"]))
            checkpoint["backup_sha256"] = _digest(api.backup_bytes)
            checkpoint["backup_size_bytes"] = len(api.backup_bytes)
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
            offline = helper.verify_offline_business(data_root, checkpoint_path)
            self.assertEqual(offline["sqlite_integrity_check"], "ok")
            self.assertEqual(offline["business_rows"], 2)

    def test_restore_fails_after_deadline_when_api_child_never_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            state_path = data_root / "runtime" / "supervisor.json"
            checkpoint_path = root / "checkpoint.json"
            candidate_executable = root / "Zhiyi.exe"
            candidate_executable.write_bytes(b"candidate executable")
            runtime_snapshot = self._runtime_snapshot(candidate_executable)
            api = FakeApi(data_root, state_path)
            helper.prepare_business(
                api, "0.6.0", state_path, 4567, candidate_executable, checkpoint_path,
                runtime_snapshot=runtime_snapshot,
            )

            clock = [0.0]

            def monotonic():
                return clock[0]

            def advance_without_wait(seconds):
                clock[0] += seconds

            with (
                patch.object(helper.time, "monotonic", side_effect=monotonic),
                patch.object(helper.time, "sleep", side_effect=advance_without_wait) as sleep_mock,
                self.assertRaisesRegex(
                    helper.VerificationError,
                    "before timeout: supervised restore did not replace the API child process",
                ),
            ):
                helper.restore_business(
                    api, "0.6.0", state_path, 4567, candidate_executable, checkpoint_path,
                    runtime_snapshot=runtime_snapshot, timeout_seconds=0.1,
                )
            self.assertEqual(sleep_mock.call_count, 2)

    def test_prepare_refuses_wrong_port_owner_before_any_api_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            state_path = data_root / "runtime" / "supervisor.json"
            checkpoint_path = root / "checkpoint.json"
            candidate_executable = root / "Zhiyi.exe"
            candidate_executable.write_bytes(b"candidate executable")
            api = FakeApi(data_root, state_path)

            with self.assertRaisesRegex(helper.VerificationError, "loopback port 18765 owner"):
                helper.prepare_business(
                    api, "0.6.0", state_path, 4567, candidate_executable, checkpoint_path,
                    runtime_snapshot=self._runtime_snapshot(candidate_executable, port_owner=9999),
                )

            self.assertEqual(api.api_calls, [])
            self.assertFalse(checkpoint_path.exists())

    def test_api_client_refuses_non_loopback_destination(self):
        with self.assertRaises(helper.VerificationError):
            helper.LoopbackApi("https://example.invalid")

    def test_business_numbers_accept_numeric_equivalence_but_reject_coercions(self):
        self.assertTrue(helper._number_is_exact(0, 0))
        self.assertTrue(helper._number_is_exact(0.0, 0))
        self.assertTrue(helper._number_is_exact(-12.5, -12.5))
        for value in ("0", False, None):
            with self.subTest(value=value):
                self.assertFalse(helper._number_is_exact(value, 0))

    def test_business_import_fixture_contains_typed_numeric_cells(self):
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(helper.BUSINESS_XLSX_BYTES), read_only=True, data_only=True)
        try:
            rows = list(workbook.active.values)
        finally:
            workbook.close()
        self.assertEqual(rows[1], ("release-zero", 0, "initial zero"))
        self.assertEqual(rows[2], ("release-negative", -12.5, "initial negative"))
        self.assertTrue(helper._number_is_exact(rows[1][1], 0))
        self.assertTrue(helper._number_is_exact(rows[2][1], -12.5))

    def test_business_cli_dispatch_passes_candidate_and_timeout_arguments(self):
        base_url = "http://127.0.0.1:18765"
        version = "0.6.0"
        state_path = Path("supervisor.json")
        supervisor_pid = 4567
        candidate_executable = Path("Zhiyi.exe")
        checkpoint_path = Path("checkpoint.json")
        api = object()
        cases = (
            ("prepare-business", "prepare_business", [], {}),
            ("verify-business", "verify_business", [], {}),
            ("restore-business", "restore_business", ["--timeout-seconds", "12"],
             {"timeout_seconds": 12.0}),
        )

        for command, function_name, extra_arguments, extra_keywords in cases:
            with self.subTest(command=command):
                arguments = [
                    command, "--base-url", base_url, "--version", version,
                    "--supervisor-state", str(state_path), "--supervisor-pid", str(supervisor_pid),
                    "--candidate-executable", str(candidate_executable),
                    "--checkpoint", str(checkpoint_path), *extra_arguments,
                ]
                with (
                    patch.object(helper, "LoopbackApi", return_value=api) as api_factory,
                    patch.object(helper, function_name, return_value={"status": "passed"}) as business_call,
                    redirect_stdout(io.StringIO()),
                ):
                    exit_code = helper.main(arguments)

                self.assertEqual(exit_code, 0)
                api_factory.assert_called_once_with(base_url)
                business_call.assert_called_once_with(
                    api, version, state_path, supervisor_pid, candidate_executable,
                    checkpoint_path, **extra_keywords,
                )


if __name__ == "__main__":
    unittest.main()
