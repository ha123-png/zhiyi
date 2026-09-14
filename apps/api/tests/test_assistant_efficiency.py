from copy import deepcopy

import pytest

from document_pipeline_api.services.assistant_usage import tracked_stream
from test_assistant import client as ledger_fixture, ScriptedProvider, select_profile, send, finish

client = ledger_fixture


def test_stable_tools_do_not_include_random_snapshot_ids(client):
    from document_pipeline_api.services.assistant_tools import scoped_tool_specs, Context

    with client.app.state.session_factory() as session:
        context = Context(table_id="ledger")
        first = scoped_tool_specs(session, context, {}, False, "分析", stable=True)
        second = scoped_tool_specs(
            session,
            context,
            {
                "random-new-id": {
                    "analysis_id": "random-new-id",
                    "data": [{"value": 1}],
                    "metric_keys": ["count:*"],
                },
                "_history": {"items": []},
                "_tool_results": {},
            },
            False,
            "分析",
            stable=True,
        )
        assert first == second


def test_row_number_query_intersects_authorized_selection(client):
    from document_pipeline_api.services.assistant_tools import execute_tool, Context

    with client.app.state.session_factory() as session:
        result = execute_tool(
            session,
            Context(table_id="ledger", row_ids=[1]),
            "read_data_rows",
            {"table_id": "ledger", "row_ids": [1, 2]},
            {},
        )
        assert [r["row_id"] for r in result["rows"]] == [1]
        result = execute_tool(
            session,
            Context(table_id="ledger", row_ids=[1]),
            "read_data_rows",
            {"table_id": "ledger", "row_ids": "[1,2]"},
            {},
        )
        assert [r["row_id"] for r in result["rows"]] == [1]


def test_usage_snapshots_are_replaced_within_call_and_summed_across_calls():
    class Provider:
        def stream(self, *_):
            for value in (10, 20):
                yield {
                    "type": "usage",
                    "usage": {
                        "prompt_tokens": value,
                        "completion_tokens": 3,
                        "total_tokens": value + 3,
                        "prompt_tokens_details": {"cached_tokens": 5},
                    },
                }

    usage = {}
    for _ in range(2):
        list(tracked_stream(usage, Provider(), [], []))
    assert usage["prompt_tokens"] == 40
    assert usage["completion_tokens"] == 6
    assert usage["cached_tokens"] == 10
    assert usage["cache_hit_ratio"] == 0.25
    assert usage["model_calls"] == 2 and usage["usage_complete"]


def test_failed_call_without_usage_remains_unknown_not_free():
    class Provider:
        def stream(self, *_):
            yield {"type": "text", "text": "partial"}
            raise ValueError("interrupted")

    usage = {}
    with pytest.raises(ValueError):
        list(tracked_stream(usage, Provider(), [], []))
    assert usage["model_calls"] == 1
    assert not usage["usage_complete"]
    assert "prompt_tokens" not in usage
    assert usage["cache_hit_ratio"] is None
    assert usage["calls"][0]["status"] == "interrupted"


def test_deepseek_usage_and_missing_cache_coverage():
    class Provider:
        def stream(self, *_):
            yield {
                "type": "usage",
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_tokens_details": None,
                    "prompt_cache_hit_tokens": 80,
                },
            }

    class UnknownCache:
        def stream(self, *_):
            yield {"type": "usage", "usage": {"prompt_tokens": 200}}

    usage = {}
    list(tracked_stream(usage, Provider(), [], []))
    list(tracked_stream(usage, UnknownCache(), [], []))
    assert usage["prompt_tokens"] == 300
    assert usage["cache_hit_ratio"] == 0.8
    assert usage["cache_measured_prompt_tokens"] == 100
    assert usage["calls_with_cache_usage"] == 1


@pytest.mark.parametrize("cloud", [True, False])
def test_cache_marks_reusable_boundaries_without_mutating_history(tmp_path, cloud):
    import json
    import threading
    import httpx
    from document_pipeline_api.config import Settings
    from document_pipeline_api.model_providers.conversation import ConversationProvider

    settings = Settings(
        database_url="sqlite://",
        storage_dir=tmp_path,
        model_name="qwen3.6-flash",
        model_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        if cloud
        else "http://localhost:1234/v1",
    )
    provider = ConversationProvider(settings, threading.Event())
    received = []

    def handler(request):
        received.append(json.loads(request.content))
        return httpx.Response(
            200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        )

    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    messages = [
        {"role": "system", "content": "稳定提示" * 500},
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "之前回答"},
        {"role": "user", "content": "本轮问题"},
    ]
    original = deepcopy(messages)
    try:
        list(provider.stream(messages, [{"type": "function", "function": {"name": "catalog"}}]))
    finally:
        provider.close()
    assert messages == original
    sent = received[0]["messages"]
    if cloud:
        assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert sent[-1]["content"][0]["text"] == "本轮问题"
        assert sent[1:3] == original[1:3]
    else:
        assert sent == original


def test_cloud_sized_history_retains_recent_text_and_stable_system(client):
    class Provider(ScriptedProvider):
        captured = []

        def stream(self, messages, tools):
            self.captured.append(deepcopy(messages))
            yield {"type": "text", "text": "保留完整段落" * 600 + "末尾关键结论"}

    client.app.state.conversation_factory = Provider
    profile = select_profile(client)
    first = finish(client, send(client, profile, text="请记住这些内容"))
    second = finish(client, send(client, profile, thread_id=first["id"], text="继续"))
    assert second["runs"][0]["status"] == "completed"
    assert Provider.captured[0][0] == Provider.captured[1][0]
    assert any("末尾关键结论" in m["content"] for m in Provider.captured[1][:-1])
