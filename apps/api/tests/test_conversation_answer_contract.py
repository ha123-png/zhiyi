"""Offline named-tool wire compatibility; no model endpoint is called."""

import pytest
import httpx

from test_conversation_reasoning import provider_for

TOOLS = [{"type": "function", "function": {"name": "finish_answer", "parameters": {"type": "object"}}}]
CHOICE = {"type": "function", "function": {"name": "finish_answer"}}


def test_named_completion_choice_is_explicit_and_not_retained_on_following_request(tmp_path):
    provider, requests = provider_for(tmp_path)
    try:
        list(provider.stream([], TOOLS, tool_choice=CHOICE))
        list(provider.stream([], TOOLS))
    finally:
        provider.close()
    assert requests[0]["tool_choice"] == CHOICE
    assert "tool_choice" not in requests[1]
    assert requests[0]["tools"] == requests[1]["tools"] == TOOLS


@pytest.mark.parametrize("status", [400, 422])
def test_unsupported_named_choice_fails_once_without_silent_transport_retry(tmp_path, status):
    provider, requests = provider_for(tmp_path, response=lambda: httpx.Response(status, json={"error": "unsupported tool_choice"}))
    try:
        with pytest.raises(ValueError, match="拒绝定向完成回答"):
            list(provider.stream([], TOOLS, tool_choice=CHOICE))
    finally:
        provider.close()
    assert len(requests) == 1


def test_ollama_does_not_claim_support_for_named_tool_choice_or_send_a_fallback_request(tmp_path):
    provider, requests = provider_for(tmp_path, service="ollama")
    try:
        with pytest.raises(ValueError, match="Ollama.*不支持定向"):
            list(provider.stream([], TOOLS, tool_choice=CHOICE))
    finally:
        provider.close()
    assert requests == []
