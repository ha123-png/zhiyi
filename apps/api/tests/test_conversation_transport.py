import threading
import json

import httpx
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.model_providers.conversation import (
    ConversationProvider, ConversationTransportError,
)
from test_assistant import client as ledger_fixture, select_profile, send, finish

client = ledger_fixture


@pytest.mark.parametrize("failure", ["eof", "length", "broken_second_call"])
def test_incomplete_or_invalid_tool_batches_never_emit_executable_events(tmp_path, failure):
    from test_conversation_reasoning import provider_for

    calls = [{"index": 0, "id": "first", "type": "function", "function": {"name": "read", "arguments": "{}"}}]
    if failure == "broken_second_call":
        calls.append({"index": 1, "id": "second", "type": "function", "function": {"name": "read", "arguments": "{"}})
    wire = "data: " + json.dumps({"choices": [{"delta": {"content": "partial", "tool_calls": calls},
               "finish_reason": "length" if failure == "length" else "tool_calls"}]}) + "\n\n"
    if failure != "eof":
        wire += "data: [DONE]\n\n"
    provider, requests = provider_for(tmp_path, response=lambda: httpx.Response(200, text=wire))
    events = []
    try:
        with pytest.raises(ValueError):
            for event in provider.stream([{"role": "user", "content": "synthetic"}], []):
                events.append(event)
    finally:
        provider.close()
    assert len(requests) == 1
    assert any(event["type"] == "text" for event in events)
    assert not any(event["type"] == "tool" for event in events)


@pytest.mark.parametrize("url,timeout,expected", [
    ("http://127.0.0.1:1234/v1", 90, 5),
    ("http://localhost.:1234/v1", 90, 5),
    ("http://[::1]:1234/v1", 90, 5),
    ("https://models.example.test/v1", 90, 15),
    ("https://models.example.test/v1", 8, 8),
    ("http://localhost:1234/v1", 2, 2),
])
def test_connect_budget_is_bounded_and_preserves_configured_read_timeout(tmp_path, url, timeout, expected):
    provider = ConversationProvider(Settings(
        database_url="sqlite://", storage_dir=tmp_path, model_base_url=url,
        model_timeout_seconds=timeout,
    ), threading.Event())
    try:
        assert provider.client.timeout.connect == expected
        assert provider.client.timeout.read == timeout
    finally:
        provider.close()


@pytest.mark.parametrize("error_type,code,phrase", [
    (httpx.ConnectTimeout, "chat_connect_timeout", "连接聊天服务超时"),
    (httpx.ConnectError, "chat_connect_failed", "无法连接聊天服务"),
    (httpx.ReadTimeout, "chat_read_timeout", "等待聊天服务回复超时"),
    (httpx.WriteTimeout, "chat_send_failed", "发送问题"),
    (httpx.PoolTimeout, "chat_connection_busy", "聊天连接繁忙"),
    (httpx.ReadError, "chat_connection_interrupted", "聊天连接中断"),
    (httpx.RemoteProtocolError, "chat_connection_interrupted", "聊天连接中断"),
])
def test_transport_errors_are_actionable_redacted_and_never_retried(tmp_path, error_type, code, phrase):
    secret = "synthetic-unprefixed-sensitive-value"
    settings = Settings(database_url="sqlite://", storage_dir=tmp_path,
                        model_base_url="https://models.example.test/v1", model_api_key=secret)
    provider = ConversationProvider(settings, threading.Event())
    requests = []
    original = error_type("_ssl.c:993: handshake timed out " + secret)

    def fail(request):
        requests.append(request)
        raise original

    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(ConversationTransportError) as captured:
            list(provider.stream([{"role": "user", "content": "synthetic"}], []))
        error = captured.value
        assert len(requests) == 1 and error.__cause__ is original
        assert phrase in str(error) and error.code == code
        assert "_ssl.c" not in str(error) and secret not in str(error)
        assert "_ssl.c" in error.diagnostic and secret not in error.diagnostic
    finally:
        provider.close()


def test_cancel_during_connect_error_remains_cancellation(tmp_path):
    cancel = threading.Event()
    provider = ConversationProvider(Settings(database_url="sqlite://", storage_dir=tmp_path), cancel)

    def stop(request):
        cancel.set()
        raise httpx.ConnectError("connection was closed")

    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(stop))
    try:
        with pytest.raises(InterruptedError, match="已停止生成"):
            list(provider.stream([{"role": "user", "content": "synthetic"}], []))
    finally:
        provider.close()


def test_partial_reply_survives_read_timeout_with_saved_redacted_diagnostic(client):
    requests = []
    closed = []

    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield 'data: {"choices":[{"delta":{"content":"已经收到的部分回答。"}}]}\n\n'.encode()
            raise httpx.ReadTimeout("_ssl.c:993: timed out; api_key=synthetic-key")

        def close(self):
            closed.append(True)

    def response(request):
        requests.append(request)
        return httpx.Response(200, stream=InterruptedStream())

    class InterruptedProvider(ConversationProvider):
        def __init__(self, settings, cancel):
            super().__init__(settings, cancel)
            self.client.close()
            self.client = httpx.Client(transport=httpx.MockTransport(response))

    client.app.state.conversation_factory = InterruptedProvider
    result = finish(client, send(client, select_profile(client)))
    run = result["runs"][0]
    assert run["status"] == "failed"
    assert "等待聊天服务回复超时" in run["error"] and "_ssl.c" not in run["error"]
    assert result["messages"][-1]["parts"] == [{"type": "text", "text": "已经收到的部分回答。"}]
    assert run["usage"]["model_calls"] == 1 and not run["usage"]["usage_complete"]
    assert run["usage"]["error_code"] == "chat_read_timeout"
    assert "ReadTimeout" in run["usage"]["error_diagnostic"]
    assert "synthetic-key" not in run["usage"]["error_diagnostic"]
    assert len(requests) == 1 and closed
