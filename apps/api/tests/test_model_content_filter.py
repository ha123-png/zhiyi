"""A service content refusal must not become a successful result or tool batch."""

import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_providers.base import ModelRequestRejectedError, ModelResponseError
from document_pipeline_api.model_providers.conversation import ConversationProvider
from document_pipeline_api.model_providers.openai_compatible import OpenAICompatibleProvider
from document_pipeline_api.models import ExtractionRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.services.extraction import process_task
from test_assistant import client as ledger_fixture, finish, select_profile, send

client = ledger_fixture


def _stream_provider(tmp_path, reason):
    frames = [
        {"choices": [{"delta": {"content": "尚未核实的草稿。", "tool_calls": [
            {"index": 0, "id": "first", "type": "function", "function": {
                "name": "read_resource", "arguments": '{"kind":"table","id":"synthetic"}',
            }},
            {"index": 1, "id": "second", "type": "function", "function": {
                "name": "finish_answer", "arguments": '{"kind":"general","text":"unverified"}',
            }},
        ]}}]},
        {"choices": [{"delta": {}, "finish_reason": reason}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}},
    ]
    wire = "\n\n".join("data: " + json.dumps(frame) for frame in frames) + "\n\ndata: [DONE]\n\n"
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, text=wire)

    provider = ConversationProvider(Settings(
        database_url="sqlite://", storage_dir=tmp_path, model_base_url="https://models.invalid/v1",
    ), threading.Event())
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(response))
    return provider, requests


@pytest.mark.parametrize("reason", ["content_filter", "length"])
def test_rejected_or_truncated_stream_never_emits_any_tool(tmp_path, reason):
    provider, requests = _stream_provider(tmp_path, reason)
    events = []
    try:
        with pytest.raises(ValueError) as caught:
            for event in provider.stream([{"role": "user", "content": "合成问题"}], []):
                events.append(event)
    finally:
        provider.close()
    assert len(requests) == 1
    assert not [event for event in events if event["type"] == "tool"]
    assert any(event["type"] == "usage" and event["usage"].get("total_tokens") == 17 for event in events)
    if reason == "content_filter":
        assert "内容限制" in str(caught.value) and "服务" in str(caught.value)
        assert "缩小" not in str(caught.value) and "长度" not in str(caught.value)
    else:
        assert "长度" in str(caught.value)


def test_completed_stream_still_emits_full_valid_batch(tmp_path):
    provider, requests = _stream_provider(tmp_path, "tool_calls")
    try:
        events = list(provider.stream([{"role": "user", "content": "合成问题"}], []))
    finally:
        provider.close()
    assert len(requests) == 1
    assert [event["name"] for event in events if event["type"] == "tool"] == ["read_resource", "finish_answer"]


def test_filtered_assistant_response_fails_without_creating_tool_records(client):
    requests = []
    wire = "data: " + json.dumps({"choices": [{"delta": {"content": "未核实的合成草稿，不应显示或保存。", "tool_calls": [{
        "index": 0, "id": "filtered-call", "type": "function", "function": {
            "name": "read_resource", "arguments": '{"kind":"table","id":"ledger"}',
        },
    }]}}]}) + '\n\ndata: {"choices":[{"delta":{},"finish_reason":"content_filter"}]}\n\ndata: [DONE]\n\n'

    def response(request):
        requests.append(request)
        return httpx.Response(200, text=wire)

    class FilteredProvider(ConversationProvider):
        def __init__(self, settings, cancel):
            super().__init__(settings, cancel)
            self.client.close()
            self.client = httpx.Client(transport=httpx.MockTransport(response))

    client.app.state.conversation_factory = FilteredProvider
    result = finish(client, send(client, select_profile(client), text="查看所选表结构", context={"table_id": "ledger"}))
    assert result["runs"][0]["status"] == "failed"
    assert "内容限制" in result["runs"][0]["error"]
    assert result["messages"][-1]["parts"] == []
    assert result["tools"] == []
    assert len(requests) == 1


def test_content_filter_failure_keeps_original_and_saves_no_extraction(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'synthetic.db'}", storage_dir=tmp_path / "uploads")
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"header":{"name":"synthetic"},"items":[]}'},
                         "finish_reason": "content_filter"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
        })

    provider = OpenAICompatibleProvider("https://models.invalid/v1", "synthetic",
        client=httpx.Client(transport=httpx.MockTransport(response)))
    original = b"Synthetic source retained after a content refusal."
    try:
        with TestClient(create_app(settings)) as client:
            template = client.post("/api/v1/templates", json={
                "name": "Synthetic content refusal", "fields": [{"key": "name", "label": "名称"}],
            })
            assert template.status_code == 201
            uploaded = client.post("/api/v1/tasks", files={"file": ("synthetic.txt", original, "text/plain")},
                                   data={"template_id": template.json()["id"]})
            assert uploaded.status_code == 201
            task_id = uploaded.json()["id"]
            with client.app.state.session_factory() as session:
                with pytest.raises(ModelRequestRejectedError) as caught:
                    process_task(session, settings, task_id, client=provider)
                session.expire_all()
                task = session.get(TaskRecord, task_id)
                assert task.status == "failed" and task.failure_code == "model_request_rejected"
                assert "内容限制" in task.failure_message
                assert "缩小" not in task.failure_message and "输出设置" not in task.failure_message
                assert session.get(ExtractionRecord, task_id) is None
                assert session.get(FileCurrentDataRecord, task_id) is None
            assert str(caught.value) == "模型服务因内容限制拒绝了本次请求。请查看服务的内容限制或联系服务方。"
            assert client.get(f"/api/v1/tasks/{task_id}/file").content == original
    finally:
        provider.close()
    assert len(requests) == 1 and provider.last_usage["total_tokens"] == 9


def test_length_remains_a_response_failure():
    class Result(BaseModel):
        answer: str

    provider = OpenAICompatibleProvider("https://models.invalid/v1", "synthetic", client=httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "choices": [{"message": {"content": '{"answer":"unfinished"}'}, "finish_reason": "length"}],
        }))))
    try:
        with pytest.raises(ModelResponseError, match="输出未完成"):
            provider.complete_text("synthetic", Result)
    finally:
        provider.close()
