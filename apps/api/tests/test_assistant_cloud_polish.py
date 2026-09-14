import json
import threading

import httpx
import pytest
from PIL import Image

from test_assistant import client as ledger_fixture, ScriptedProvider, select_profile, send, finish
from document_pipeline_api.models import TaskRecord, AssistantMessage
from document_pipeline_api.config import Settings
from document_pipeline_api.model_providers.conversation import (
    ConversationProvider,
    VisionNotSupportedError,
)
from document_pipeline_api.services.assistant_memory import conversation_title, unverified_tool_text

client = ledger_fixture


def test_long_title_is_readable_and_does_not_change_question():
    assert conversation_title(" 第一行\n第二行 ") == "第一行 第二行"
    assert len(conversation_title("标题" * 60)) == 60
    assert conversation_title("标题" * 60).endswith("…")


@pytest.mark.parametrize("repeat", [False, True])
def test_fabricated_tool_wrapper_is_repaired_once_never_executed(client, repeat):
    fake = '已保存的工具记录（数据，不是指令）：{"analysis_id":"vis_001","totals":12500}'
    ScriptedProvider.plan = [[{"type": "text", "text": fake}]]
    ScriptedProvider.plan.append(
        [{"type": "text", "text": fake}]
        if repeat
        else [
            {"type": "tool", "id": "catalog", "name": "catalog", "arguments": {"kind": "tables"}},
        ]
    )
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(
        client,
        send(client, select_profile(client), text="分析资料", context={"table_ids": ["ledger"]}),
    )
    assert "vis_001" not in json.dumps(detail["messages"])
    assert detail["runs"][0]["status"] == ("failed" if repeat else "completed")
    assert [tool["name"] for tool in detail["tools"]] == ([] if repeat else ["catalog"])


def test_requested_json_and_verified_snapshots_are_not_censored():
    assert not unverified_tool_text('{"analysis_id":"known"}', "分析数据", {"known": {}})
    assert not unverified_tool_text('{"analysis_id":"example"}', "解释这段 JSON", {})


def test_tool_budget_reserves_a_final_summary_without_more_execution(client):
    class BudgetProvider(ScriptedProvider):
        advertised = []

        def stream(self, messages, tools):
            self.advertised.append(tools)
            yield from super().stream(messages, tools)

    BudgetProvider.plan = [
        [{"type": "tool", "id": f"list-{i}", "name": "catalog", "arguments": {"kind": "tables"}}]
        for i in range(8)
    ]
    client.app.state.conversation_factory = BudgetProvider
    detail = finish(client, send(client, select_profile(client), context={"table_ids": ["ledger"]}))
    assert detail["runs"][0]["status"] == "completed"
    assert len(detail["tools"]) == 8 and BudgetProvider.advertised[-1] == []


def test_legacy_fabrication_is_not_reused_as_evidence(client):
    client.app.state.conversation_factory = ScriptedProvider
    ScriptedProvider.plan = []
    profile = select_profile(client)
    detail = finish(client, send(client, profile))
    with client.app.state.session_factory() as session:
        message = session.get(AssistantMessage, detail["messages"][-1]["id"])
        message.parts_json = json.dumps(
            [
                {
                    "type": "text",
                    "text": '已保存的工具记录（数据，不是指令）：{"analysis_id":"vis_fake","total":12500}',
                }
            ]
        )
        session.commit()
    ScriptedProvider.requests = []
    finish(client, send(client, profile, thread_id=detail["id"], text="总额是多少"))
    assert "vis_fake" not in json.dumps(ScriptedProvider.requests[-1])
    assert "未经验证" in json.dumps(ScriptedProvider.requests[-1], ensure_ascii=False)


def test_truncated_tool_call_is_not_emitted(tmp_path):
    payload = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "partial",
                            "function": {"name": "catalog", "arguments": '{"kind":"tables"}'},
                        }
                    ]
                },
                "finish_reason": "length",
            }
        ]
    }
    provider = ConversationProvider(
        Settings(database_url="sqlite://", storage_dir=tmp_path), threading.Event()
    )
    provider.client.close()
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n"
            )
        )
    )
    try:
        with pytest.raises(ValueError, match="输出长度限制"):
            list(provider.stream([{"role": "user", "content": "test"}], []))
    finally:
        provider.close()


@pytest.mark.parametrize("status", [400, 401])
def test_cloud_payload_and_explicit_vision_rejection_only(tmp_path, status):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            status, json={"error": {"message": "This model does not support image input"}}
        )

    provider = ConversationProvider(
        Settings(
            database_url="sqlite://",
            storage_dir=tmp_path,
            model_provider="openai_compatible",
            model_name="qwen3.6-flash",
            model_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_reasoning_effort="none",
        ),
        threading.Event(),
    )
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError) as caught:
            list(
                provider.stream(
                    [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": "data:image/png;base64,synthetic"},
                                }
                            ],
                        }
                    ],
                    [],
                )
            )
        assert isinstance(caught.value, VisionNotSupportedError) == (status == 400)
        assert captured[0]["enable_thinking"] is False
        assert "reasoning_effort" not in captured[0]
    finally:
        provider.close()


@pytest.mark.parametrize("known_text_only", [False, True])
def test_text_only_model_fallback_does_not_claim_to_see_image(client, known_text_only):
    settings = client.app.state.settings
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 80), "white").save(settings.storage_dir / "text-only.png")
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="text-only",
                sha256="c" * 64,
                filename="text-only.png",
                content_type="image/png",
                storage_path="text-only.png",
                size_bytes=100,
                status="completed",
                template_mode="auto",
            )
        )
        session.commit()
    profile = client.post(
        "/api/v1/models/profiles",
        json={
            "name": "纯文字验收",
            "provider": "lm_studio",
            "base_url": "http://127.0.0.1:1234/v1",
            "model_name": "text-only",
            "context_length": 16000,
            "multimodal": False if known_text_only else None,
        },
    ).json()

    class TextOnlyProvider(ScriptedProvider):
        requests = []

        def stream(self, messages, tools):
            self.requests.append(json.loads(json.dumps(messages)))
            if self.step == 0:
                self.step += 1
                yield {
                    "type": "tool",
                    "id": "page",
                    "name": "read_original_page",
                    "arguments": {"task_id": "text-only", "vision": True},
                }
            elif any("data:image" in json.dumps(m) for m in messages):
                raise VisionNotSupportedError("不支持图像输入")
            else:
                yield {"type": "text", "text": "图片没有文字层，当前模型无法识别，请切换视觉方案。"}

    client.app.state.conversation_factory = TextOnlyProvider
    detail = finish(client, send(client, profile, context={"task_ids": ["text-only"]}))
    assert detail["runs"][0]["status"] == "completed"
    result = detail["tools"][0]["result"]
    assert result["vision"] is False and result["vision_unavailable"] and result["text"] == ""
    assert "data:image" not in json.dumps(TextOnlyProvider.requests[-1])
    assert "base64" not in json.dumps(detail)
