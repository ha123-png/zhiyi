"""Verify a release candidate's full payload and synthetic packaged data lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class VerificationError(RuntimeError):
    pass


ORIGINAL_BYTES = (
    b"PRODUCTION-LIFECYCLE-SYNTHETIC-ORIGINAL-v1\n"
    b"Candidate package business-preservation sentinel.\n"
)

def _business_xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet("Business")
    worksheet.append(["Scenario", "Amount", "Note"])
    worksheet.append(["release-zero", 0, "initial zero"])
    worksheet.append(["release-negative", -12.5, "initial negative"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


BUSINESS_XLSX_BYTES = _business_xlsx_bytes()
BUSINESS_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SAFE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(
        hasattr(path, "is_junction") and path.is_junction()
    )


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise VerificationError(f"unsafe manifest path: {value!r}")
    relative = PurePosixPath(value)
    if (relative.is_absolute() or relative.as_posix() != value
            or any(part in {"", ".", ".."} for part in relative.parts)):
        raise VerificationError(f"unsafe manifest path: {value!r}")
    return relative.as_posix()


def _read_json(path: Path) -> dict:
    if _is_link(path) or not path.is_file():
        raise VerificationError(f"required regular file is missing or linked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read JSON file: {path}: {error}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"JSON root must be an object: {path}")
    return value


def verify_payload_root(
    root: Path,
    manifest: dict,
    *,
    allowed_extra: set[str] | None = None,
) -> dict:
    root = root.resolve(strict=True)
    expected = manifest.get("files")
    if manifest.get("schema_version") != 1 or not isinstance(expected, list):
        raise VerificationError("unsupported or malformed PAYLOAD_MANIFEST.json")
    if len(expected) != manifest.get("file_count") or not expected:
        raise VerificationError("payload manifest file_count does not match its entries")
    if len(expected) > 100_000:
        raise VerificationError("payload manifest contains too many entries")

    expected_by_path: dict[str, dict] = {}
    for item in expected:
        if not isinstance(item, dict):
            raise VerificationError("payload entry must be an object")
        relative = _safe_relative(item.get("path"))
        if relative in expected_by_path:
            raise VerificationError(f"duplicate payload path: {relative}")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if not isinstance(digest, str) or not SAFE_SHA256.fullmatch(digest):
            raise VerificationError(f"invalid payload digest: {relative}")
        if type(size) is not int or size < 0:
            raise VerificationError(f"invalid payload size: {relative}")
        expected_by_path[relative] = item

    actual: dict[str, Path] = {}
    for path in root.rglob("*"):
        if _is_link(path):
            raise VerificationError(f"payload contains a link: {path.relative_to(root)}")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise VerificationError(f"payload path escaped its root: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            actual[relative] = path

    allowed = allowed_extra or set()
    extras = set(actual) - set(expected_by_path) - allowed
    missing = set(expected_by_path) - set(actual)
    if missing or extras:
        raise VerificationError(
            f"payload file set differs (missing={sorted(missing)[:8]}, extra={sorted(extras)[:8]})"
        )
    for relative, record in expected_by_path.items():
        path = actual[relative]
        if path.stat().st_size != record["size_bytes"] or sha256(path) != record["sha256"]:
            raise VerificationError(f"payload content differs: {relative}")
    for relative in allowed & set(actual):
        if relative != "Uninstall.exe":
            raise VerificationError(f"unexpected extra installed file: {relative}")
    return {"file_count": len(expected_by_path), "verified": True}


def verify_candidate(dist_root: Path, expected_version: str | None = None) -> dict:
    dist_root = dist_root.resolve(strict=True)
    release_path = dist_root / "RELEASE_MANIFEST.json"
    payload_path = dist_root / "PAYLOAD_MANIFEST.json"
    source_path = dist_root / "resources" / "SOURCE_INPUTS.json"
    release = _read_json(release_path)
    if release.get("schema_version") != 2 or release.get("product") != "Document Pipeline":
        raise VerificationError("candidate must have a schema 2 Document Pipeline release manifest")
    version = release.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise VerificationError("candidate release manifest has an invalid version")
    if expected_version is not None and version != expected_version:
        raise VerificationError(
            f"requested version {expected_version} does not match candidate {version}"
        )
    if release.get("candidate_kind") != "local-engineering-candidate":
        raise VerificationError("release manifest does not identify a local engineering candidate")

    payload_hash = release.get("payload_manifest_sha256")
    source_hash = release.get("source_manifest_sha256")
    if not isinstance(payload_hash, str) or not SAFE_SHA256.fullmatch(payload_hash):
        raise VerificationError("release manifest has no valid payload manifest hash")
    if not isinstance(source_hash, str) or not SAFE_SHA256.fullmatch(source_hash):
        raise VerificationError("release manifest has no valid source manifest hash")
    if sha256(payload_path) != payload_hash:
        raise VerificationError("PAYLOAD_MANIFEST.json hash differs from release manifest")
    if sha256(source_path) != source_hash:
        raise VerificationError("SOURCE_INPUTS.json hash differs from release manifest")

    source = _read_json(source_path)
    if source.get("schema_version") != 1 or not isinstance(source.get("files"), list):
        raise VerificationError("malformed source input manifest")
    if source.get("git_revision") != release.get("git_revision"):
        raise VerificationError("source manifest revision differs from release manifest")
    if source.get("source_dirty") != release.get("source_dirty"):
        raise VerificationError("source manifest dirty state differs from release manifest")

    payload = _read_json(payload_path)
    onedir = dist_root / "Zhiyi"
    payload_result = verify_payload_root(onedir, payload)
    artifacts = release.get("artifacts")
    if not isinstance(artifacts, list):
        raise VerificationError("release manifest artifact list is missing")
    artifact_by_path = {}
    for item in artifacts:
        if not isinstance(item, dict):
            raise VerificationError("release artifact entry must be an object")
        relative = _safe_relative(item.get("path"))
        if relative in artifact_by_path:
            raise VerificationError(f"duplicate release artifact: {relative}")
        artifact_by_path[relative] = item
    executable_hashes = {}
    for relative in ("Zhiyi/Zhiyi.exe", "Zhiyi/ZhiyiCLI.exe"):
        record = artifact_by_path.get(relative)
        executable = dist_root.joinpath(*PurePosixPath(relative).parts)
        if not isinstance(record, dict) or not executable.is_file() or _is_link(executable):
            raise VerificationError(f"candidate artifact is missing: {relative}")
        actual_hash = sha256(executable)
        if actual_hash != record.get("sha256") or executable.stat().st_size != record.get("size_bytes"):
            raise VerificationError(f"candidate executable differs from release manifest: {relative}")
        executable_hashes[relative] = actual_hash

    return {
        "version": version,
        "git_revision": release.get("git_revision"),
        "source_dirty": release.get("source_dirty"),
        "source_manifest_sha256": source_hash,
        "payload_manifest_sha256": payload_hash,
        "release_manifest_sha256": sha256(release_path),
        "payload_file_count": payload_result["file_count"],
        "executables": executable_hashes,
        "candidate_kind": release["candidate_kind"],
    }


def verify_build_sources(
    dist_root: Path, project_root: Path, required_paths: list[str],
) -> dict:
    """Bind the files used to compile SMOKE_MODE to the candidate's source snapshot."""
    dist_root = dist_root.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    release = _read_json(dist_root / "RELEASE_MANIFEST.json")
    source_path = dist_root / "resources" / "SOURCE_INPUTS.json"
    source_hash = release.get("source_manifest_sha256")
    if not isinstance(source_hash, str) or not SAFE_SHA256.fullmatch(source_hash):
        raise VerificationError("release manifest has no valid source manifest hash")
    if sha256(source_path) != source_hash:
        raise VerificationError("SOURCE_INPUTS.json hash differs from release manifest")
    source = _read_json(source_path)
    if source.get("schema_version") != 1 or not isinstance(source.get("files"), list):
        raise VerificationError("malformed source input manifest")
    records = {}
    for item in source["files"]:
        if not isinstance(item, dict):
            raise VerificationError("source input entry must be an object")
        relative = _safe_relative(item.get("path"))
        if relative in records:
            raise VerificationError(f"duplicate source input path: {relative}")
        records[relative] = item

    verified = []
    for value in required_paths:
        relative = _safe_relative(value)
        record = records.get(relative)
        if not isinstance(record, dict) or record.get("missing") is True:
            raise VerificationError(f"candidate source snapshot does not contain {relative}")
        digest = record.get("sha256")
        size = record.get("size_bytes")
        if not isinstance(digest, str) or not SAFE_SHA256.fullmatch(digest):
            raise VerificationError(f"candidate source snapshot has an invalid digest: {relative}")
        if type(size) is not int or size < 0:
            raise VerificationError(f"candidate source snapshot has an invalid size: {relative}")

        target = project_root.joinpath(*PurePosixPath(relative).parts)
        cursor = project_root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if _is_link(cursor):
                raise VerificationError(f"build source path is linked: {relative}")
        if not target.is_file() or not target.resolve(strict=True).is_relative_to(project_root):
            raise VerificationError(f"build source file is missing or escaped the project: {relative}")
        actual_size = target.stat().st_size
        actual_digest = sha256(target)
        if actual_size != size or actual_digest != digest:
            raise VerificationError(f"build source differs from candidate snapshot: {relative}")
        verified.append({"path": relative, "size_bytes": actual_size, "sha256": actual_digest})
    return {"verified": True, "files": verified}


class LoopbackApi:
    def __init__(self, base_url: str, timeout: float = 12.0):
        parts = urlsplit(base_url)
        try:
            address = ipaddress.ip_address(parts.hostname or "")
        except ValueError as error:
            raise VerificationError("API base URL must use a loopback IP literal") from error
        if (
            parts.scheme != "http"
            or not address.is_loopback
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise VerificationError("API base URL must be plain HTTP on loopback without credentials")
        self.base_url = base_url.rstrip("/")
        self.host = str(address)
        try:
            self.port = parts.port
        except ValueError as error:
            raise VerificationError("API base URL has an invalid port") from error
        if self.port is None or not 1 <= self.port <= 65535:
            raise VerificationError("API base URL must include a valid explicit port")
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method: str, path: str, *, body: bytes | None = None,
                content_type: str | None = None, binary: bool = False):
        if not path.startswith("/") or path.startswith("//"):
            raise VerificationError("API path must be an absolute local path")
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = response.read()
        except HTTPError as error:
            detail = error.read(4096).decode("utf-8", errors="replace")
            raise VerificationError(f"API {method} {path} returned HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise VerificationError(f"API {method} {path} failed: {error}") from error
        if binary:
            return payload
        if not payload:
            return None
        try:
            value = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise VerificationError(f"API {method} {path} returned invalid JSON") from error
        return value

    def json(self, method: str, path: str, body: dict | None = None):
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        return self.request(method, path, body=payload,
                            content_type="application/json" if payload is not None else None)

    def multipart(self, path: str, *, filename: str, media_type: str, content: bytes,
                  fields: dict[str, str] | None = None):
        boundary = "----ZhiyiLifecycle" + hashlib.sha256(content).hexdigest()[:24]
        chunks = []
        for key, value in (fields or {}).items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                value.encode("utf-8"), b"\r\n",
            ])
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            (f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n').encode(),
            f"Content-Type: {media_type}\r\n\r\n".encode(),
            content, b"\r\n", f"--{boundary}--\r\n".encode(),
        ])
        return self.request("POST", path, body=b"".join(chunks),
                            content_type=f"multipart/form-data; boundary={boundary}")


def _windows_runtime_snapshot(port: int, supervisor_pid: int, api_pid: int) -> dict:
    """Read the loopback listener and process ancestry using built-in Windows tools."""
    if sys.platform != "win32":
        raise VerificationError("packaged API process identity can only be verified on Windows")
    powershell = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh.exe")
        or shutil.which("pwsh")
    )
    if not powershell:
        raise VerificationError("PowerShell is unavailable for read-only API process identity checks")
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$port = __PORT__
$supervisorId = __SUPERVISOR_PID__
$apiId = __API_PID__
$owners = @(
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop |
        Where-Object { $_.LocalAddress -eq '127.0.0.1' } |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { [int]$_ }
)
$processes = @(
    foreach ($id in @($supervisorId, $apiId) | Select-Object -Unique) {
        $process = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = ' + $id) -ErrorAction Stop
        if (-not $process -or -not $process.ExecutablePath) { throw "Cannot inspect process PID $id." }
        [pscustomobject]@{
            process_id = [int]$process.ProcessId
            parent_process_id = [int]$process.ParentProcessId
            executable_path = [string]$process.ExecutablePath
        }
    }
)
[pscustomobject]@{ port_owners = $owners; processes = $processes } |
    ConvertTo-Json -Depth 4 -Compress
"""
    script = (script.replace("__PORT__", str(port))
              .replace("__SUPERVISOR_PID__", str(supervisor_pid))
              .replace("__API_PID__", str(api_pid)))
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError(f"could not inspect the packaged API process: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise VerificationError(f"Windows API process inspection failed: {detail}")
    try:
        snapshot = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise VerificationError("Windows API process inspection returned invalid JSON") from error
    if not isinstance(snapshot, dict):
        raise VerificationError("Windows API process inspection returned an invalid object")
    return snapshot


def _normalized_executable_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationError("Windows process executable path is unavailable")
    path = os.path.normcase(os.path.abspath(value.strip()))
    if path.startswith("\\\\?\\"):
        path = path[4:]
    return path


def _validate_runtime_binding(
    state: dict,
    supervisor_pid: int,
    api_port: int,
    candidate_executable: Path,
    snapshot: dict,
) -> dict:
    if type(state.get("app_port")) is not int or state["app_port"] != api_port:
        raise VerificationError(
            f"supervisor app_port {state.get('app_port')!r} does not match API port {api_port}"
        )
    child_pids = state.get("child_pids")
    if not isinstance(child_pids, list) or not child_pids or type(child_pids[0]) is not int:
        raise VerificationError("supervisor state has no valid API child PID in child_pids[0]")
    api_pid = child_pids[0]
    if api_pid <= 4 or api_pid == supervisor_pid:
        raise VerificationError("supervisor state contains an unsafe API child PID")

    owners = snapshot.get("port_owners")
    if not isinstance(owners, list) or any(type(pid) is not int for pid in owners):
        raise VerificationError("Windows process inspection has no valid loopback listener owners")
    if set(owners) != {api_pid}:
        raise VerificationError(
            f"loopback port {api_port} owner {owners!r} does not match supervisor API PID {api_pid}"
        )

    records = snapshot.get("processes")
    if not isinstance(records, list):
        raise VerificationError("Windows process inspection has no process metadata")
    by_pid = {}
    for record in records:
        if not isinstance(record, dict) or type(record.get("process_id")) is not int:
            raise VerificationError("Windows process metadata is malformed")
        process_id = record["process_id"]
        if process_id in by_pid:
            raise VerificationError(f"Windows process metadata repeats PID {process_id}")
        by_pid[process_id] = record

    supervisor = by_pid.get(supervisor_pid)
    api_process = by_pid.get(api_pid)
    if not supervisor or not api_process:
        raise VerificationError("Windows process metadata is missing the supervisor or API PID")
    if type(api_process.get("parent_process_id")) is not int or api_process["parent_process_id"] != supervisor_pid:
        raise VerificationError(
            f"API PID {api_pid} parent PID {api_process.get('parent_process_id')!r} "
            f"does not match supervisor PID {supervisor_pid}"
        )

    expected_path = _normalized_executable_path(candidate_executable.resolve(strict=True).as_posix())
    for process_id, record in ((supervisor_pid, supervisor), (api_pid, api_process)):
        actual_path = _normalized_executable_path(record.get("executable_path"))
        if actual_path != expected_path:
            raise VerificationError(
                f"PID {process_id} executable {record.get('executable_path')!r} "
                f"does not match candidate executable {str(candidate_executable)!r}"
            )
    return {
        "supervisor_pid": supervisor_pid,
        "api_pid": api_pid,
        "app_port": api_port,
        "candidate_executable": str(candidate_executable.resolve(strict=True)),
    }


def _runtime_identity(
    api: LoopbackApi,
    version: str,
    state_path: Path,
    supervisor_pid: int,
    candidate_executable: Path,
    *,
    runtime_snapshot: dict | None = None,
) -> dict:
    state = _read_json(state_path)
    if type(state.get("pid")) is not int or state["pid"] != supervisor_pid:
        raise VerificationError(
            f"supervisor state PID {state.get('pid')!r} does not match launched process {supervisor_pid}"
        )
    if api.host != "127.0.0.1":
        raise VerificationError("packaged API identity checks require its 127.0.0.1 listener")
    api_pid = state.get("child_pids", [None])[0] if isinstance(state.get("child_pids"), list) else None
    if type(api_pid) is not int:
        raise VerificationError("supervisor state has no valid API child PID in child_pids[0]")
    snapshot = runtime_snapshot
    if snapshot is None:
        snapshot = _windows_runtime_snapshot(api.port, supervisor_pid, api_pid)
    binding = _validate_runtime_binding(
        state, supervisor_pid, api.port, candidate_executable, snapshot,
    )
    health = api.json("GET", "/api/v1/health")
    if not isinstance(health, dict) or health.get("status") != "ok" or health.get("version") != version:
        raise VerificationError(f"API health does not identify candidate {version}: {health!r}")
    return {"api_version": health["version"], **binding}


def _multipart_upload(api: LoopbackApi, path: str, filename: str, media_type: str,
                      content: bytes, fields: dict[str, str] | None = None) -> dict:
    result = api.multipart(path, filename=filename, media_type=media_type,
                           content=content, fields=fields)
    if not isinstance(result, dict):
        raise VerificationError(f"upload returned an invalid object: {path}")
    return result


def _table_detail(api: LoopbackApi, table_id: str) -> dict:
    result = api.json("GET", f"/api/v1/tables/{table_id}?page=1&page_size=100")
    if not isinstance(result, dict):
        raise VerificationError("table detail response is invalid")
    return result


def _columns(detail: dict) -> dict[str, str]:
    columns = detail.get("columns")
    if not isinstance(columns, list):
        raise VerificationError("imported table has no column list")
    result = {}
    for column in columns:
        if isinstance(column, dict) and isinstance(column.get("label"), str):
            result[column["label"]] = column.get("key")
    if any(not result.get(label) for label in ("Scenario", "Amount", "Note")):
        raise VerificationError(f"imported table columns do not match the oracle: {result!r}")
    return result


def _number_is_exact(value: object, expected: int | float) -> bool:
    return type(value) in (int, float) and value == expected


def _logical_history(api: LoopbackApi, table_id: str, row_id: int) -> list[dict]:
    history = api.json("GET", f"/api/v1/tables/{table_id}/rows/{row_id}/revisions")
    if not isinstance(history, list):
        raise VerificationError("row revision history response is invalid")
    return [
        {key: item.get(key) for key in ("version", "operation", "before", "after", "editor")}
        for item in history
    ]


def _find_rows(detail: dict, columns: dict[str, str]) -> tuple[dict, dict]:
    rows = detail.get("rows")
    if not isinstance(rows, list) or len(rows) != 2:
        raise VerificationError(f"expected exactly two business rows, got {len(rows) if isinstance(rows, list) else 'invalid'}")
    by_scenario = {}
    for row in rows:
        values = row.get("values", {})
        scenario = values.get(columns["Scenario"])
        if scenario in by_scenario:
            raise VerificationError(f"duplicate scenario row: {scenario!r}")
        by_scenario[scenario] = row
    zero = by_scenario.get("release-zero")
    negative = by_scenario.get("release-negative")
    if not zero or not negative:
        raise VerificationError("expected release-zero and release-negative rows")
    if not _number_is_exact(zero["values"].get(columns["Amount"]), 0):
        raise VerificationError("zero-valued business fact changed type or value")
    if not _number_is_exact(negative["values"].get(columns["Amount"]), -12.5):
        raise VerificationError("negative decimal business fact changed type or value")
    return zero, negative


def _load_checkpoint(path: Path) -> dict:
    result = _read_json(path)
    if result.get("schema_version") != 1:
        raise VerificationError("unsupported lifecycle checkpoint")
    return result


def _verify_original(api: LoopbackApi, checkpoint: dict) -> None:
    raw = api.request("GET", f"/api/v1/tasks/{checkpoint['task_id']}/file", binary=True)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != checkpoint["original_sha256"] or len(raw) != checkpoint["original_size_bytes"]:
        raise VerificationError("original file bytes differ from the pre-backup oracle")


def _verify_baseline(api: LoopbackApi, checkpoint: dict) -> dict:
    detail = _table_detail(api, checkpoint["table_id"])
    if detail.get("row_count") != 2:
        raise VerificationError(f"restored table row_count must be 2, got {detail.get('row_count')!r}")
    columns = checkpoint["columns"]
    zero, negative = _find_rows(detail, columns)
    zero_values = zero["values"]
    negative_values = negative["values"]
    expected = checkpoint["baseline"]
    for item, values in ((expected["zero"], zero_values), (expected["negative"], negative_values)):
        if values.get(columns["Scenario"]) != item["scenario"] or values.get(columns["Note"]) != item["note"]:
            raise VerificationError("current business text values differ from the checkpoint")
    if zero["id"] != expected["zero"]["row_id"] or negative["id"] != expected["negative"]["row_id"]:
        raise VerificationError("restore changed stable business row identity")
    revisions = _logical_history(api, checkpoint["table_id"], zero["id"])
    if revisions != checkpoint["zero_history"]:
        raise VerificationError("file-independent current-data revision history differs after restore")
    _verify_original(api, checkpoint)
    backups = api.json("GET", "/api/v1/backups")
    if not isinstance(backups, list) or not any(
        isinstance(item, dict) and item.get("name") == checkpoint["backup_name"] for item in backups
    ):
        raise VerificationError("the pre-mutation business backup is missing")
    return {
        "row_count": detail["row_count"],
        "zero_amount": zero_values[columns["Amount"]],
        "negative_amount": negative_values[columns["Amount"]],
        "zero_row_revision_count": len(revisions),
        "original_sha256": checkpoint["original_sha256"],
        "backup_name": checkpoint["backup_name"],
    }


def prepare_business(api: LoopbackApi, version: str, state_path: Path,
                     supervisor_pid: int, candidate_executable: Path, checkpoint_path: Path,
                     *, runtime_snapshot: dict | None = None) -> dict:
    identity = _runtime_identity(api, version, state_path, supervisor_pid,
                                 candidate_executable, runtime_snapshot=runtime_snapshot)
    paused = api.json("POST", "/api/v1/queue/pause")
    if not isinstance(paused, dict) or paused.get("paused") is not True:
        raise VerificationError("could not freeze the queue before synthetic upload")

    task = _multipart_upload(
        api, "/api/v1/tasks", "release-lifecycle-original.txt", "text/plain",
        ORIGINAL_BYTES, {"template_mode": "smart"},
    )
    if task.get("status") != "paused" or not isinstance(task.get("id"), str):
        raise VerificationError("synthetic original upload was not safely frozen in the queue")
    original = api.request("GET", f"/api/v1/tasks/{task['id']}/file", binary=True)
    if original != ORIGINAL_BYTES:
        raise VerificationError("uploaded synthetic original did not read back byte-for-byte")

    imported = _multipart_upload(
        api, "/api/v1/tables/import-new", "release-lifecycle-business.xlsx",
        BUSINESS_XLSX_MEDIA_TYPE, BUSINESS_XLSX_BYTES,
    )
    table_id = imported.get("id")
    if not isinstance(table_id, str):
        raise VerificationError("business XLSX import did not return a table identity")
    detail = _table_detail(api, table_id)
    columns = _columns(detail)
    zero, negative = _find_rows(detail, columns)
    zero_row_id = zero["id"]

    edited = api.json("PATCH", f"/api/v1/tables/{table_id}/rows/{zero_row_id}", {
        "expected_version": zero["version"],
        "changes": {columns["Note"]: "durable current-data revision"},
        "editor": "release-lifecycle-oracle",
    })
    if not isinstance(edited, dict) or edited.get("version") != zero["version"] + 1:
        raise VerificationError("current-data edit did not create a new row version")
    detail = _table_detail(api, table_id)
    zero, negative = _find_rows(detail, columns)
    if zero["values"].get(columns["Note"]) != "durable current-data revision":
        raise VerificationError("current-data edit did not read back its exact value")
    history = _logical_history(api, table_id, zero_row_id)
    if len(history) != 2 or [item["operation"] for item in history] != ["import_create", "table_edit"]:
        raise VerificationError("current-data edit history is incomplete")

    backup = api.json("POST", "/api/v1/backups")
    backup_name = backup.get("name") if isinstance(backup, dict) else None
    if not isinstance(backup_name, str) or not re.fullmatch(r"\d{8}T\d{12}Z\.dpbak", backup_name):
        raise VerificationError("business backup returned an invalid candidate-local name")
    data_root = state_path.resolve(strict=True).parent.parent
    backup_path = data_root / "backups" / backup_name
    if _is_link(backup_path) or not backup_path.is_file():
        raise VerificationError("created business backup is missing or linked")

    checkpoint = {
        "schema_version": 1,
        "version": version,
        "task_id": task["id"],
        "original_sha256": hashlib.sha256(ORIGINAL_BYTES).hexdigest(),
        "original_size_bytes": len(ORIGINAL_BYTES),
        "table_id": table_id,
        "columns": columns,
        "baseline": {
            "zero": {"row_id": zero_row_id, "scenario": "release-zero", "note": "durable current-data revision"},
            "negative": {"row_id": negative["id"], "scenario": "release-negative", "note": "initial negative"},
        },
        "zero_history": history,
        "backup_name": backup_name,
        "backup_sha256": sha256(backup_path),
        "backup_size_bytes": backup_path.stat().st_size,
    }
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    baseline = _verify_baseline(api, checkpoint)
    return {
        "status": "passed",
        "identity": identity,
        "queue_paused": True,
        "original_sha256": checkpoint["original_sha256"],
        "table_id": table_id,
        "row_count": baseline["row_count"],
        "exact_amounts": [baseline["zero_amount"], baseline["negative_amount"]],
        "row_revision_count": baseline["zero_row_revision_count"],
        "backup_name": backup_name,
        "checkpoint": str(checkpoint_path),
    }


def verify_business(api: LoopbackApi, version: str, state_path: Path,
                    supervisor_pid: int, candidate_executable: Path, checkpoint_path: Path,
                    *, runtime_snapshot: dict | None = None) -> dict:
    identity = _runtime_identity(api, version, state_path, supervisor_pid,
                                 candidate_executable, runtime_snapshot=runtime_snapshot)
    checkpoint = _load_checkpoint(checkpoint_path)
    if checkpoint.get("version") != version:
        raise VerificationError("checkpoint version differs from the running candidate")
    baseline = _verify_baseline(api, checkpoint)
    return {"status": "passed", "identity": identity, "baseline": baseline}


def _is_restore_transition_error(error: VerificationError) -> bool:
    """Identify runtime/oracle reads that can be temporarily stale during supervised restart."""
    message = str(error)
    if message.startswith("API GET ") and (
        " failed:" in message or " returned invalid JSON" in message
    ):
        return True
    retryable_prefixes = (
        "could not inspect the packaged API process:",
        "Windows API process inspection failed:",
        "Windows API process inspection returned",
        "Windows process inspection",
        "Windows process metadata",
        "loopback port ",
        "API PID ",
        "PID ",
        "supervisor state has no valid API child PID",
        "API health does not identify candidate ",
        "restored table row_count must be ",
        "expected exactly two business rows,",
        "duplicate scenario row:",
        "expected release-zero and release-negative rows",
        "zero-valued business fact changed type or value",
        "negative decimal business fact changed type or value",
        "current business text values differ from the checkpoint",
        "restore changed stable business row identity",
        "file-independent current-data revision history differs after restore",
        "original file bytes differ from the pre-backup oracle",
        "the pre-mutation business backup is missing",
        "supervised restore did not replace the API child process",
    )
    return message.startswith(retryable_prefixes)


def restore_business(api: LoopbackApi, version: str, state_path: Path,
                     supervisor_pid: int, candidate_executable: Path, checkpoint_path: Path,
                     *, runtime_snapshot: dict | None = None,
                     timeout_seconds: float = 45.0) -> dict:
    identity = _runtime_identity(api, version, state_path, supervisor_pid,
                                 candidate_executable, runtime_snapshot=runtime_snapshot)
    checkpoint = _load_checkpoint(checkpoint_path)
    table_id = checkpoint["table_id"]
    columns = checkpoint["columns"]
    baseline = _table_detail(api, table_id)
    zero, negative = _find_rows(baseline, columns)
    api.json("PATCH", f"/api/v1/tables/{table_id}/rows/{zero['id']}", {
        "expected_version": zero["version"],
        "changes": {columns["Note"]: "must disappear after restore"},
        "editor": "release-lifecycle-post-backup",
    })
    extra = api.json("POST", f"/api/v1/tables/{table_id}/rows", {
        "values": {
            columns["Scenario"]: "post-backup-only",
            columns["Amount"]: 999,
            columns["Note"]: "must disappear after restore",
        },
        "editor": "release-lifecycle-post-backup",
    })
    if not isinstance(extra, dict):
        raise VerificationError("post-backup row creation failed")
    changed = _table_detail(api, table_id)
    changed_rows = changed.get("rows")
    if changed.get("row_count") != 3 or not any(
        row.get("values", {}).get(columns["Scenario"]) == "post-backup-only"
        for row in changed_rows if isinstance(row, dict)
    ):
        raise VerificationError("post-backup changes were not persisted before restore")
    if not any(row.get("values", {}).get(columns["Note"]) == "must disappear after restore"
               for row in changed_rows if isinstance(row, dict)):
        raise VerificationError("post-backup row edit was not persisted before restore")

    requested = api.json("POST", f"/api/v1/backups/{checkpoint['backup_name']}/restore", {})
    if not isinstance(requested, dict) or requested.get("scheduled") is not True or requested.get("restart_required") is not True:
        raise VerificationError("restore was not scheduled by the packaged supervisor")

    deadline = time.monotonic() + timeout_seconds
    offline_observed = False
    restore_state = None
    while time.monotonic() < deadline:
        try:
            health = api.json("GET", "/api/v1/health")
            if isinstance(health, dict):
                if health.get("status") != "ok" or health.get("version") != version:
                    raise VerificationError("candidate health changed during restore restart")
                state = _read_json(state_path)
                if state.get("pid") == supervisor_pid:
                    try:
                        status = api.json("GET", "/api/v1/backups/status")
                        restore_state = status.get("restore_state") if isinstance(status, dict) else None
                    except VerificationError:
                        pass
                    if restore_state == "succeeded":
                        break
        except VerificationError as error:
            if "API GET /api/v1/health failed" in str(error):
                offline_observed = True
            else:
                raise
        time.sleep(0.075)
    if restore_state != "succeeded":
        raise VerificationError("supervised restore did not finish successfully before timeout")
    after = None
    last_verification_error = None
    while time.monotonic() < deadline:
        try:
            candidate_state = verify_business(
                api, version, state_path, supervisor_pid, candidate_executable,
                checkpoint_path, runtime_snapshot=runtime_snapshot,
            )
            restored_identity = candidate_state["identity"]
            if restored_identity["supervisor_pid"] != identity["supervisor_pid"]:
                raise VerificationError("supervised restore changed the supervisor process identity")
            if restored_identity["api_pid"] == identity["api_pid"]:
                raise VerificationError("supervised restore did not replace the API child process")
            after = candidate_state
            break
        except VerificationError as error:
            if not _is_restore_transition_error(error):
                raise
            last_verification_error = error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.075, remaining))
    if after is None:
        detail = str(last_verification_error) if last_verification_error else "no verified API identity was observed"
        raise VerificationError(
            "supervised restore did not reach a process-bound, business-verified state before timeout: "
            + detail
        ) from last_verification_error
    restored_identity = after["identity"]
    return {
        "status": "passed",
        "identity": identity,
        "offline_observed": offline_observed,
        "api_process_restarted": True,
        "restored_identity": restored_identity,
        "restore_state": restore_state,
        "pre_restore_row_count": changed["row_count"],
        "restored": after["baseline"],
    }


def _visible_values(raw: str | None) -> dict | None:
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise VerificationError("stored business values are not a JSON object")
    return {key: item for key, item in value.items() if not key.startswith("__")}


def verify_offline_business(data_root: Path, checkpoint_path: Path) -> dict:
    """Read the isolated store after uninstall without launching the package."""
    checkpoint = _load_checkpoint(checkpoint_path)
    data_root = data_root.resolve(strict=True)
    database = data_root / "document-pipeline.db"
    if _is_link(database) or not database.is_file():
        raise VerificationError("business database is missing after uninstall")
    uploads = data_root / "uploads"
    backups = data_root / "backups"
    if _is_link(uploads) or _is_link(backups) or not uploads.is_dir() or not backups.is_dir():
        raise VerificationError("uploads or backups directory is missing or linked after uninstall")

    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise VerificationError(f"SQLite integrity_check failed after uninstall: {integrity}")
        table_count = connection.execute(
            "SELECT count(*) FROM data_tables WHERE id=?", (checkpoint["table_id"],),
        ).fetchone()[0]
        if table_count != 1:
            raise VerificationError("business table is missing after uninstall")
        rows = connection.execute(
            "SELECT id, row_json FROM data_rows WHERE table_id=? AND task_id IS NULL ORDER BY item_index, id",
            (checkpoint["table_id"],),
        ).fetchall()
        expected = checkpoint["baseline"]
        expected_by_id = {
            expected["zero"]["row_id"]: expected["zero"],
            expected["negative"]["row_id"]: expected["negative"],
        }
        if {row_id for row_id, _ in rows} != set(expected_by_id):
            raise VerificationError("offline business row set differs after uninstall")
        columns = checkpoint["columns"]
        visible_by_id = {row_id: _visible_values(raw) for row_id, raw in rows}
        for row_id, item in expected_by_id.items():
            values = visible_by_id[row_id]
            if values.get(columns["Scenario"]) != item["scenario"] or values.get(columns["Note"]) != item["note"]:
                raise VerificationError("offline current business values differ after uninstall")
            amount = values.get(columns["Amount"])
            target_amount = 0 if item["scenario"] == "release-zero" else -12.5
            if not _number_is_exact(amount, target_amount):
                raise VerificationError("offline business number changed type or value")

        history_rows = connection.execute(
            "SELECT version, operation, before_json, after_json, editor "
            "FROM data_row_revisions WHERE row_id=? AND table_id=? AND task_id IS NULL "
            "ORDER BY version, id",
            (expected["zero"]["row_id"], checkpoint["table_id"]),
        ).fetchall()
        history = [
            {"version": version, "operation": operation,
             "before": _visible_values(before_json), "after": _visible_values(after_json),
             "editor": editor}
            for version, operation, before_json, after_json, editor in history_rows
        ]
        if history != checkpoint["zero_history"]:
            raise VerificationError("offline current-data revision history differs after uninstall")
        task_row = connection.execute(
            "SELECT storage_path, sha256 FROM tasks WHERE id=?", (checkpoint["task_id"],),
        ).fetchone()
    finally:
        connection.close()

    if task_row is None or not isinstance(task_row[0], str) or Path(task_row[0]).name != task_row[0]:
        raise VerificationError("synthetic original task or its storage reference is missing")
    original_path = uploads / task_row[0]
    if _is_link(original_path) or not original_path.is_file():
        raise VerificationError("synthetic original file is missing or linked after uninstall")
    original_hash = sha256(original_path)
    if original_hash != checkpoint["original_sha256"] or task_row[1] != original_hash:
        raise VerificationError("synthetic original bytes or database digest changed after uninstall")

    backup = backups / checkpoint["backup_name"]
    if _is_link(backup) or not backup.is_file():
        raise VerificationError("business backup is missing or linked after uninstall")
    backup_hash = sha256(backup)
    if backup_hash != checkpoint["backup_sha256"] or backup.stat().st_size != checkpoint["backup_size_bytes"]:
        raise VerificationError("business backup bytes changed after uninstall")
    return {
        "status": "passed",
        "sqlite_integrity_check": integrity,
        "business_rows": len(rows),
        "zero_row_revision_count": len(history),
        "original_sha256": original_hash,
        "backup_sha256": backup_hash,
    }


def _write_report(value: dict, path: Path | None) -> None:
    serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidate_parser = subparsers.add_parser("verify-candidate")
    candidate_parser.add_argument("--dist-root", type=Path, required=True)
    candidate_parser.add_argument("--version")
    candidate_parser.add_argument("--report", type=Path)

    installed_parser = subparsers.add_parser("verify-installed")
    installed_parser.add_argument("--install-root", type=Path, required=True)
    installed_parser.add_argument("--payload-manifest", type=Path, required=True)
    installed_parser.add_argument("--report", type=Path)

    source_parser = subparsers.add_parser("verify-build-sources")
    source_parser.add_argument("--dist-root", type=Path, required=True)
    source_parser.add_argument("--project-root", type=Path, required=True)
    source_parser.add_argument("--path", action="append", required=True)
    source_parser.add_argument("--report", type=Path)

    for name in ("prepare-business", "verify-business", "restore-business"):
        api_parser = subparsers.add_parser(name)
        api_parser.add_argument("--base-url", required=True)
        api_parser.add_argument("--version", required=True)
        api_parser.add_argument("--supervisor-state", type=Path, required=True)
        api_parser.add_argument("--supervisor-pid", type=int, required=True)
        api_parser.add_argument("--candidate-executable", type=Path, required=True)
        api_parser.add_argument("--checkpoint", type=Path, required=True)
        api_parser.add_argument("--report", type=Path)
        if name == "restore-business":
            api_parser.add_argument("--timeout-seconds", type=float, default=45.0)

    offline_parser = subparsers.add_parser("verify-offline-business")
    offline_parser.add_argument("--data-root", type=Path, required=True)
    offline_parser.add_argument("--checkpoint", type=Path, required=True)
    offline_parser.add_argument("--report", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "verify-candidate":
            result = {"status": "passed", "candidate": verify_candidate(args.dist_root, args.version)}
        elif args.command == "verify-installed":
            manifest = _read_json(args.payload_manifest)
            result = {"status": "passed", "installed_payload": verify_payload_root(
                args.install_root, manifest, allowed_extra={"Uninstall.exe"},
            )}
        elif args.command == "verify-build-sources":
            result = {"status": "passed", "build_sources": verify_build_sources(
                args.dist_root, args.project_root, args.path,
            )}
        elif args.command == "verify-offline-business":
            result = verify_offline_business(args.data_root, args.checkpoint)
        else:
            api = LoopbackApi(args.base_url)
            if args.command == "prepare-business":
                value = prepare_business(api, args.version, args.supervisor_state,
                                         args.supervisor_pid, args.candidate_executable, args.checkpoint)
            elif args.command == "verify-business":
                value = verify_business(api, args.version, args.supervisor_state,
                                        args.supervisor_pid, args.candidate_executable, args.checkpoint)
            else:
                value = restore_business(api, args.version, args.supervisor_state,
                                         args.supervisor_pid, args.candidate_executable, args.checkpoint,
                                         timeout_seconds=args.timeout_seconds)
            result = value
        _write_report(result, args.report)
        return 0
    except (OSError, VerificationError, ValueError, KeyError, TypeError) as error:
        result = {"status": "failed", "error": str(error)}
        _write_report(result, getattr(args, "report", None))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
