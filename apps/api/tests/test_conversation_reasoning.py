"""Offline protocol contracts: a requested switch is not observed server behavior."""

import json
import threading

import httpx
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.model_providers.conversation import ConversationProvider
from document_pipeline_api.services.assistant_usage import tracked_stream


def provider_for(tmp_path, *, service="lm_studio", effort=None, model="qwen3.5-4b", url=None, response=None):
    settings = Settings(
        database_url="sqlite://", storage_dir=tmp_path,
        model_provider=service, model_name=model, model_reasoning_effort=effort,
        model_base_url=url or "http://127.0.0.1:1234/v1",
    )
    provider = ConversationProvider(settings, threading.Event())
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if response:
            return response()
        if service == "ollama":
            return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}, "done": True}) + "\n")
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "ok"}}]}))

    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider, requests


def sse(*items):
    return "\n\n".join("data: " + json.dumps(item) for item in items) + "\n\ndata: [DONE]\n\n"


@pytest.mark.parametrize("service,url,model,effort,expected,requested", [
    ("lm_studio", None, "qwen3.5-4b", None, {"reasoning_effort": "none"}, "off"),
    ("lm_studio", None, "qwen3.5-9b", "none", {"reasoning_effort": "none"}, "off"),
    ("lm_studio", None, "qwen3.5-9b", "high", {"reasoning_effort": "high"}, "on"),
    ("ollama", None, "qwen3.5:9b", None, {"think": False}, "off"),
    ("ollama", None, "qwen3.5:9b", "none", {"think": False}, "off"),
    ("ollama", None, "qwen3.5:9b", "high", {"think": True}, "on"),
    ("ollama", None, "gpt-oss:20b", "low", {"think": "low"}, "on"),
    ("ollama", None, "gpt-oss:20b", None, {"think": False}, "off"),
    ("openai_compatible", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.6-flash", None, {"enable_thinking": False}, "off"),
    ("openai_compatible", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.6-flash", "none", {"enable_thinking": False}, "off"),
    ("openai_compatible", "https://DASHSCOPE.ALIYUNCS.COM./compatible-mode/v1", "Qwen3.5-Plus", "high", {"enable_thinking": True}, "on"),
    ("openai_compatible", "https://api.deepseek.com/v1", "deepseek-flash", None, {"thinking": {"type": "disabled"}}, "off"),
    ("openai_compatible", "https://api.deepseek.com/v1", "deepseek-flash", "high", {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}, "on"),
    ("openai_compatible", "https://ordinary.example/v1", "non-reasoning-model", None, {}, "provider"),
    ("openai_compatible", "https://dashscope.aliyuncs.com.evil.example/v1", "qwen3.6-flash", None, {}, "provider"),
    ("openai_compatible", "https://ordinary.example/v1", "custom", "low", {"reasoning_effort": "low"}, "on"),
])
def test_chat_request_uses_provider_control_without_changing_other_model_defaults(
    tmp_path, service, url, model, effort, expected, requested,
):
    provider, requests = provider_for(tmp_path, service=service, url=url, model=model, effort=effort)
    try:
        events = list(provider.stream([{"role": "user", "content": "synthetic"}], []))
        controls = {key: requests[0][key] for key in ("think", "thinking", "enable_thinking", "reasoning_effort") if key in requests[0]}
        assert controls == expected and len(requests) == 1
        assert provider.settings.model_reasoning_effort == effort
        metadata = [e["usage"] for e in events if e["type"] == "usage"][-1]
        assert metadata == {"reasoning_requested": requested, "reasoning_observed": False, "reasoning_characters": 0}
    finally:
        provider.close()


@pytest.mark.parametrize("field,service", [
    ("reasoning_content", "openai_compatible"), ("reasoning", "lm_studio"), ("thinking", "ollama"),
])
@pytest.mark.parametrize("effort", [None, "high"])
def test_reasoning_channels_do_not_leak_or_prevent_tools_and_only_one_notice(tmp_path, field, service, effort):
    secret_thought = "private synthetic reasoning"
    deltas = [
        {field: None}, {field: ""}, {field: "\n\n"},
        {field: secret_thought}, {field: "second fragment", "content": "已核对。"},
        {"tool_calls": [{"index": 0, "id": "count", "function": {"name": "catalog", "arguments": '{"kind":"tables"}'}}]},
    ]
    if service == "ollama":
        wire = "\n".join(json.dumps({"message": d}) for d in deltas)
        wire += "\n" + json.dumps({"done": True, "message": {}, "prompt_eval_count": 8, "eval_count": 3}) + "\n"
    else:
        wire = sse(*[{"choices": [{"delta": d}]} for d in deltas], {"usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}})
    provider, requests = provider_for(
        tmp_path, service=service, effort=effort,
        url="https://dashscope.aliyuncs.com/compatible-mode/v1" if service == "openai_compatible" else None,
        response=lambda: httpx.Response(200, text=wire),
    )
    usage = {}
    try:
        events = list(tracked_stream(usage, provider, [{"role": "user", "content": "synthetic"}], []))
    finally:
        provider.close()
    assert len(requests) == 1
    assert [e for e in events if e["type"] == "text"] == [{"type": "text", "text": "已核对。"}]
    assert events[-1] == {"type": "tool", "id": "count", "name": "catalog", "arguments": {"kind": "tables"}}
    assert len([e for e in events if e["type"] == "notice"]) == (1 if effort is None else 0)
    assert secret_thought not in json.dumps(events) + json.dumps(usage)
    assert usage["reasoning_observed"] is True
    assert usage["reasoning_characters"] == len(secret_thought + "second fragment")
    assert usage["prompt_tokens"] == 8 and usage["completion_tokens"] == 3 and usage["usage_complete"]


def test_service_reported_reasoning_tokens_are_observation_not_estimated_characters(tmp_path):
    provider, _ = provider_for(tmp_path, response=lambda: httpx.Response(200, text=sse(
        {"choices": [{"delta": {"content": "answer"}}]},
        {"usage": {"prompt_tokens": 2, "completion_tokens": 7, "completion_tokens_details": {"reasoning_tokens": 5}}},
    )))
    try:
        events = list(provider.stream([], []))
    finally:
        provider.close()
    assert len([e for e in events if e["type"] == "notice"]) == 1
    assert events[-1]["usage"]["reasoning_observed"] is True
    assert events[-1]["usage"]["reasoning_characters"] == 0


def test_reasoning_snapshots_replace_within_call_sum_across_calls_and_survive_failure():
    class Provider:
        def __init__(self, fail=False):
            self.fail = fail

        def stream(self, *_):
            for characters in (5, 12):
                yield {"type": "usage", "usage": {
                    "reasoning_requested": "off", "reasoning_observed": True,
                    "reasoning_characters": characters,
                }}
            if self.fail:
                raise ValueError("synthetic interruption")
            yield {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 3}}

    usage = {}
    list(tracked_stream(usage, Provider(), [], []))
    with pytest.raises(ValueError, match="synthetic interruption"):
        list(tracked_stream(usage, Provider(fail=True), [], []))
    assert usage["reasoning_observed"] is True and usage["reasoning_characters"] == 24
    assert usage["reasoning_requested"] == "off" and usage["model_calls"] == 2
    assert usage["calls"][1]["status"] == "interrupted" and not usage["usage_complete"]
    assert usage["completion_tokens"] == 3


def test_reasoning_observation_is_reset_for_each_tool_loop_request(tmp_path):
    responses = iter([
        sse({"choices": [{"delta": {"reasoning": "private", "content": "first"}}]}),
        sse({"choices": [{"delta": {"content": "second"}}]}),
    ])
    provider, _ = provider_for(tmp_path, response=lambda: httpx.Response(200, text=next(responses)))
    usage = {}
    try:
        list(tracked_stream(usage, provider, [], []))
        second = list(tracked_stream(usage, provider, [], []))
    finally:
        provider.close()
    assert not any(e["type"] == "notice" for e in second)
    assert usage["calls"][0]["reasoning_characters"] == 7
    assert usage["calls"][1]["reasoning_characters"] == 0
    assert usage["calls"][1]["reasoning_observed"] is False
    assert usage["reasoning_characters"] == 7 and usage["reasoning_observed"] is True


def test_untrusted_usage_metadata_does_not_become_valid_reasoning_observation():
    class Provider:
        def stream(self, *_):
            yield {"type": "usage", "usage": {
                "reasoning_requested": {}, "reasoning_observed": "false", "reasoning_characters": True,
            }}

    usage = {}
    list(tracked_stream(usage, Provider(), [], []))
    assert "reasoning_requested" not in usage
    assert "reasoning_observed" not in usage
    assert "reasoning_characters" not in usage
