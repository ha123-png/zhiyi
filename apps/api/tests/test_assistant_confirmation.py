"""The review journey: arithmetic, one proposal, and durable decisions."""

import json
import threading

import pytest

from test_assistant import client as ledger_fixture, ScriptedProvider, select_profile, send, finish
from test_assistant_rework import plan_run, decide
from document_pipeline_api.models import DataRowRecord, DataTableRecord, AssistantToolCall
from document_pipeline_api.services.assistant_memory import summarize_result
from document_pipeline_api.services.assistant_tools import execute_tool, Context

client = ledger_fixture


def increment(ids=(1, 2), delta=1, field="amount"):
    return {"kind": "increment_rows", "table_id": "ledger", "row_ids": list(ids), "changes": {field: delta}}


def event(operation, identifier="plan"):
    return {"type": "tool", "id": identifier, "name": "propose_operations",
            "arguments": {"title": "模型声称的标题不能替代真实变更", "operations": [operation]}}


def values(client, *ids):
    with client.app.state.session_factory() as session:
        return [(json.loads(session.get(DataRowRecord, i).row_json)["amount"],
                 session.get(DataRowRecord, i).row_version) for i in ids]


def test_increment_is_exact_and_confirm_is_idempotent_and_undo_restores(client):
    detail = plan_run(client, [increment(delta=0.1)])
    tool = detail["tools"][0]
    preview = tool["result"]
    assert "调整数值" in preview["title"]
    assert [r["after"]["amount"] for r in preview["items"][0]["affected"]] == [200.1, 1000.6]
    summary = summarize_result(preview)
    assert summary["items"][0]["changes"][0]["before"] == {"amount": 200}
    assert values(client, 1, 2) == [(200, 1), (1000.5, 1)]
    first = decide(client, tool["id"])
    assert first.status_code == 200, first.text
    assert decide(client, tool["id"]).json() == first.json()
    assert values(client, 1, 2) == [(200.1, 2), (1000.6, 2)]
    undo = client.post(f"/api/v1/assistant/tools/{tool['id']}/undo").json()
    assert decide(client, undo["id"]).status_code == 200
    assert values(client, 1, 2) == [(200, 3), (1000.5, 3)]


@pytest.mark.parametrize("operation", [increment((1, 4)), increment(field="vendor"),
    increment(delta=True), increment(delta="1")])
def test_increment_invalid_values_fail_whole_preview(client, operation):
    before = values(client, 1, 2, 4)
    detail = plan_run(client, [operation])
    assert detail["tools"][0]["status"] == "failed"
    assert values(client, 1, 2, 4) == before


def test_shared_numeric_field_increments_once_per_document(client):
    with client.app.state.session_factory() as session:
        table = session.get(DataTableRecord, "ledger")
        columns = json.loads(table.columns_json)
        columns[1]["value_type"] = "number"
        table.columns_json = json.dumps(columns)
        session.commit()
    detail = plan_run(client, [increment((1, 2), field="total")])
    assert decide(client, detail["tools"][0]["id"]).status_code == 200
    with client.app.state.session_factory() as session:
        for identifier in (1, 2):
            row = session.get(DataRowRecord, identifier)
            assert json.loads(row.row_json)["total"] == 1201.5
            assert row.row_version == 2


def test_first_preview_stops_same_response_duplicates_and_subsequent_model_calls(client):
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [[event(increment(), "first"), event(increment(), "duplicate")],
                             [event(increment(), "next_round")]]
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(client, send(client, select_profile(client), text="金额增加1", context={"table_id": "ledger"}))
    assert len(detail["tools"]) == 1
    assert len(ScriptedProvider.requests) == 1
    assert values(client, 1, 2) == [(200, 1), (1000.5, 1)]
    assert decide(client, detail["tools"][0]["id"]).status_code == 200
    assert values(client, 1, 2) == [(201, 2), (1001.5, 2)]


def test_new_proposal_supersedes_old_but_cannot_execute_old_card(client):
    detail = plan_run(client, [increment()])
    old_id = detail["tools"][0]["id"]
    ScriptedProvider.plan = [[event(increment(delta=2))]]
    next_detail = finish(client, send(client, select_profile(client), thread_id=detail["id"],
                                     text="改为增加2", context={"table_ids": ["ledger"]}))
    old = next(t for t in next_detail["tools"] if t["id"] == old_id)
    assert old["status"] == "superseded"
    assert decide(client, old_id).json()["status"] == "superseded"
    assert values(client, 1, 2) == [(200, 1), (1000.5, 1)]
    fresh = next(t for t in next_detail["tools"] if t["status"] == "pending")
    assert decide(client, fresh["id"]).status_code == 200
    assert values(client, 1, 2) == [(202, 2), (1002.5, 2)]


def test_changed_preview_is_visibly_stale_and_never_overwrites_user_edit(client):
    detail = plan_run(client, [increment()])
    identifier = detail["tools"][0]["id"]
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 1)
        row.row_json = json.dumps({**json.loads(row.row_json), "amount": 777})
        row.row_version += 1
        session.commit()
    shown = client.get(f"/api/v1/assistant/threads/{detail['id']}").json()
    assert shown["tools"][0]["status"] == "stale"
    assert decide(client, identifier).status_code == 409
    assert decide(client, identifier).json()["status"] == "stale"
    assert values(client, 1, 2) == [(777, 2), (1000.5, 1)]


@pytest.mark.parametrize("approve", [True, False])
def test_early_decision_is_visible_to_next_turn_even_before_generation_finishes(client, approve):
    waiting = threading.Event()
    release = threading.Event()

    class PausedFinish(ScriptedProvider):
        def close(self):
            waiting.set()
            release.wait(5)

    ScriptedProvider.requests = []
    ScriptedProvider.plan = [[event(increment())]]
    client.app.state.conversation_factory = PausedFinish
    profile = select_profile(client)
    response = send(client, profile, text="金额增加1", context={"table_ids": ["ledger"]})
    try:
        assert waiting.wait(5)
        thread_id = response.json()["thread_id"]
        detail = client.get(f"/api/v1/assistant/threads/{thread_id}").json()
        identifier = detail["tools"][0]["id"]
        assert client.post(f"/api/v1/assistant/tools/{identifier}/decision", json={"approve": approve}).status_code == 200
    finally:
        release.set()
    finish(client, response)
    ScriptedProvider.plan = []
    ScriptedProvider.requests = []
    client.app.state.conversation_factory = ScriptedProvider
    finish(client, send(client, profile, thread_id=thread_id, text="现在操作是什么状态", context={"table_ids": ["ledger"]}))
    history = "\n".join(m.get("content", "") for m in ScriptedProvider.requests[0])
    expected_status = "executed" if approve else "rejected"
    assert f'"status": "{expected_status}"' in history
    assert values(client, 1, 2) == ([(201, 2), (1001.5, 2)] if approve else [(200, 1), (1000.5, 1)])


def test_readonly_scope_blocks_increment_even_if_model_ignores_tools(client):
    detail = plan_run(client, [increment()], mode="read")
    assert detail["tools"][0]["status"] == "failed"
    assert values(client, 1, 2) == [(200, 1), (1000.5, 1)]


def test_reading_one_field_does_not_redirect_an_independent_numeric_operation(client):
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [[{
        "type": "tool", "id": "table", "name": "read_resource",
        "arguments": {"kind": "table", "id": "ledger"},
    }, event(increment())]]
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(client, send(client, select_profile(client),
                                 text="先看看第一个字段，再把金额增加1", context={"table_id": "ledger"}))
    assert [t["name"] for t in detail["tools"]] == ["read_resource", "propose_operations"]
    assert detail["tools"][-1]["status"] == "pending"
    assert values(client, 1, 2) == [(200, 1), (1000.5, 1)]
    assert len(ScriptedProvider.requests) == 1


def test_all_in_scope_is_explicit_and_respects_selected_rows(client):
    op = {**increment(), "row_ids": [], "all_in_scope": True}
    detail = plan_run(client, [op], table_id="ledger", row_ids=[1, 2])
    tool = detail["tools"][0]
    assert {r["row_id"] for r in tool["result"]["items"][0]["affected"]} == {1, 2}
    assert decide(client, tool["id"]).status_code == 200
    assert values(client, 1, 2, 3) == [(201, 2), (1001.5, 2), (90, 1)]


def test_all_in_scope_membership_change_invalidates_the_whole_plan(client):
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 4)
        row.row_json = json.dumps({**json.loads(row.row_json), "amount": 0})
        session.commit()
    op = {**increment(), "row_ids": [], "all_in_scope": True}
    detail = plan_run(client, [op], table_id="ledger")
    assert detail["tools"][0]["status"] == "pending", detail
    with client.app.state.session_factory() as session:
        session.add(DataRowRecord(table_id="ledger", item_index=20,
                                  row_json=json.dumps({"vendor": "A", "amount": 3})))
        session.commit()
    assert decide(client, detail["tools"][0]["id"]).status_code == 409
    assert values(client, 1, 2) == [(200, 1), (1000.5, 1)]


def test_shared_and_item_increments_merge_all_affected_fields(client):
    with client.app.state.session_factory() as session:
        table = session.get(DataTableRecord, "ledger")
        columns = json.loads(table.columns_json)
        columns[1]["value_type"] = "number"
        table.columns_json = json.dumps(columns)
        session.commit()
    op = {**increment(), "changes": {"total": 1, "amount": 2}}
    detail = plan_run(client, [op])
    tool = detail["tools"][0]
    affected = {r["row_id"]: r for r in tool["result"]["items"][0]["affected"]}
    assert affected[1]["after"] == {"total": 1201.5, "amount": 202}
    assert affected[2]["after"] == {"total": 1201.5, "amount": 1002.5}
    assert decide(client, tool["id"]).status_code == 200
    assert [v for v, _ in values(client, 1, 2)] == [202, 1002.5]
    undo = client.post(f"/api/v1/assistant/tools/{tool['id']}/undo").json()
    assert decide(client, undo["id"]).status_code == 200
    assert [v for v, _ in values(client, 1, 2)] == [200, 1000.5]


def test_bulk_undo_handles_more_than_twelve_distinct_original_values(client):
    with client.app.state.session_factory() as session:
        identifiers = []
        for index in range(13):
            row = DataRowRecord(table_id="ledger", item_index=20 + index,
                                row_json=json.dumps({"amount": index}))
            session.add(row)
            session.flush()
            identifiers.append(row.id)
        session.commit()
    detail = plan_run(client, [increment(identifiers)])
    identifier = detail["tools"][0]["id"]
    result = decide(client, identifier)
    assert result.status_code == 200
    assert result.json()["result"]["execution"]["can_undo"]
    undo = client.post(f"/api/v1/assistant/tools/{identifier}/undo")
    assert undo.status_code == 200, undo.text
    assert decide(client, undo.json()["id"]).status_code == 200
    assert [value for value, _ in values(client, *identifiers)] == list(range(13))


def test_legacy_template_undo_returns_template_page_guidance(client):
    detail = plan_run(client, [increment()])
    identifier = detail["tools"][0]["id"]
    with client.app.state.session_factory() as session:
        call = session.get(AssistantToolCall, identifier)
        call.status = "approved"
        call.result_json = json.dumps({
            "kind": "operation_plan", "title": "历史模板修改",
            "items": [{"operation": {"kind": "update_template"}}],
            "execution": {"can_undo": True, "undo_guards": {},
                          "undo": [{"kind": "restore_template", "template_id": "old", "version": 1}]},
        })
        session.commit()
    result = client.post(f"/api/v1/assistant/tools/{identifier}/undo")
    assert result.status_code == 409, result.text
    assert "模板页面" in result.json()["detail"]


def test_prose_never_creates_a_confirmable_or_executed_plan(client):
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [[{"type": "text", "text": "已执行修改，请确认。"}]]
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(client, send(client, select_profile(client), text="金额增加1", context={"table_id": "ledger"}))
    assert not detail["tools"]
    assert len(ScriptedProvider.requests) == 1
    assert values(client, 1, 2) == [(200, 1), (1000.5, 1)]


def test_record_id_sort_is_numeric_in_both_directions(client):
    with client.app.state.session_factory() as session:
        for descending, expected in [(False, [1, 2]), (True, [4, 3])]:
            result = execute_tool(session, Context(table_id="ledger"), "read_data_rows",
                                  {"table_id": "ledger", "sort": "row_id", "descending": descending, "limit": 2}, {})
            assert [row["row_id"] for row in result["rows"]] == expected


@pytest.mark.parametrize("approved, expected", [(True, "已执行"), (False, "已取消，没有执行")])
def test_latest_operation_state_is_available_without_replacing_the_model_reply(client, approved, expected):
    detail = plan_run(client, [increment()])
    client.post(f"/api/v1/assistant/tools/{detail['tools'][0]['id']}/decision", json={"approve": approved})
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [[{"type": "text", "text": "请看对应操作卡的当前状态。"}]]
    result = finish(client, send(client, select_profile(client), thread_id=detail['id'],
                                text="我刚才的修改执行了吗？只说明状态。", context={"table_ids": ["ledger"]}))
    assert len(ScriptedProvider.requests) == 1
    assert expected in ScriptedProvider.requests[0][-1]["content"]
    assert "请看对应操作卡的当前状态。" in "".join(p.get("text", "") for p in result["messages"][-1]["parts"])


def test_status_only_does_not_reuse_an_operation_from_another_scope(client):
    detail = plan_run(client, [increment()])
    decide(client, detail['tools'][0]['id'])
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [[{"type": "text", "text": "当前范围没有可核对的操作记录。"}]]
    result = finish(client, send(client, select_profile(client), thread_id=detail['id'],
                                text="刚才执行了吗？", context={"table_id": "ledger", "row_ids": [4]}))
    assert len(ScriptedProvider.requests) == 1
    assert "最近操作的当前状态" not in ScriptedProvider.requests[0][-1]["content"]
    assert "已执行" not in "".join(p.get("text", "") for p in result["messages"][-1]["parts"])


def test_undo_preview_replaces_other_pending_plan_in_same_thread(client):
    first = plan_run(client, [increment((1,))])
    original = first['tools'][0]['id']
    assert decide(client, original).status_code == 200
    ScriptedProvider.plan = [[event(increment((3,)), 'new-plan')]]
    next_detail = finish(client, send(client, select_profile(client), thread_id=first['id'],
                                    text='第三条金额增加1', context={'table_ids': ['ledger']}))
    next_id = next(t['id'] for t in next_detail['tools'] if t['status'] == 'pending')
    undone = client.post(f'/api/v1/assistant/tools/{original}/undo')
    assert undone.status_code == 200, undone.text
    current = client.get(f"/api/v1/assistant/threads/{first['id']}").json()
    assert [t['id'] for t in current['tools'] if t['status'] == 'pending'] == [undone.json()['id']]
    assert decide(client, next_id).json()['status'] == 'superseded'


def test_normal_help_is_not_deleted_or_retried_because_it_mentions_confirmation(client):
    answer = "请在模板页面修改并确认保存；保存成功后状态显示已更新。"
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [[{"type": "text", "text": answer}]]
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(client, send(client, select_profile(client), text="如何修改模板？", context={"table_id": "ledger"}))
    assert not detail["tools"]
    assert len(ScriptedProvider.requests) == 1
    assert answer == ''.join(p.get('text', '') for p in detail['messages'][-1]['parts'])
    assert values(client, 1, 2) == [(200, 1), (1000.5, 1)]


def test_data_operation_contract_does_not_change_with_user_wording(client):
    from document_pipeline_api.services.assistant_tools import scoped_tool_specs

    with client.app.state.session_factory() as session:
        contracts = []
        for question in ("增加一条记录", "新增记录", "金额增加1", "append a record", "调整数值后添加新记录"):
            specs = scoped_tool_specs(session, Context(table_id="ledger"),
                                      {"_operations": {"categories": ["data"]}}, False, question)
            operation = next(s for s in specs if s["function"]["name"] == "propose_operations")["function"]["parameters"]["$defs"]["Operation"]
            contracts.append(operation)
        assert all(contract == contracts[0] for contract in contracts)
        assert set(contracts[0]["properties"]["kind"]["enum"]) == {"add_rows", "update_rows", "increment_rows", "delete_rows"}
        assert "rows" in contracts[0]["properties"]
        # An addition has no existing record selector; do not require fabricated IDs.
        assert set(contracts[0]["required"]) == {"kind", "table_id"}


def test_mixed_request_does_not_hide_a_permitted_operation(client):
    from document_pipeline_api.services.assistant_tools import scoped_tool_specs

    with client.app.state.session_factory() as session:
        specs = scoped_tool_specs(session, Context(table_id="ledger"), {}, False,
                                  "金额增加1并重命名表")
        operation = next(s for s in specs if s["function"]["name"] == "propose_operations")["function"]["parameters"]["$defs"]["Operation"]
        assert {"increment_rows", "rename_table"} <= set(operation["properties"]["kind"]["enum"])
