"""Terminal answer protocol: real aggregates are proof; scripted prose is not."""

import json
import threading

import pytest
from document_pipeline_api.models import AssistantMessage, AssistantRun, AssistantToolCall
from document_pipeline_api.services.assistant_tools import Context, scoped_tool_specs
from test_assistant import client as ledger_fixture, finish, select_profile, send
from test_assistant_scope_claims import seed_fractional_amounts

client = ledger_fixture


def terminal(kind="general", **arguments):
    return {"type": "tool", "id": "finish", "name": "finish_answer",
            "arguments": {"kind": kind, **arguments}}


class Provider:
    def __init__(self, settings, cancel):
        self.cancel = cancel
        self.step = 0

    def close(self):
        pass


def test_unverified_pending_reply_never_reaches_checkpoint_sse_or_history(client):
    paused, release = threading.Event(), threading.Event()
    raw = "本轮已计算金额合计为 777.77。"
    requests = []

    class HeldProvider(Provider):
        def close(self):
            release.set()

        def stream(self, messages, tools, **options):
            requests.append((json.loads(json.dumps(messages)), options))
            if self.step == 0:
                self.step += 1
                yield {"type": "text", "text": raw}
                # This invokes EventWriter.checkpoint before the generator resumes.
                yield {"type": "usage", "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
                paused.set()
                assert release.wait(5)
            else:
                yield terminal("limitation", text="没有取得可核对的统计结果。")

    client.app.state.conversation_factory = HeldProvider
    started = send(client, select_profile(client), text="计算所选范围合计", context={"table_ids": ["ledger"], "mode": "read"})
    assert started.status_code == 200
    try:
        assert paused.wait(5)
        partial = client.get(f"/api/v1/assistant/threads/{started.json()['thread_id']}").json()
        assert raw not in json.dumps(partial, ensure_ascii=False)
        with client.app.state.session_factory() as session:
            run = session.get(AssistantRun, started.json()["run_id"])
            assert raw not in session.get(AssistantMessage, run.message_id).parts_json
    finally:
        release.set()
    detail = finish(client, started)
    stream = client.get(f"/api/v1/assistant/runs/{started.json()['run_id']}/events").text
    assert raw not in stream and raw not in json.dumps(detail, ensure_ascii=False)
    assert len(requests) == 2
    assert requests[1][1] == {"tool_choice": "required"}
    assert raw not in json.dumps(requests[1][0], ensure_ascii=False)
    assert detail["runs"][0]["usage"]["answer_kind"] == "limitation"


@pytest.mark.parametrize("context,expected,rows", [
    ({"table_id": "ledger", "row_ids": [1, 2], "mode": "read"}, 0.3, 2),
    ({"table_ids": ["ledger"], "mode": "read"}, 90.3, 4),
])
def test_analysis_finish_uses_current_committed_sum_and_server_permissions(client, context, expected, rows):
    seed_fractional_amounts(client)

    class AggregateProvider(Provider):
        def stream(self, messages, tools, **options):
            assert not options  # Normal tool selection is never globally forced.
            if self.step == 0:
                self.step += 1
                spec = next(spec for spec in tools if spec["function"]["name"] == "analyze_data_table")
                assert spec["function"]["parameters"]["properties"]["table_id"]["enum"] == ["ledger"]
                assert "明细额" in spec["function"]["description"]
                yield {"type": "tool", "id": "real-sum", "name": "analyze_data_table", "arguments": {
                    "table_id": "ledger", "metrics": [{"op": "sum", "field": "amount"}],
                }}
            else:
                result = json.loads(next(message["content"] for message in reversed(messages) if message["role"] == "tool"))
                yield terminal("analysis", analysis_id=result["analysis_id"], metric_keys=["sum:amount"])

    client.app.state.conversation_factory = AggregateProvider
    detail = finish(client, send(client, select_profile(client), text="重新计算当前范围的金额合计并说明归档权限。", context=context))
    assert detail["runs"][0]["status"] == "completed"
    assert detail["runs"][0]["usage"]["answer_kind"] == "analysis"
    aggregate = next(tool for tool in detail["tools"] if tool["name"] == "analyze_data_table")
    assert aggregate["status"] == "completed"
    assert aggregate["result"]["totals"] == {"sum:amount": expected}
    assert aggregate["result"]["source"]["row_count"] == rows
    answer = "\n".join(p["text"] for p in detail["messages"][-1]["parts"] if p["type"] == "text")
    assert str(expected) in answer and f"{rows} 条" in answer
    assert "不能修改、删除或归档" in answer
    assert all(tool["name"] != "finish_answer" for tool in detail["tools"])
    with client.app.state.session_factory() as session:
        proof = session.get(AssistantToolCall, aggregate["id"])
        assert proof.run_id == detail["runs"][0]["id"]


def test_general_answer_is_natural_without_business_reads_or_an_extra_inference(client):
    calls = []

    class GeneralProvider(Provider):
        def stream(self, messages, tools, **options):
            calls.append(options)
            yield terminal(text="你好，我们可以继续。")

    client.app.state.conversation_factory = GeneralProvider
    detail = finish(client, send(client, select_profile(client), context={"table_ids": ["ledger"], "mode": "read"}))
    assert detail["runs"][0]["status"] == "completed"
    assert detail["messages"][-1]["parts"] == [{"type": "text", "text": "你好，我们可以继续。"}]
    assert detail["tools"] == [] and calls == [{}]
    assert detail["runs"][0]["usage"]["answer_kind"] == "general"


def test_repeat_bare_prose_fails_once_without_saving_any_draft(client):
    calls = []

    class BareProvider(Provider):
        def stream(self, messages, tools, **options):
            calls.append(options)
            yield {"type": "text", "text": "不带工具标识也不能冒充已完成的统计：888.8"}

    client.app.state.conversation_factory = BareProvider
    detail = finish(client, send(client, select_profile(client), context={"table_id": "ledger", "mode": "read"}))
    assert detail["runs"][0]["status"] == "failed"
    assert len(calls) == 2
    assert detail["messages"][-1]["parts"] == []
    assert "未能按要求完成回答" in detail["runs"][0]["error"]


@pytest.mark.parametrize("source_kind", ["rows", "records", "historical", "failed", "invented", "metric"])
def test_analysis_completion_rejects_non_current_aggregate_evidence(client, source_kind):
    from document_pipeline_api.services.assistant_answers import FinishAnswer, render_answer
    from document_pipeline_api.services.assistant_tools import execute_tool

    class GeneralProvider(Provider):
        def stream(self, messages, tools, **options):
            yield terminal(text="一般说明。")

    client.app.state.conversation_factory = GeneralProvider
    profile = select_profile(client)
    previous = send(client, profile, context={"table_id": "ledger", "mode": "read"})
    finish(client, previous)
    started = send(client, profile, thread_id=previous.json()["thread_id"], context={"table_id": "ledger", "mode": "read"})
    finish(client, started)
    with client.app.state.session_factory() as session:
        context = Context(table_id="ledger", mode="read")
        tool_name = "read_data_rows" if source_kind == "rows" else "analyze_data_table"
        arguments = {"table_id": "ledger", **({"records": True} if source_kind == "records" else {})}
        result = execute_tool(session, context, tool_name, arguments, {})
        run_id = started.json()["run_id"]
        call = AssistantToolCall(id="proof", run_id=previous.json()["run_id"] if source_kind == "historical" else run_id,
                                 name=tool_name, status="failed" if source_kind == "failed" else "completed",
                                 arguments_json='{}', result_json=json.dumps(result))
        session.add(call)
        session.flush()
        target = "invented" if source_kind == "invented" else result["analysis_id"]
        with pytest.raises(ValueError, match="本轮.*统计"):
            render_answer(session, run_id, context, FinishAnswer(kind="analysis", analysis_id=target,
                          metric_keys=["sum:amount"] if source_kind == "metric" else []))


def test_analysis_contract_does_not_accept_model_numbers_or_text(client):
    from pydantic import ValidationError
    from document_pipeline_api.services.assistant_answers import FinishAnswer

    with pytest.raises(ValidationError):
        FinishAnswer(kind="analysis", analysis_id="proof", text="合计999")
    with pytest.raises(ValidationError):
        FinishAnswer.model_validate({"kind": "analysis", "analysis_id": "proof", "totals": {"sum:amount": 999}})


def test_single_table_metadata_does_not_grant_an_unselected_table(client):
    seed_fractional_amounts(client)
    with client.app.state.session_factory() as session:
        specs = scoped_tool_specs(session, Context(table_ids=["ledger"], mode="read"), {}, False, "计算合计", stable=True)
        schema = next(s["function"]["parameters"] for s in specs if s["function"]["name"] == "analyze_data_table")
        assert schema["properties"]["table_id"]["enum"] == ["ledger"]
        assert "control" not in json.dumps(schema)


@pytest.mark.parametrize("stop_action", ["cancel", "revoke"])
def test_cancellation_or_scope_change_cannot_publish_buffered_terminal_text(client, stop_action):
    entered, release = threading.Event(), threading.Event()

    class HeldTerminalProvider(Provider):
        def close(self):
            release.set()

        def stream(self, messages, tools, **options):
            yield {"type": "text", "text": "不能发布的草稿。"}
            yield terminal(text="取消后的终结正文也不能发布。")
            entered.set()
            assert release.wait(5)

    client.app.state.conversation_factory = HeldTerminalProvider
    profile = select_profile(client)
    started = send(client, profile, context={"table_id": "ledger", "mode": "read"})
    try:
        assert entered.wait(5)
        payload = started.json()
        if stop_action == "cancel":
            response = client.post(f"/api/v1/assistant/runs/{payload['run_id']}/cancel")
        else:
            response = client.put(f"/api/v1/assistant/threads/{payload['thread_id']}/authorization", json={
                "context": {}, "profile_id": profile["id"], "profile_version": profile["version"],
                "remote_authorized": False,
            })
        assert response.status_code == 200
    finally:
        release.set()
    detail = finish(client, started)
    assert detail["runs"][0]["status"] == "cancelled"
    assert detail["messages"][-1]["parts"] == []


def test_finish_mixed_with_business_call_rejects_whole_batch_before_execution(client):
    class MixedProvider(Provider):
        def stream(self, messages, tools, **options):
            yield terminal(text="已经完成。")
            yield {"type": "tool", "id": "unexpected", "name": "analyze_data_table", "arguments": {"table_id": "ledger"}}

    client.app.state.conversation_factory = MixedProvider
    detail = finish(client, send(client, select_profile(client), context={"table_id": "ledger"}))
    assert detail["runs"][0]["status"] == "failed"
    assert not detail["tools"] and not detail["messages"][-1]["parts"]


def test_actual_tool_result_survives_later_unverified_text_timeout(client):
    import httpx
    class InterruptedProvider(Provider):
        def stream(self, messages, tools, **options):
            if self.step == 0:
                self.step += 1
                yield {"type": "tool", "id": "actual", "name": "analyze_data_table", "arguments": {"table_id": "ledger"}}
            else:
                yield {"type": "text", "text": "未完成的金额解释。"}
                raise httpx.ReadTimeout("synthetic stream interruption")

    client.app.state.conversation_factory = InterruptedProvider
    detail = finish(client, send(client, select_profile(client), context={"table_id": "ledger"}))
    assert detail["runs"][0]["status"] == "failed"
    assert [part["type"] for part in detail["messages"][-1]["parts"]] == ["tool"]
    assert detail["tools"][0]["status"] == "completed"
    assert detail["runs"][0]["usage"]["calls"][-1]["status"] == "interrupted"


def test_missing_aggregate_can_be_recovered_by_real_tool_before_verified_finish(client):
    seed_fractional_amounts(client)

    class RepairEvidenceProvider(Provider):
        def stream(self, messages, tools, **options):
            step, self.step = self.step, self.step + 1
            if step == 0:
                yield {"type": "tool", "id": "read", "name": "read_data_rows", "arguments": {"table_id": "ledger", "limit": 2}}
            elif step == 2:
                assert "error" in json.loads(messages[-1]["content"])
                yield {"type": "tool", "id": "sum", "name": "analyze_data_table", "arguments": {
                    "table_id": "ledger", "metrics": [{"op": "sum", "field": "amount"}],
                }}
            else:
                result = json.loads(messages[-1]["content"])
                yield terminal("analysis", analysis_id=result["analysis_id"])

    client.app.state.conversation_factory = RepairEvidenceProvider
    detail = finish(client, send(client, select_profile(client), context={"table_ids": ["ledger"], "mode": "read"}))
    assert detail["runs"][0]["status"] == "completed"
    assert detail["runs"][0]["usage"]["answer_kind"] == "analysis"
    assert detail["runs"][0]["usage"]["model_calls"] == 4
    aggregate = next(tool for tool in detail["tools"] if tool["name"] == "analyze_data_table")
    assert aggregate["result"]["totals"] == {"sum:amount": 90.3}
    assert "90.3" in detail["messages"][-1]["parts"][-1]["text"]


def test_rejected_completion_arguments_are_not_cached_or_replayed_as_history(client):
    marker = "REJECTED-COMPLETION-777.77"

    class RejectThenFinish(Provider):
        def stream(self, messages, tools, **options):
            self.step += 1
            if self.step == 1:
                yield {**terminal("analysis", analysis_id="invented", text=marker), "id": "bad-finish"}
            else:
                yield {**terminal("limitation", text="尚未得到统计结果。"), "id": "valid-finish"}

    client.app.state.conversation_factory = RejectThenFinish
    profile = select_profile(client)
    first = finish(client, send(client, profile, context={"table_ids": ["ledger"], "mode": "read"}))
    assert first["runs"][0]["status"] == "completed"
    with client.app.state.session_factory() as session:
        message = session.get(AssistantMessage, first["messages"][-1]["id"])
        assert marker not in (message.native_context_json or "")
    captured = []

    class Followup(Provider):
        def stream(self, messages, tools, **options):
            captured.append(messages)
            yield terminal(text="继续一般说明。")

    client.app.state.conversation_factory = Followup
    finish(client, send(client, profile, thread_id=first["id"], context={"table_ids": ["ledger"], "mode": "read"}))
    assert marker not in json.dumps(captured)
