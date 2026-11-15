"""R3 localhost HTTP + official stdio MCP acceptance, confined to one data dir."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = (ROOT / ".local" / "v050-product-r3-protocol").resolve()
DATA_DIR = Path(os.environ.get("R3_DATA_DIR", str(DATA_ROOT))).resolve()
PORT = 8822
BASE = f"http://127.0.0.1:{PORT}"
EVIDENCE_PATH = DATA_DIR / "cap-evidence.json"
EVIDENCE_OWNED = False


class AcceptanceFailure(AssertionError):
    pass


EVIDENCE: dict[str, Any] = {
    "schema_version": 1,
    "product_version": "0.5.0",
    "run_id": DATA_DIR.name,
    "workspace": str(ROOT),
    "data_dir": str(DATA_DIR),
    "api_origin": BASE,
    "mcp_transport": "official mcp.client.stdio ClientSession + product launcher command",
    "model_calls": 0,
    "model_api_key_read": False,
    "checks": [],
    "snapshots": [],
    "summary": {"status": "running"},
}


def record(check_id: str, cap_ids: list[str], passed: bool, detail: str, **facts: Any) -> None:
    row = {
        **facts,
        "check_id": check_id,
        "capability_ids": cap_ids,
        "status": "passed" if passed else "failed",
        "detail": detail,
    }
    EVIDENCE["checks"].append(row)
    if not passed:
        raise AcceptanceFailure(f"{check_id}: {detail}")


def _safe_process_environment(data_dir: Path) -> dict[str, str]:
    """Pass only OS/runtime variables to the product launcher, never user credentials."""
    allow = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allow}
    result["DOCUMENT_PIPELINE_DATA_DIR"] = str(data_dir)
    result["UV_OFFLINE"] = "1"
    return result


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _db_snapshot(database: Path, *, task_id: str | None = None, row_id: int | None = None) -> dict[str, Any]:
    uri = database.as_uri() + "?mode=ro"
    source = sqlite3.connect(uri, uri=True, timeout=10)
    source.row_factory = sqlite3.Row
    image = sqlite3.connect(":memory:")
    image.row_factory = sqlite3.Row
    try:
        source.backup(image)
        tables = {row[0] for row in image.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for table in ("tasks", "data_tables", "data_rows", "data_row_revisions", "file_current_data", "file_revisions", "file_revision_details", "extractions"):
            if table in tables:
                counts[table] = image.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        selected = {}
        if task_id and "file_current_data" in tables:
            current = image.execute(
                "SELECT current_version, header_json, has_data FROM file_current_data WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if current:
                selected["file_current"] = {
                    "version": current["current_version"],
                    "header": json.loads(current["header_json"]),
                    "has_data": bool(current["has_data"]),
                }
            history = image.execute(
                "SELECT id, version, operation, editor, changes_json FROM file_revisions WHERE task_id=? ORDER BY version",
                (task_id,),
            ).fetchall()
            selected["file_history"] = [
                {"id": item["id"], "version": item["version"], "operation": item["operation"],
                 "editor": item["editor"], "changes": json.loads(item["changes_json"])}
                for item in history
            ]
        if row_id is not None and "data_rows" in tables:
            row = image.execute("SELECT id, task_id, row_version, row_json FROM data_rows WHERE id=?", (row_id,)).fetchone()
            selected["data_row"] = None if row is None else {
                "id": row["id"], "task_id": row["task_id"], "version": row["row_version"],
                "values": json.loads(row["row_json"]),
            }
        serialized = image.serialize()
        return {"sha256": _hash_bytes(serialized), "row_counts": counts, "selected": selected}
    finally:
        image.close()
        source.close()


def _json(response):
    try:
        return response.json()
    except (ValueError, AttributeError):
        return None


def _expect(response, status: int, label: str):
    if response.status_code != status:
        detail = _json(response)
        raise AcceptanceFailure(f"{label}: expected HTTP {status}, received {response.status_code}; body={detail!r}")
    return _json(response)


def _parse_mcp(result):
    if result.structuredContent is not None:
        value = result.structuredContent
        if isinstance(value, dict) and set(value) == {"result"}:
            return value["result"]
        return value
    text = result.content[0].text if result.content else "null"
    return json.loads(text)


def _add_capability_evidence() -> None:
    mapping: dict[str, list[dict[str, str]]] = {}
    for check in EVIDENCE["checks"]:
        for capability_id in check["capability_ids"]:
            mapping.setdefault(capability_id, []).append(
                {"check_id": check["check_id"], "status": check["status"]}
            )
    EVIDENCE["capability_evidence"] = {
        capability_id: {
            "check_count": len(checks),
            "passed": sum(item["status"] == "passed" for item in checks),
            "failed": sum(item["status"] != "passed" for item in checks),
            "check_ids": [item["check_id"] for item in checks],
            "status": "passed" if all(item["status"] == "passed" for item in checks) else "failed",
        }
        for capability_id, checks in sorted(mapping.items())
    }


async def main_async() -> None:
    global EVIDENCE_OWNED
    EVIDENCE_OWNED = False
    if DATA_DIR != DATA_ROOT and DATA_ROOT not in DATA_DIR.parents:
        raise RuntimeError(f"R3_DATA_DIR must remain inside the assigned isolated root: {DATA_ROOT}")
    if DATA_DIR == DATA_ROOT and DATA_DIR.exists():
        raise RuntimeError(f"Refusing to reuse or overwrite existing isolated data directory: {DATA_DIR}")
    DATA_DIR.mkdir(parents=True, exist_ok=False)
    # Only a newly created run owns its evidence file. Refused reruns must not
    # overwrite a previous run's report, even on the exception path.
    with EVIDENCE_PATH.open("x", encoding="utf-8"):
        pass
    EVIDENCE_OWNED = True
    fixtures = DATA_DIR / "fixture"
    files_root = fixtures / "allowed-files"
    export_root = files_root / "exports"
    files_root.mkdir(parents=True)
    export_root.mkdir(parents=True)
    source_path = files_root / "r3-offline-acceptance.txt"
    source_bytes = "R3_PROTOCOL_OFFLINE_FIXTURE. Synthetic local acceptance text; no personal data.\n".encode("utf-8")
    source_path.write_bytes(source_bytes)
    outside_path = fixtures / "outside.txt"
    outside_path.write_text("synthetic outside file\n", encoding="utf-8", newline="\n")

    os.environ["DOCUMENT_PIPELINE_DATA_DIR"] = str(DATA_DIR)
    os.environ["DOCUMENT_PIPELINE_QUEUE_DB"] = str(DATA_DIR / "queue.db")

    from document_pipeline_api.config import Settings
    from document_pipeline_api.main import create_app
    from document_pipeline_api.services.extraction import process_task
    from document_pipeline_api.schemas.templates import TemplateMatchDecision
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    import httpx
    import uvicorn

    database = DATA_DIR / "document-pipeline.db"
    settings = Settings(
        database_url=f"sqlite:///{database.as_posix()}",
        storage_dir=DATA_DIR / "uploads",
        model_provider="offline_fixture",
        model_base_url="http://127.0.0.1:1/v1",
        model_name="offline-r3-only",
        model_api_key="",
        model_reasoning_effort="none",
        queue_enabled=False,
        recover_local_model_on_startup=False,
        development_origins_enabled=False,
    )
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False))
    api_thread = threading.Thread(target=server.run, name="r3-api-8822", daemon=True)
    api_thread.start()
    client = httpx.Client(base_url=BASE, timeout=15)
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            if not api_thread.is_alive():
                raise RuntimeError("isolated API server exited before startup")
            if time.monotonic() >= deadline:
                raise TimeoutError("isolated API server did not start on 127.0.0.1:8822")
            time.sleep(0.1)
        _expect(client.get("/api/v1/health"), 200, "health")
        record("R3-HTTP-BOOT", ["CAP-090"], True, "isolated local API listening on 127.0.0.1:8822", port=PORT)

        def api(method: str, path: str, **kwargs):
            return client.request(method, path, **kwargs)

        def save_permissions(**updates):
            body = {
                "task_read": False,
                "template_read": False,
                "result_read": False,
                "data_read": False,
                "task_control": False,
                "write_enabled": False,
                "file_access": False,
                "file_roots": [],
                "data_scope": "all",
                "table_ids": [],
            }
            body.update(updates)
            _expect(api("POST", "/api/v1/integration/settings/permissions", json=body), 200, "save MCP permissions")

        def get_settings():
            return _expect(api("GET", "/api/v1/integration/settings"), 200, "read integration status")

        # The HTTP API is closed by default, and product launcher permissions expose only the policy tool.
        no_key = _expect(api("GET", "/api/integration/v1/capabilities"), 503, "HTTP integration default deny")
        record("R3-HTTP-DEFAULT-DENY", ["CAP-090"], True, "HTTP integration returns 503 before any key is configured", response_detail=no_key.get("detail"))

        cfg = _expect(api("GET", "/api/v1/integration/settings/mcp-config"), 200, "actual MCP launcher config")
        EVIDENCE["launcher_config"] = cfg
        save_permissions()
        server_cfg = cfg["server"]
        safe_env = _safe_process_environment(DATA_DIR)
        params = StdioServerParameters(
            command=server_cfg["command"],
            args=server_cfg["args"],
            env=safe_env,
            cwd=str(ROOT),
        )

        @asynccontextmanager
        async def mcp_client():
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session

        async with mcp_client() as denied:
            denied_tools = {tool.name for tool in (await denied.list_tools()).tools}
            denied_caps = _parse_mcp(await denied.call_tool("get_capabilities", {}))
            record(
                "R3-MCP-DEFAULT-DENY", ["CAP-090"],
                denied_tools == {"get_capabilities"} and denied_caps["read"] == []
                and denied_caps["fact_write"] is False and denied_caps["file_access"] is False,
                "product launcher starts a real stdio server with no granted business tools",
                tool_names=sorted(denied_tools), capability_reads=denied_caps["read"],
            )

        # API read/write keys are created and rotated through the same local settings UI endpoints.
        read_key_1 = _expect(api("POST", "/api/v1/integration/settings/keys", json={"kind": "read"}), 200, "create first read key")["token"]
        write_key_1 = _expect(api("POST", "/api/v1/integration/settings/keys", json={"kind": "write"}), 200, "create first write key")["token"]
        key_headers_read_1 = {"Authorization": f"Bearer {read_key_1}"}
        key_headers_write_1 = {"Authorization": f"Bearer {write_key_1}"}
        caps_read_1 = _expect(api("GET", "/api/integration/v1/capabilities", headers=key_headers_read_1), 200, "read key capability")
        record("R3-HTTP-READ-KEY", ["CAP-090"], caps_read_1["scope"] == "read" and caps_read_1["write"] == [],
               "read key is accepted as read scope and advertises no write operations", scope=caps_read_1["scope"], write_operations=caps_read_1["write"])

        # Create two ordinary user templates through HTTP; the deterministic model will choose
        # 'ambiguous', making MCP template selection a real state transition, with no network model.
        template_body_a = {
            "name": "R3 离线验收模板 A", "description": "R3_PROTOCOL_OFFLINE_FIXTURE synthetic test only",
            "fields": [{"key": "title", "label": "标题", "section": "header", "value_type": "text"}],
        }
        template_body_b = {
            "name": "R3 离线验收模板 B", "description": "R3_PROTOCOL_OFFLINE_FIXTURE second candidate",
            "fields": [{"key": "title", "label": "标题", "section": "header", "value_type": "text"}],
        }
        template_a = _expect(api("POST", "/api/v1/templates", json=template_body_a), 201, "create offline template A")
        template_b = _expect(api("POST", "/api/v1/templates", json=template_body_b), 201, "create offline template B")
        template_a = _expect(api("PUT", f"/api/v1/templates/{template_a['id']}/smart-pool", json={"in_smart_pool": True}), 200, "enable template A smart pool")
        template_b = _expect(api("PUT", f"/api/v1/templates/{template_b['id']}/smart-pool", json={"in_smart_pool": True}), 200, "enable template B smart pool")
        assert template_a["in_smart_pool"] and template_b["in_smart_pool"]
        api_table_2 = _expect(api("POST", "/api/v1/tables", json={"name": "R3 scope control table", "template_key": template_a["id"]}), 201, "create scope control table")

        save_permissions(
            task_read=True, template_read=True, result_read=True, data_read=True,
            task_control=True, write_enabled=True, file_access=True,
            file_roots=[str(files_root.resolve())], data_scope="all",
        )

        class OfflineModel:
            model_name = "offline-r3-no-network"
            calls = 0

            def complete_text(self, prompt, result_type):
                self.calls += 1
                if result_type is TemplateMatchDecision:
                    if "R3_PROTOCOL_OFFLINE_FIXTURE" not in prompt:
                        raise AssertionError("matching prompt did not include the synthetic marker")
                    for template_id in (template_a["id"], template_b["id"]):
                        if template_id not in prompt:
                            raise AssertionError("the matching prompt did not include both acceptance templates")
                    return TemplateMatchDecision(outcome="ambiguous", template_ids=[template_a["id"], template_b["id"]])
                return result_type.model_validate({"header": {"title": "R3 初始事实"}, "items": []})

        offline_model = OfflineModel()
        EVIDENCE["model_calls"] = 0

        async with mcp_client() as mcp:
            full_tools = {tool.name for tool in (await mcp.list_tools()).tools}
            caps = _parse_mcp(await mcp.call_tool("get_capabilities", {}))
            assert {"create_task_from_file", "control_task", "select_task_template", "update_data_row", "get_task_result", "get_data_table", "export_data_table"} <= full_tools
            assert caps["raw_file_read"] is False and caps["table_export"] is True
            record("R3-MCP-GRANTED-TOOLS", ["CAP-090"], True, "full policy registers task, data, write, and export tools; raw original-file read stays disabled",
                   tool_count=len(full_tools), raw_file_read=caps["raw_file_read"], table_export=caps["table_export"])

            traversal = await mcp.call_tool("create_task_from_file", {"file_path": str(outside_path), "template_id": template_a["id"]})
            record("R3-MCP-FILE-IMPORT-ESCAPE", ["CAP-090"], bool(traversal.isError), "file import rejects a source outside the configured root")

            imported_result = await mcp.call_tool("create_task_from_file", {"file_path": str(source_path), "template_mode": "smart"})
            if imported_result.isError:
                raise AcceptanceFailure(f"MCP file import failed: {_parse_mcp(imported_result)}")
            task = _parse_mcp(imported_result)
            task_id = task["id"]
            record("R3-MCP-CREATE-TASK", ["CAP-090"], task["status"] in {"queued", "created"}, "MCP created an isolated synthetic task through the product upload service",
                   task_id=task_id, observed_task_status=task["status"], source_sha256=_hash_bytes(source_bytes))

            paused = _parse_mcp(await mcp.call_tool("control_task", {"task_id": task_id, "action": "pause"}))
            resumed = _parse_mcp(await mcp.call_tool("control_task", {"task_id": task_id, "action": "resume"}))
            record("R3-MCP-PAUSE-RESUME", ["CAP-090"], paused["status"] == "paused" and resumed["status"] == "queued",
                   "MCP pause/resume follows task state machine; no worker is running", paused_status=paused["status"], resumed_status=resumed["status"])

            with app.state.session_factory() as session:
                match_result = process_task(session, settings, task_id, client=offline_model)
            EVIDENCE["model_calls"] = offline_model.calls
            task_after_match = _parse_mcp(await mcp.call_tool("get_task", {"task_id": task_id}))
            record("R3-OFFLINE-MATCH", ["CAP-090"], match_result is None and task_after_match["status"] == "waiting_for_template",
                   "explicit offline fixture produced an ambiguous match, never contacting a configured provider",
                   observed_task_status=task_after_match["status"], model_name=offline_model.model_name)

            selected = _parse_mcp(await mcp.call_tool("select_task_template", {"task_id": task_id, "template_id": template_a["id"]}))
            record("R3-MCP-TEMPLATE-SELECT", ["CAP-090"], selected["status"] == "queued" and selected["template_id"] == template_a["id"],
                   "MCP template selection resolves waiting task to the selected active template", observed_task_status=selected["status"], template_id=selected["template_id"])

            with app.state.session_factory() as session:
                extraction = process_task(session, settings, task_id, client=offline_model)
            EVIDENCE["model_calls"] = offline_model.calls
            if extraction is None:
                raise AcceptanceFailure("offline extraction did not produce a result")
            task_after_extract = _parse_mcp(await mcp.call_tool("get_task", {"task_id": task_id}))
            record("R3-OFFLINE-EXTRACT", ["CAP-090"], task_after_extract["status"] in {"needs_review", "completed"},
                   "the selected template extracted only the synthetic title with injected offline model", observed_task_status=task_after_extract["status"], review_version=extraction.review_version)

            confirmed = _expect(api("POST", f"/api/v1/tasks/{task_id}/confirm", json={"expected_review_version": extraction.review_version}), 200, "confirm offline result")
            table_id = confirmed["table_id"]
            table_detail = _expect(api("GET", f"/api/v1/tables/{table_id}"), 200, "read confirmed table")
            assert len(table_detail["rows"]) == 1
            table_row = table_detail["rows"][0]
            row_id = table_row["id"]
            row_version = table_row["version"]
            record("R3-HTTP-CONFIRM", ["CAP-090"], confirmed["row_count"] == 1 and table_row["values"].get("title") == "R3 初始事实",
                   "HTTP confirmation materialized one current file row and identifies its table", table_id=table_id, row_id=row_id, row_version=row_version)

            # An explicitly selected table is visible; a direct query outside scope fails.
            save_permissions(task_read=True, template_read=True, result_read=True, data_read=True,
                             task_control=True, write_enabled=True, file_access=True,
                             file_roots=[str(files_root.resolve())], data_scope="selected", table_ids=[table_id])
            old_selected_call = await mcp.call_tool("get_capabilities", {})
            record("R3-MCP-OLD-CONNECTION-REVOKED", ["CAP-090"], old_selected_call.isError,
                   "saving a changed policy invalidates an already connected client immediately")

        async with mcp_client() as selected_mcp:
            allowed_tables = _parse_mcp(await selected_mcp.call_tool("list_data_tables", {}))
            allowed_get = await selected_mcp.call_tool("get_data_table", {"table_id": table_id})
            denied_get = await selected_mcp.call_tool("get_data_table", {"table_id": api_table_2["id"]})
            record("R3-MCP-SELECTED-TABLES", ["CAP-090"], [item["id"] for item in allowed_tables] == [table_id]
                   and not allowed_get.isError and denied_get.isError,
                   "selected scope lists only the explicitly authorized table and rejects a direct out-of-scope read",
                   authorized_table_count=len(allowed_tables), denied_table_call_error=bool(denied_get.isError))

            # Selected scope with an empty set means no data access, including list enumeration.
            save_permissions(task_read=True, template_read=True, result_read=True, data_read=True,
                             task_control=True, write_enabled=True, file_access=True,
                             file_roots=[str(files_root.resolve())], data_scope="selected", table_ids=[])
            empty_old = await selected_mcp.call_tool("get_capabilities", {})
            record("R3-MCP-EMPTY-SCOPE-INVALIDATES", ["CAP-090"], empty_old.isError,
                   "selected-scope change invalidates the previous connected client")

        async with mcp_client() as empty_mcp:
            empty_caps = _parse_mcp(await empty_mcp.call_tool("get_capabilities", {}))
            empty_tables = _parse_mcp(await empty_mcp.call_tool("list_data_tables", {}))
            denied_empty = await empty_mcp.call_tool("get_data_table", {"table_id": table_id})
            record("R3-MCP-EMPTY-SCOPE", ["CAP-090"], empty_caps["allowed_table_ids"] == [] and empty_tables == [] and denied_empty.isError,
                   "selected scope with an empty authorization has no list or direct-read access")

            save_permissions(task_read=True, template_read=True, result_read=True, data_read=True,
                             task_control=True, write_enabled=True, file_access=True,
                             file_roots=[str(files_root.resolve())], data_scope="all")
            empty_old_reauth = await empty_mcp.call_tool("get_capabilities", {})
            record("R3-MCP-OLD-CONNECTION-NO-RESURRECTION", ["CAP-090"], empty_old_reauth.isError,
                   "granting permissions again does not revive a connection invalidated by revocation")

        async with mcp_client() as live_mcp:
            live_caps = _parse_mcp(await live_mcp.call_tool("get_capabilities", {}))
            record("R3-MCP-NEW-CONNECTION-REAUTHORIZED", ["CAP-090"], live_caps["data_read"] is True and live_caps["fact_write"] is True,
                   "new launcher process receives the newly granted policy")

            # HTTP read token cannot edit the row; both semantic snapshot and revision history stay unchanged.
            read_key_2 = _expect(api("POST", "/api/v1/integration/settings/keys", json={"kind": "read"}), 200, "rotate read key")["token"]
            write_key_2 = _expect(api("POST", "/api/v1/integration/settings/keys", json={"kind": "write"}), 200, "rotate write key")["token"]
            old_read_status = api("GET", "/api/integration/v1/capabilities", headers=key_headers_read_1).status_code
            old_write_status = api("GET", "/api/integration/v1/capabilities", headers=key_headers_write_1).status_code
            read_headers = {"Authorization": f"Bearer {read_key_2}"}
            write_headers = {"Authorization": f"Bearer {write_key_2}"}
            record("R3-HTTP-KEY-ROTATION", ["CAP-090"], old_read_status == 401 and old_write_status == 401
                   and api("GET", "/api/integration/v1/capabilities", headers=read_headers).status_code == 200,
                   "key rotation invalidates both prior credentials and immediately accepts the new read key",
                   old_read_status=old_read_status, old_write_status=old_write_status)

            before_forbidden = _db_snapshot(database, task_id=task_id, row_id=row_id)
            read_edit = api("PATCH", f"/api/integration/v1/tables/{table_id}/rows/{row_id}",
                            headers=read_headers, json={"expected_version": row_version, "changes": {"title": "R3 unauthorized"}})
            read_after_forbidden = _db_snapshot(database, task_id=task_id, row_id=row_id)
            record("R3-HTTP-READ-CANNOT-WRITE", ["CAP-090"], read_edit.status_code == 403
                   and before_forbidden["sha256"] == read_after_forbidden["sha256"]
                   and before_forbidden["row_counts"] == read_after_forbidden["row_counts"],
                   "read-only key receives 403 and database snapshot, row counts, and history remain unchanged",
                   status_code=read_edit.status_code, sha256_before=before_forbidden["sha256"], sha256_after=read_after_forbidden["sha256"])

            status_view = get_settings()
            record("R3-HTTP-KEY-NOT-READ-BACK", ["CAP-090"], status_view.get("read_token_set") is True
                   and status_view.get("write_token_set") is True and "token" not in status_view,
                   "status endpoint reports configured state without returning token material")

            write_edit = api("PATCH", f"/api/integration/v1/tables/{table_id}/rows/{row_id}",
                             headers=write_headers, json={"expected_version": row_version, "changes": {"title": "R3 HTTP 已核对"}})
            write_payload = _expect(write_edit, 200, "write-key row update")
            updated_version = write_payload["version"]
            updated_snapshot = _db_snapshot(database, task_id=task_id, row_id=row_id)
            EVIDENCE["snapshots"].append({"name": "after_http_write", **updated_snapshot})
            record("R3-HTTP-WRITE-KEY", ["CAP-090"], write_payload["values"].get("title") == "R3 HTTP 已核对"
                   and updated_version == row_version + 1
                   and updated_snapshot["row_counts"]["data_rows"] == before_forbidden["row_counts"]["data_rows"]
                   and updated_snapshot["row_counts"]["file_revisions"] == before_forbidden["row_counts"]["file_revisions"] + 1,
                   "write key updates one current row, advances its version, and appends one file revision",
                   version_before=row_version, version_after=updated_version,
                   history_count_before=before_forbidden["row_counts"]["file_revisions"],
                   history_count_after=updated_snapshot["row_counts"]["file_revisions"],
                   sha256_before=before_forbidden["sha256"], sha256_after=updated_snapshot["sha256"])

            # A concurrent stale edit is rejected and cannot create a second revision or alter any row.
            before_stale = _db_snapshot(database, task_id=task_id, row_id=row_id)
            stale_response = api("PATCH", f"/api/integration/v1/tables/{table_id}/rows/{row_id}",
                                 headers=write_headers, json={"expected_version": row_version, "changes": {"title": "R3 stale overwrite"}})
            after_stale = _db_snapshot(database, task_id=task_id, row_id=row_id)
            record("R3-HTTP-STALE-409", ["CAP-090"], stale_response.status_code == 409
                   and before_stale["sha256"] == after_stale["sha256"]
                   and before_stale["row_counts"] == after_stale["row_counts"]
                   and before_stale["selected"] == after_stale["selected"],
                   "stale expected_version returns 409 with identical database snapshot, row counts, current fact, and history",
                   status_code=stale_response.status_code, sha256_before=before_stale["sha256"], sha256_after=after_stale["sha256"],
                   row_counts=after_stale["row_counts"])

            http_result = _expect(api("GET", f"/api/v1/tasks/{task_id}/result"), 200, "read current result")
            current_details = _expect(api("GET", f"/api/v1/tasks/{task_id}/current-details"), 200, "read file current details")
            current_files = _expect(api("GET", f"/api/v1/tables/{table_id}/files"), 200, "read current file collection")
            history = _expect(api("GET", f"/api/v1/tasks/{task_id}/revisions"), 200, "list current file revisions")
            latest_revision = next(item for item in history["revisions"] if item["editor"] == "integration-api")
            history_detail = _expect(api("GET", f"/api/v1/tasks/{task_id}/revisions/{latest_revision['id']}"), 200, "read updated file revision")
            mcp_table = _parse_mcp(await live_mcp.call_tool("get_data_table", {"table_id": table_id}))
            mcp_result = _parse_mcp(await live_mcp.call_tool("get_task_result", {"task_id": task_id}))
            export_path = export_root / "r3-current.json"
            export_args = {"table_id": table_id, "output_path": str(export_path), "format": "json"}
            exported = await live_mcp.call_tool("export_data_table", export_args)
            if exported.isError:
                raise AcceptanceFailure(f"allowed table export failed: {_parse_mcp(exported)}")
            export_result = _parse_mcp(exported)
            export_payload = json.loads(export_path.read_text(encoding="utf-8-sig"))
            current_title = http_result["result"]["header"].get("title")
            same_fact_checks = {
                "http_result": current_title == "R3 HTTP 已核对",
                "file_current_details": current_details["header"].get("title") == current_title,
                "file_collection": current_files["files"][0]["header"].get("title") == current_title,
                "mcp_result": mcp_result["result"]["header"].get("title") == current_title,
                "mcp_table": any(item["values"].get("title") == current_title for item in mcp_table["rows"]),
                "export_row_count": export_result["row_count"] == 1,
                "export_fact": current_title in json.dumps(export_payload, ensure_ascii=False),
                "history_fact": history_detail["header"]["title"]["after"] == current_title,
            }
            record("R3-HTTP-MCP-FILE-HISTORY-SAME-FACT", ["CAP-090"], all(same_fact_checks.values()),
                   "one HTTP edit is visible in current result, file current, MCP current/table, file export, and durable file history",
                   comparisons=same_fact_checks,
                   current_version=http_result["review_version"], history_revision=latest_revision["version"],
                   exported_sha256=_hash_bytes(export_path.read_bytes()), oracle_sha256=updated_snapshot["sha256"])

            # Export cannot overwrite; outside-root import and export both fail.
            export_hash_before = _hash_bytes(export_path.read_bytes())
            overwrite_result = await live_mcp.call_tool("export_data_table", export_args)
            overwrite_hash_after = _hash_bytes(export_path.read_bytes())
            escape_export = await live_mcp.call_tool("export_data_table", {**export_args, "output_path": str(fixtures / "../escape.json")})
            record("R3-MCP-EXPORT-BOUNDARIES", ["CAP-090"], overwrite_result.isError and escape_export.isError
                   and export_hash_before == overwrite_hash_after,
                   "MCP export refuses overwrite and paths outside configured roots; existing export bytes are unchanged",
                   overwrite_refused=bool(overwrite_result.isError), traversal_refused=bool(escape_export.isError),
                   existing_export_sha256=export_hash_before)

            # Revoke access while this stdio session remains connected, then restore the grant.
            save_permissions(task_read=True, template_read=True, result_read=True, data_read=False,
                             task_control=False, write_enabled=False, file_access=True,
                             file_roots=[str(files_root.resolve())], data_scope="all")
            revoked_call = await live_mcp.call_tool("get_capabilities", {})
            record("R3-MCP-LIVE-REVOCATION", ["CAP-090"], revoked_call.isError,
                   "permission revocation blocks calls on a running MCP connection")
            save_permissions(task_read=True, template_read=True, result_read=True, data_read=True,
                             task_control=True, write_enabled=True, file_access=True,
                             file_roots=[str(files_root.resolve())], data_scope="all")
            revived_call = await live_mcp.call_tool("get_capabilities", {})
            record("R3-MCP-LIVE-REAUTH-NO-REVIVAL", ["CAP-090"], revived_call.isError,
                   "the same old MCP connection remains invalid after reauthorization")

        save_permissions(task_read=True, template_read=True, result_read=True, data_read=False,
                         task_control=False, write_enabled=False, file_access=True,
                         file_roots=[str(files_root.resolve())], data_scope="all")
        async with mcp_client() as export_denied:
            caps_no_data = _parse_mcp(await export_denied.call_tool("get_capabilities", {}))
            no_data_tools = {tool.name for tool in (await export_denied.list_tools()).tools}
            record("R3-MCP-EXPORT-REQUIRES-DATA-READ", ["CAP-090"], caps_no_data["file_access"] is True
                   and caps_no_data["data_read"] is False and "export_data_table" not in no_data_tools,
                   "file access alone does not register table export without data-read permission",
                   export_tool_registered="export_data_table" in no_data_tools)

        save_permissions(task_read=True, template_read=True, result_read=True, data_read=True,
                         task_control=True, write_enabled=True, file_access=True,
                         file_roots=[str(files_root.resolve())], data_scope="all")
        async with mcp_client() as ready_for_restore:
            # Restore is deliberately last: its generation fence must invalidate this live session.
            backup = _expect(api("POST", "/api/v1/backups"), 201, "create business backup")
            backup_name = backup["name"]
            backup_bytes = (DATA_DIR / "backups" / backup_name).stat().st_size
            pre_restore_version = _expect(api("GET", f"/api/v1/tables/{table_id}"), 200, "read row before restore")["rows"][0]["version"]
            later_edit = api("PATCH", f"/api/integration/v1/tables/{table_id}/rows/{row_id}",
                             headers=write_headers, json={"expected_version": pre_restore_version, "changes": {"title": "R3 post-backup fact"}})
            _expect(later_edit, 200, "post-backup edit")
            before_restore = _db_snapshot(database, task_id=task_id, row_id=row_id)
            raw_db_hash_before = _hash_bytes(database.read_bytes())
            live_restore_response = api("POST", f"/api/v1/backups/{backup_name}/restore")
            if live_restore_response.status_code == 409:
                after_failed_restore = _db_snapshot(database, task_id=task_id, row_id=row_id)
                raw_db_hash_after = _hash_bytes(database.read_bytes())
                old_after_failed_restore = await ready_for_restore.call_tool("get_capabilities", {})
                record("R3-RESTORE-LIVE-HANDLE-REPRO", ["CAP-089", "CAP-090"],
                       before_restore["sha256"] == after_failed_restore["sha256"]
                       and before_restore["row_counts"] == after_failed_restore["row_counts"]
                       and before_restore["selected"] == after_failed_restore["selected"],
                       "restore returned 409 with the live MCP stdio process; rollback retained the current row, row counts, history, and SQLite snapshot",
                       status_code=live_restore_response.status_code,
                       raw_database_sha256_before=raw_db_hash_before,
                       raw_database_sha256_after=raw_db_hash_after,
                       snapshot_sha256_before=before_restore["sha256"],
                       snapshot_sha256_after=after_failed_restore["sha256"],
                       row_counts_before=before_restore["row_counts"],
                       row_counts_after=after_failed_restore["row_counts"],
                       current_and_history_unchanged=before_restore["selected"] == after_failed_restore["selected"],
                       old_connection_rejected=bool(old_after_failed_restore.isError))
            else:
                restore_result = _expect(live_restore_response, 200, "restore business backup with live MCP")
                restored_snapshot = _db_snapshot(database, task_id=task_id, row_id=row_id)
                old_after_restore = await ready_for_restore.call_tool("get_capabilities", {})
                restored_title = _expect(api("GET", f"/api/v1/tasks/{task_id}/result"), 200, "read restored result")["result"]["header"].get("title")
                record("R3-RESTORE-GENERATION", ["CAP-089", "CAP-090"], restore_result["restart_required"] is True
                       and restore_result["scheduled"] is False
                       and old_after_restore.isError
                       and restored_title == "R3 HTTP 已核对"
                       and before_restore["sha256"] != restored_snapshot["sha256"],
                       "direct development restore reinstates the backed-up fact and generation-invalidates an already connected MCP client",
                       restore_scheduled=restore_result["scheduled"], restart_required=restore_result["restart_required"],
                       backup_size_bytes=backup_bytes, old_connection_rejected=bool(old_after_restore.isError),
                       title_after_restore=restored_title,
                       before_restore_sha256=before_restore["sha256"], restored_sha256=restored_snapshot["sha256"],
                       before_restore_row_counts=before_restore["row_counts"],
                       restored_row_counts=restored_snapshot["row_counts"],
                       restored_file_revision_count=len(restored_snapshot["selected"].get("file_history", [])))

        if live_restore_response.status_code == 409:
            closed_restore = _expect(api("POST", f"/api/v1/backups/{backup_name}/restore"), 200, "restore same backup after MCP client closed")
            closed_snapshot = _db_snapshot(database, task_id=task_id, row_id=row_id)
            closed_title = _expect(api("GET", f"/api/v1/tasks/{task_id}/result"), 200, "read result after closed-client restore")["result"]["header"].get("title")
            record("R3-RESTORE-AFTER-MCP-CLOSE", ["CAP-089", "CAP-090"],
                   closed_restore["restart_required"] is True and closed_title == "R3 HTTP 已核对",
                   "the same business backup restored after the official MCP stdio context closed normally",
                   backup_name=backup_name, backup_size_bytes=backup_bytes,
                   restored_title=closed_title, restored_sha256=closed_snapshot["sha256"],
                   restored_row_counts=closed_snapshot["row_counts"],
                   restored_file_revision_count=len(closed_snapshot["selected"].get("file_history", [])))

        async with mcp_client() as post_restore:
            post_caps = _parse_mcp(await post_restore.call_tool("get_capabilities", {}))
            post_table = _parse_mcp(await post_restore.call_tool("get_data_table", {"table_id": table_id}))
            record("R3-RESTORE-NEW-CONNECTION", ["CAP-089", "CAP-090"], post_caps["data_read"] is True
                   and any(row["values"].get("title") == "R3 HTTP 已核对" for row in post_table["rows"]),
                   "new MCP connection after restore opens on current generation and reads restored state")

        # Final independent key revocation check: no configured API key remains usable.
        _expect(api("POST", "/api/v1/integration/settings/keys/revoke", json={"kind": "read"}), 200, "revoke read key")
        revoked_read_status = api("GET", "/api/integration/v1/capabilities", headers=read_headers).status_code
        _expect(api("POST", "/api/v1/integration/settings/keys/revoke", json={"kind": "write"}), 200, "revoke write key")
        revoked_write_status = api("GET", "/api/integration/v1/capabilities", headers=write_headers).status_code
        record("R3-HTTP-KEY-REVOCATION", ["CAP-090"], revoked_read_status == 401 and revoked_write_status == 503,
               "revoking both managed credentials disables the HTTP integration API immediately",
               read_status=revoked_read_status, write_status=revoked_write_status)

        final_snapshot = _db_snapshot(database, task_id=task_id, row_id=row_id)
        EVIDENCE["snapshots"].append({"name": "final_after_restore", **final_snapshot})
        EVIDENCE["task_id"] = task_id
        EVIDENCE["table_id"] = table_id
        EVIDENCE["row_id"] = row_id
        EVIDENCE["source_sha256"] = _hash_bytes(source_path.read_bytes())
        EVIDENCE["summary"] = {"status": "passed", "passed": sum(c["status"] == "passed" for c in EVIDENCE["checks"]),
                                "failed": 0, "check_count": len(EVIDENCE["checks"])}
    finally:
        client.close()
        server.should_exit = True
        api_thread.join(timeout=10)
        _add_capability_evidence()
        EVIDENCE["summary"].setdefault("status", "failed")
        EVIDENCE["summary"].setdefault("check_count", len(EVIDENCE["checks"]))
        EVIDENCE["summary"].setdefault("passed", sum(c["status"] == "passed" for c in EVIDENCE["checks"]))
        EVIDENCE["summary"].setdefault("failed", 1 if EVIDENCE["summary"]["status"] != "passed" else 0)
        EVIDENCE["finished_at_local"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
        EVIDENCE_PATH.write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    try:
        asyncio.run(main_async())
        print(json.dumps(EVIDENCE["summary"], ensure_ascii=False))
        return 0
    except Exception as error:
        EVIDENCE["summary"] = {"status": "failed", "error_type": type(error).__name__, "error": str(error),
                                "check_count": len(EVIDENCE["checks"]),
                                "passed": sum(c["status"] == "passed" for c in EVIDENCE["checks"]), "failed": 1,
                                "traceback": traceback.format_exc()[-6000:]}
        if EVIDENCE_OWNED:
            EVIDENCE["finished_at_local"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
            EVIDENCE_PATH.write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(EVIDENCE["summary"], ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
