"""Backend tool proof and current operation notices, without live inference.

Scripted providers verify orchestration only, not a real model's instruction following.
"""

import json

import pytest
from fastapi import HTTPException

from test_assistant import client as ledger_fixture, ScriptedProvider, select_profile, send, finish
from test_assistant_rework import decide, plan_run
from document_pipeline_api.models import AssistantToolCall, DataRowRecord, DataTableRecord
from document_pipeline_api.services.assistant_tools import Context, execute_tool


client = ledger_fixture


def seed_fractional_amounts(client):
    with client.app.state.session_factory() as session:
        for identifier, amount in [(1, 0.1), (2, 0.2)]:
            row = session.get(DataRowRecord, identifier)
            row.row_json = json.dumps({**json.loads(row.row_json), "amount": amount})
        session.add(DataTableRecord(id="control", name="未选择的合成表", template_key="manual:control",
            template_version="1", document_kind="custom", columns_json=json.dumps([
                {"key": "amount", "label": "金额", "section": "item", "value_type": "number"},
            ])))
        session.flush()
        session.add(DataRowRecord(table_id="control", item_index=1, row_json='{"amount": 999999}'))
        session.commit()


@pytest.mark.parametrize("selected_rows,expected", [(None, 90.3), ([1, 2], 0.3)])
def test_record_snapshot_cannot_substitute_for_actual_scoped_sum(client, selected_rows, expected):
    seed_fractional_amounts(client)
    context = Context(table_id="ledger", row_ids=selected_rows, mode="read")
    with client.app.state.session_factory() as session:
        artifacts = {}
        records = execute_tool(session, context, "read_data_rows",
                               {"table_id": "ledger", "fields": ["amount"], "limit": 2}, artifacts)
        assert [row["values"]["amount"] for row in records["rows"]] == [0.1, 0.2]
        assert records["source"]["request"]["records"] is True
        assert records["source"]["request"]["metrics"] == [{"op": "count", "field": None}]
        assert records["truncated"] is (selected_rows is None)
        assert not {"totals", "metric_keys", "data"}.intersection(records)

        aggregate = execute_tool(session, context, "analyze_data_table", {
            "table_id": "ledger", "metrics": [{"op": "sum", "field": "amount"}],
        }, artifacts)
        assert aggregate["source"]["request"]["records"] is False
        assert aggregate["source"]["request"]["metrics"] == [{"op": "sum", "field": "amount"}]
        assert aggregate["totals"] == {"sum:amount": expected}
        assert aggregate["data"][0]["sum:amount"] == expected
        assert records["analysis_id"] != aggregate["analysis_id"]
        assert artifacts[records["analysis_id"]] is records
        assert artifacts[aggregate["analysis_id"]] is aggregate
        with pytest.raises(HTTPException) as forbidden:
            execute_tool(session, context, "analyze_data_table", {
                "table_id": "control", "metrics": [{"op": "sum", "field": "amount"}],
            }, artifacts)
        assert forbidden.value.status_code == 403


def test_scripted_conversation_persists_actual_sum_tool_result_in_read_mode(client):
    seed_fractional_amounts(client)
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [
        [{"type": "tool", "id": "rows", "name": "read_data_rows",
          "arguments": {"table_id": "ledger", "limit": 2}}],
        [{"type": "tool", "id": "sum", "name": "analyze_data_table",
          "arguments": {"table_id": "ledger", "metrics": [{"op": "sum", "field": "amount"}]}}],
        [{"type": "text", "text": "按当前授权范围的后端汇总结果回答。"}],
    ]
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(client, send(client, select_profile(client), text="读取前两条明细，并汇总整张所选表的明细额。",
                                 context={"table_ids": ["ledger"], "mode": "read"}))
    assert detail["runs"][0]["status"] == "completed"
    rows = next(tool for tool in detail["tools"] if tool["name"] == "read_data_rows")
    summed = next(tool for tool in detail["tools"] if tool["name"] == "analyze_data_table")
    assert rows["status"] == summed["status"] == "completed"
    assert "totals" not in rows["result"]
    assert summed["result"]["totals"] == {"sum:amount": 90.3}
    assert summed["result"]["source"]["table_id"] == "ledger"
    assert summed["result"]["source"]["request"]["metrics"] == [{"op": "sum", "field": "amount"}]
    with client.app.state.session_factory() as session:
        saved = json.loads(session.get(AssistantToolCall, summed["id"]).result_json)
        assert saved["totals"] == {"sum:amount": 90.3}
        assert json.loads(session.get(DataRowRecord, 1).row_json)["amount"] == 0.1
        assert json.loads(session.get(DataRowRecord, 2).row_json)["amount"] == 0.2


def test_operation_card_notices_follow_undo_without_rewriting_saved_evidence(client):
    detail = plan_run(client, [{"kind": "increment_rows", "table_id": "ledger", "row_ids": [1, 2],
                               "changes": {"amount": 0.1}}])
    identifier = detail["tools"][0]["id"]
    approved = decide(client, identifier)
    assert approved.status_code == 200
    with client.app.state.session_factory() as session:
        saved_approved = session.get(AssistantToolCall, identifier).result_json
    undo = client.post(f"/api/v1/assistant/tools/{identifier}/undo")
    assert undo.status_code == 200
    assert decide(client, undo.json()["id"]).status_code == 200
    refreshed = client.get(f"/api/v1/assistant/threads/{detail['id']}").json()
    original = next(tool for tool in refreshed["tools"] if tool["id"] == identifier)
    reverse = next(tool for tool in refreshed["tools"] if tool["id"] == undo.json()["id"])
    assert original["status"] == "undone"
    assert original["result"]["note"] == "操作已撤销，原先的变更已还原。"
    assert reverse["status"] == "approved"
    assert reverse["result"]["note"] == "撤销已执行，原先的变更已还原。"
    with client.app.state.session_factory() as session:
        assert session.get(AssistantToolCall, identifier).result_json == saved_approved
        assert json.loads(session.get(DataRowRecord, 1).row_json)["amount"] == 200
        assert json.loads(session.get(DataRowRecord, 2).row_json)["amount"] == 1000.5
