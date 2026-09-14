import json

import pytest
from fastapi import HTTPException

from test_assistant import client as ledger_fixture, ScriptedProvider, select_profile, send, finish
from document_pipeline_api.models import DataRowRecord
from document_pipeline_api.services.assistant_tools import Context, execute_tool, scoped_tool_specs

client = ledger_fixture


def test_native_tool_history_reuses_exact_prefix_and_scope_reduction_removes_it(client):
    client.app.state.conversation_factory = ScriptedProvider
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [[{"type": "tool", "id": "native-1", "name": "analyze_data_table",
        "arguments": {"table_id": "ledger", "metrics": [{"op": "count"}]}}]]
    profile = select_profile(client)
    detail = finish(client, send(client, profile, text="分析记录数", context={"table_id": "ledger"}))
    previous_request = ScriptedProvider.requests[-1]
    assert any(m["role"] == "tool" for m in previous_request)
    ScriptedProvider.plan = []
    finish(client, send(client, profile, text="继续说明", context={"table_id": "ledger"}, thread_id=detail["id"]))
    followup = ScriptedProvider.requests[-1]
    assert followup[:len(previous_request)] == previous_request
    finish(client, send(client, profile, text="不再使用资料", context={}, thread_id=detail["id"]))
    assert not any(m["role"] == "tool" or m.get("tool_calls") for m in ScriptedProvider.requests[-1])


def test_native_history_invalidates_changed_operations_and_model_versions():
    from types import SimpleNamespace
    from document_pipeline_api.services.assistant_memory import native_context_segment, tool_state_fingerprint, compact_history, message_state_fingerprint
    call = SimpleNamespace(status="pending", result_json='{"amount": 7}')
    parts_json = json.dumps([{"type": "tool", "id": "t"}])
    context = Context(table_id="ledger").model_dump()
    context_json = json.dumps(context)
    kwargs = {"parts_json": parts_json, "context_json": context_json, "current_context": context}
    raw = json.dumps({"profile": ["p", 1], "messages": [{"role": "assistant", "content": "preview"}],
        "source_state": message_state_fingerprint(parts_json, context_json),
        "tool_states": {"t": tool_state_fingerprint(call)}})
    assert native_context_segment(raw, "p", 1, {"t": call}, **kwargs)
    assert native_context_segment(raw, "p", 2, {"t": call}, **kwargs) is None
    call.status = "completed"
    assert native_context_segment(raw, "p", 1, {"t": call}, **kwargs) is None
    compacted, changed = compact_history([
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "native", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "native", "content": json.dumps({"tool_id": "saved", "rows": ["large result"] * 1000})},
        {"role": "assistant", "content": "answer"},
    ], 1500)
    assert changed
    result = next(m for m in compacted if m["role"] == "tool")
    assert json.loads(result["content"])["tool_id"] == "saved"
    assert compacted[compacted.index(result) - 1]["tool_calls"][0]["id"] == result["tool_call_id"]
    assert all("large result" not in m["content"] for m in compacted if m["role"] == "assistant")


@pytest.mark.parametrize("limit", [500, 1500, 6500])
def test_compaction_keeps_parallel_calls_atomic_and_arguments_intact(limit):
    from document_pipeline_api.services.assistant_memory import compact_history, encode

    arguments = json.dumps({"search": "valid code {} 历史工具调用 " * 50})
    calls = [{"id": str(i), "type": "function", "function": {"name": "read", "arguments": arguments}} for i in range(2)]
    history = [{"role": "user", "content": "Earlier request " * 2000},
               {"role": "assistant", "content": "", "tool_calls": calls},
               *[{"role": "tool", "tool_call_id": str(i), "content": json.dumps({"tool_id": str(i), "status": "approved", "rows": ["evidence"] * 2000})} for i in range(2)],
               {"role": "assistant", "content": "latest answer"}]
    messages, changed = compact_history(history, limit)
    assert changed and len(encode(messages)) <= limit
    pending = set()
    for message in messages:
        if message.get("tool_calls"):
            assert not pending
            for call in message["tool_calls"]:
                assert call["function"]["arguments"] == arguments
                pending.add(call["id"])
        elif message["role"] == "tool":
            pending.remove(message["tool_call_id"])
            assert json.loads(message["content"])["status"] == "approved"
        else:
            assert not pending
    assert not pending and messages[-1]["content"] == "latest answer"
    assert history[2]["content"].count("evidence") == 2000


def test_compacted_history_does_not_insert_mid_conversation_system_messages():
    from document_pipeline_api.services.assistant_memory import compact_history

    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "较早的合成对话" * 100}
        for i in range(12)
    ]
    messages, shortened = compact_history(history, 1500)
    assert shortened and messages[0]["role"] == "assistant"
    assert all(m["role"] != "system" for m in messages)
    assert "不是当前待执行命令" in messages[0]["content"]


def test_basic_analysis_does_not_invite_unrequested_filters_but_can_enable_them(client):
    with client.app.state.session_factory() as session:
        context = Context(table_id="ledger", row_ids=[1, 2])
        artifacts = {}

        def parameters():
            return next(
                s["function"]["parameters"]
                for s in scoped_tool_specs(
                    session, context, artifacts, False, "按供应方汇总选中记录"
                )
                if s["function"]["name"] == "analyze_data_table"
            )

        assert "filters" not in parameters()["properties"]
        assert "time_bucket" not in parameters()["properties"]
        execute_tool(session, context, "enable_analysis_options", {}, artifacts)
        assert "filters" in parameters()["properties"]
        assert "row_ids" not in parameters()["properties"]
        data = execute_tool(
            session,
            context,
            "analyze_data_table",
            {"table_id": "ledger", "filters": [{"field": "vendor", "op": "eq", "value": "B"}]},
            artifacts,
        )
        assert data["source"]["row_count"] == 0


def test_resource_picker_loads_current_template_names_and_all_categories(client):
    tables = client.get("/api/v1/assistant/resources?kind=table")
    assert tables.status_code == 200 and tables.json()[0]["name"] == "合成账本"
    templates = client.get("/api/v1/assistant/resources?kind=template")
    assert templates.status_code == 200 and templates.json()
    name = templates.json()[0]["name"]
    filtered = client.get(
        "/api/v1/assistant/resources", params={"kind": "template", "search": name}
    )
    assert all(name in v["name"] for v in filtered.json())
    assert client.get("/api/v1/assistant/resources?kind=task").status_code == 200
    for kind, resource in [("table", tables.json()[0]), ("template", templates.json()[0])]:
        selected = client.get(
            "/api/v1/assistant/resources",
            params={"kind": kind, "ids": resource["id"], "search": "不匹配的名称"},
        )
        assert selected.json() == [resource]
    assert client.get("/api/v1/assistant/resources?kind=table&ids=missing").json() == []


def test_supplier_is_not_a_date_and_chart_title_comes_from_actual_grouping(client):
    with client.app.state.session_factory() as session:
        context = Context(table_id="ledger")
        with pytest.raises(HTTPException, match="供应方.*没有可识别日期"):
            execute_tool(
                session,
                context,
                "analyze_data_table",
                {"table_id": "ledger", "dimensions": ["vendor"], "time_bucket": "month"},
                {},
            )
        artifacts = {}
        result = execute_tool(
            session,
            context,
            "analyze_data_table",
            {
                "table_id": "ledger",
                "dimensions": ["vendor"],
                "metrics": [{"op": "sum", "field": "amount"}],
            },
            artifacts,
        )
        assert [r["vendor"] for r in result["data"]] == ["A", "B", "C"]
        chart = execute_tool(
            session,
            context,
            "render_chart",
            {
                "analysis_id": result["analysis_id"],
                "type": "bar",
                "series": ["sum:amount"],
                "title": "按月份统计完全不同的内容",
            },
            artifacts,
        )
        assert chart["chart"]["title"] == "按供应方汇总 · 明细额 · 合计"


def test_missing_dates_are_explicit_and_cannot_be_a_trend(client):
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 4)
        row.row_json = json.dumps({"vendor": "C", "month": "not a date", "amount": 3})
        session.flush()
        context = Context(table_id="ledger")
        result = execute_tool(
            session,
            context,
            "analyze_data_table",
            {"table_id": "ledger", "dimensions": ["month"], "time_bucket": "month"},
            artifacts := {},
        )
        assert any("1 条记录缺少有效日期" in w for w in result["warnings"])
        with pytest.raises(HTTPException, match="趋势图"):
            execute_tool(
                session,
                context,
                "render_chart",
                {
                    "analysis_id": result["analysis_id"],
                    "type": "area",
                    "series": ["count:*"],
                    "title": "月趋势",
                },
                artifacts,
            )


def test_single_category_is_a_metric_but_partial_pie_is_still_rejected(client):
    with client.app.state.session_factory() as session:
        context = Context(table_id="ledger", row_ids=[1, 2])
        result = execute_tool(
            session,
            context,
            "analyze_data_table",
            {"table_id": "ledger", "dimensions": ["vendor"]},
            artifacts := {},
        )
        chart = execute_tool(
            session,
            context,
            "render_chart",
            {
                "analysis_id": result["analysis_id"],
                "type": "donut",
                "series": ["count:*"],
                "title": "占比",
            },
            artifacts,
        )
        assert chart["chart"]["type"] == "metric"


@pytest.mark.parametrize(
    "context,expected", [(Context(), []), (Context(table_id="ledger", row_ids=[1]), ["ledger"])]
)
def test_catalog_is_direct_and_does_not_expand_permission(client, context, expected):
    with client.app.state.session_factory() as session:
        specs = scoped_tool_specs(
            session, context, {"_history": {"items": [{}]}}, False, "你可以看到什么表"
        )
        assert [s["function"]["name"] for s in specs] == ["catalog"]
        result = execute_tool(session, context, "catalog", {"kind": "tables"}, {})
        assert [item["id"] for item in result["items"]] == expected


def test_model_state_is_profile_specific_and_marks_mock_service(client):
    profile = select_profile(client)
    endpoint = f"/api/v1/assistant/model-state?profile_id={profile['id']}"
    assert client.get(endpoint).json()["state"] == "unverified"
    client.app.state.conversation_factory = ScriptedProvider
    ScriptedProvider.plan = []
    finish(client, send(client, profile))
    state = client.get(endpoint).json()
    assert state["state"] == "success" and state["responded"] and state["at"]
    client.app.state.assistant_simulated = True
    assert client.get(endpoint).json()["simulated"]


def test_visibility_word_does_not_block_requested_original_page_read(client):
    with client.app.state.session_factory() as session:
        specs = scoped_tool_specs(
            session,
            Context(table_id="ledger"),
            {},
            False,
            "你能看到这份文件原件吗？读取第一页的内容。",
        )
        assert "read_original_page" in [s["function"]["name"] for s in specs]


def test_repeated_history_loop_stops_before_eight_model_requests(client):
    client.app.state.conversation_factory = ScriptedProvider
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [
        [{"type": "tool", "id": f"read-{i}", "name": "read_conversation_history", "arguments": {}}]
        for i in range(8)
    ]
    detail = finish(client, send(client, select_profile(client)))
    assert "重复读取" in detail["runs"][0]["error"]
    assert len(ScriptedProvider.requests) == 3
