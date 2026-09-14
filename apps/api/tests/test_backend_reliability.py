"""v0.4 reliability boundaries exercised with isolated, synthetic data."""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from document_pipeline_api.models import AssistantMessage, AssistantToolCall, DataRowRecord, DataRowRevisionRecord
from document_pipeline_api.services.assistant_tools import Context
from document_pipeline_api.services.integration_config import (
    integration_config_path, read_integration_config, write_integration_config,
)
from test_assistant import client as ledger_fixture, ScriptedProvider, select_profile, send, finish

client = ledger_fixture


@pytest.mark.parametrize("mutation", ["changed_text", "broken_json", "wrong_shape", "orphan_tool"])
def test_native_history_falls_back_to_current_durable_message(client, mutation):
    client.app.state.conversation_factory = ScriptedProvider
    ScriptedProvider.plan = []
    profile = select_profile(client)
    detail = finish(client, send(client, profile))
    with client.app.state.session_factory() as session:
        message = session.get(AssistantMessage, detail["messages"][-1]["id"])
        assert message.native_context_json
        if mutation == "changed_text":
            message.parts_json = json.dumps([{"type": "text", "text": "修订后的当前结论"}])
        elif mutation == "broken_json":
            message.native_context_json = "{broken"
        elif mutation == "wrong_shape":
            message.native_context_json = "[]"
        else:
            native = json.loads(message.native_context_json)
            native["messages"] = [{"role": "tool", "tool_call_id": "missing", "content": "错误缓存"}]
            message.native_context_json = json.dumps(native)
        expected = json.loads(message.parts_json)[0]["text"]
        session.commit()
    ScriptedProvider.requests = []
    second = finish(client, send(client, profile, thread_id=detail["id"], text="继续"))
    assert second["runs"][0]["status"] == "completed"
    history = ScriptedProvider.requests[-1]
    assert any(expected in (item.get("content") or "") for item in history)
    assert "错误缓存" not in json.dumps(history, ensure_ascii=False)


@pytest.mark.parametrize("change", [{"mode": "read"}, {"capabilities": []}])
def test_capability_reduction_rebuilds_typed_history_from_durable_read_evidence(client, change):
    client.app.state.conversation_factory = ScriptedProvider
    ScriptedProvider.plan = [[{"type": "tool", "id": "count-1", "name": "analyze_data_table",
                              "arguments": {"table_id": "ledger", "metrics": [{"op": "count"}]}}]]
    profile = select_profile(client)
    detail = finish(client, send(client, profile, text="统计", context={"table_id": "ledger"}))
    ScriptedProvider.plan = []
    ScriptedProvider.requests = []
    finish(client, send(client, profile, text="解释", thread_id=detail["id"],
                        context={"table_id": "ledger", **change}))
    history = ScriptedProvider.requests[-1]
    tool = next(item for item in history if item["role"] == "tool")
    call = history[history.index(tool) - 1]["tool_calls"][0]
    assert call["id"] == tool["tool_call_id"] == detail["tools"][0]["id"]
    assert json.loads(tool["content"])["result"]["analysis_id"]
    assert all("analysis_id" not in item.get("content", "") for item in history if item["role"] == "assistant")


def test_tool_result_and_missing_state_invalidate_native_cache():
    from document_pipeline_api.services.assistant_memory import (
        message_state_fingerprint, native_context_segment, tool_state_fingerprint,
    )
    context = Context(table_id="ledger").model_dump()
    parts = json.dumps([{"type": "tool", "id": "t"}])
    context_json = json.dumps(context)
    call = SimpleNamespace(status="completed", result_json='{"total": 7}')
    cache = {"profile": ["p", 1], "messages": [{"role": "assistant", "content": "answer"}],
             "source_state": message_state_fingerprint(parts, context_json),
             "tool_states": {"t": tool_state_fingerprint(call)}}
    kwargs = dict(parts_json=parts, context_json=context_json, current_context=context)
    call.result_json = '{"total": 9}'
    assert native_context_segment(json.dumps(cache), "p", 1, {"t": call}, **kwargs) is None
    cache["tool_states"] = {}
    assert native_context_segment(json.dumps(cache), "p", 1, {"t": call}, **kwargs) is None


@pytest.mark.parametrize("change", ["corrected_result", "missing_record"])
def test_history_uses_durable_tool_evidence_instead_of_message_copy(client, change):
    client.app.state.conversation_factory = ScriptedProvider
    ScriptedProvider.plan = [[{"type": "tool", "id": "analysis", "name": "analyze_data_table",
                              "arguments": {"table_id": "ledger", "metrics": [{"op": "count"}]}}]]
    profile = select_profile(client)
    detail = finish(client, send(client, profile, text="分析", context={"table_id": "ledger"}))
    with client.app.state.session_factory() as session:
        call = session.scalar(select(AssistantToolCall))
        result = json.loads(call.result_json)
        snapshot_id = result["analysis_id"]
        if change == "corrected_result":
            result["source"]["table_name"] = "已核对合成账本"
            call.result_json = json.dumps(result)
        else:
            session.delete(call)
        session.commit()
    ScriptedProvider.plan = []
    ScriptedProvider.requests = []
    finish(client, send(client, profile, text="继续", thread_id=detail["id"], context={"table_id": "ledger"}))
    history = ScriptedProvider.requests[-1]
    text = json.dumps(history, ensure_ascii=False)
    if change == "corrected_result":
        tool = next(message for message in history if message["role"] == "tool")
        assert json.loads(tool["content"])["result"]["source"]["table_name"] == "已核对合成账本"
    else:
        assert not any(message["role"] == "tool" or message.get("tool_calls") for message in history)
        assert "缺少可核对依据" in text and snapshot_id not in text


def test_concurrent_config_save_rejects_conflict_then_merges_on_retry(tmp_path, monkeypatch):
    import document_pipeline_api.services.integration_config as module
    write_integration_config(tmp_path, {"read_token": "synthetic"})
    entered, release = threading.Event(), threading.Event()
    original = module.os.fsync
    failures = []

    def paused_fsync(fd):
        entered.set()
        assert release.wait(5)
        return original(fd)

    def writer():
        try:
            write_integration_config(tmp_path, {"mcp_data_read": True})
        except Exception as error:
            failures.append(error)

    monkeypatch.setattr(module.os, "fsync", paused_fsync)
    worker = threading.Thread(target=writer)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(HTTPException) as conflict:
            write_integration_config(tmp_path, {"write_token": "synthetic-write"})
        assert conflict.value.status_code == 409
        assert read_integration_config(tmp_path) == {"read_token": "synthetic"}
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not failures
    write_integration_config(tmp_path, {"write_token": "synthetic-write"})
    saved = read_integration_config(tmp_path)
    assert saved["mcp_data_read"] == "1"
    assert saved["read_token"] == "synthetic" and saved["write_token"] == "synthetic-write"


@pytest.mark.parametrize("fault", ["fsync", "replace"])
def test_config_disk_failure_keeps_policy_revision_and_contents(tmp_path, monkeypatch, fault):
    import document_pipeline_api.services.integration_config as module
    write_integration_config(tmp_path, {"mcp_data_read": True})
    original = integration_config_path(tmp_path).read_bytes()
    def fail(*args):
        raise OSError("synthetic disk fault")
    monkeypatch.setattr(module.os, fault, fail)
    with pytest.raises(OSError, match="synthetic disk fault"):
        write_integration_config(tmp_path, {"mcp_data_read": False})
    assert integration_config_path(tmp_path).read_bytes() == original
    assert not list((tmp_path / "config").glob(".integration-*.tmp"))


@pytest.mark.parametrize("broken", ["{broken", "[]"])
def test_corrupt_configuration_fails_closed_and_is_preserved(tmp_path, broken):
    path = integration_config_path(tmp_path)
    path.parent.mkdir()
    path.write_text(broken, encoding="utf-8")
    assert read_integration_config(tmp_path) == {}
    with pytest.raises(HTTPException, match="原文件已保留"):
        write_integration_config(tmp_path, {"mcp_data_read": True})
    assert path.read_text(encoding="utf-8") == broken


@pytest.mark.parametrize("scope", ["selcted", True, None, []])
def test_invalid_scope_never_expands_selected_table_access(tmp_path, scope):
    path = integration_config_path(tmp_path)
    path.parent.mkdir()
    path.write_text(json.dumps({"mcp_data_scope": scope, "mcp_data_read": True,
                                "mcp_table_ids": ["ledger"]}), encoding="utf-8")
    config = read_integration_config(tmp_path)
    assert config["mcp_data_scope"] == "selected" and config["mcp_table_ids"] == ""


def test_mcp_idle_connection_cannot_survive_revoke_restore(client):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from test_mcp_e2e import _mcp_params, _parse_result

    data_dir = client.app.state.settings.storage_dir.parent
    write_integration_config(data_dir, {"mcp_data_read": True, "mcp_data_scope": "selected",
                                        "mcp_table_ids": ["ledger"]})

    async def scenario():
        async with stdio_client(_mcp_params(data_dir)) as (read, write):
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()
                initial = _parse_result(await mcp.call_tool("list_data_tables", {}))
                assert "合成账本" in json.dumps(initial, ensure_ascii=False)
                write_integration_config(data_dir, {"mcp_data_read": False})
                write_integration_config(data_dir, {"mcp_data_read": True})
                denied = await mcp.call_tool("list_data_tables", {})
                assert denied.isError and "权限已变更" in str(denied)
                assert "合成账本" not in str(denied)
    asyncio.run(scenario())


def test_sse_reconnect_restores_same_timestamp_pagination_without_recent_window_loss(tmp_path):
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.requests import Request
    from document_pipeline_api.api.events import task_events, encode_task_cursor
    from document_pipeline_api.db import Base, build_engine
    from test_events import _task

    engine = build_engine(f"sqlite:///{tmp_path / 'events.db'}")
    Base.metadata.create_all(engine)
    stamp = datetime.now(timezone.utc) - timedelta(minutes=2)
    with Session(engine) as session:
        session.add_all([_task(f"task-{i:03}", now=stamp) for i in range(250)])
        session.commit()
    app = SimpleNamespace(state=SimpleNamespace(session_factory=sessionmaker(engine)))

    def response(cursor):
        request = Request({"type": "http", "method": "GET", "path": "/events", "app": app,
                           "headers": [(b"last-event-id", cursor.encode())]})
        async def connected():
            return False
        request.is_disconnected = connected
        return task_events(request)

    async def collect():
        first = response(encode_task_cursor((stamp - timedelta(seconds=1), "")))
        frame = await anext(first.body_iterator)
        assert "event: task" in frame
        cursor = next(line[4:] for line in frame.splitlines() if line.startswith("id: "))
        seen = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
        await first.body_iterator.aclose()
        resumed = response(cursor)
        async for frame in resumed.body_iterator:
            if "event: task" in frame:
                seen.extend(json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: "))))
                if len(seen) >= 250:
                    break
        await resumed.body_iterator.aclose()
        assert len(seen) == len({row["id"] for row in seen}) == 250
    asyncio.run(collect())
    engine.dispose()


def test_group_update_rolls_back_every_row_and_revision_when_sibling_conflicts(client, monkeypatch):
    from document_pipeline_api.schemas.data_tables import DataRowUpdate
    from document_pipeline_api.services.data_rows import update_data_row
    from sqlalchemy.sql.dml import Update

    with client.app.state.session_factory() as session:
        original = {row.id: (row.row_json, row.row_version)
                    for row in session.scalars(select(DataRowRecord))}
        execute = session.execute
        updates = 0
        def conflict_on_second(statement, *args, **kwargs):
            nonlocal updates
            if isinstance(statement, Update):
                updates += 1
                if updates == 2:
                    return SimpleNamespace(rowcount=0)
            return execute(statement, *args, **kwargs)
        monkeypatch.setattr(session, "execute", conflict_on_second)
        with pytest.raises(HTTPException) as conflict:
            update_data_row(session, "ledger", 1, DataRowUpdate(expected_version=1, changes={"vendor": "changed"}))
        assert conflict.value.status_code == 409 and updates == 2
    with client.app.state.session_factory() as session:
        assert {row.id: (row.row_json, row.row_version)
                for row in session.scalars(select(DataRowRecord))} == original
        assert not session.scalars(select(DataRowRevisionRecord)).all()


def test_group_update_changes_only_shared_headers_and_preserves_other_groups(client):
    from document_pipeline_api.schemas.data_tables import DataRowUpdate
    from document_pipeline_api.services.data_rows import update_data_row
    with client.app.state.session_factory() as session:
        update_data_row(session, "ledger", 1, DataRowUpdate(
            expected_version=1, changes={"vendor": "updated", "amount": 333},
        ))
        rows = {row.id: row for row in session.scalars(select(DataRowRecord))}
        assert json.loads(rows[1].row_json)["amount"] == 333
        assert json.loads(rows[2].row_json)["amount"] == 1000.5
        assert json.loads(rows[1].row_json)["vendor"] == json.loads(rows[2].row_json)["vendor"] == "updated"
        assert rows[1].row_version == rows[2].row_version == 2
        assert rows[3].row_version == rows[4].row_version == 1
        assert len(session.scalars(select(DataRowRevisionRecord)).all()) == 2
