"""Shared model errors should help users without exposing endpoint credentials."""

import traceback
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from document_pipeline_api.model_providers import OpenAICompatibleProvider
from document_pipeline_api.model_providers.base import (
    ModelRequestRejectedError,
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
)


class Result(BaseModel):
    value: float


FAKE_KEY = "synthetic_private_key_example"
SENSITIVE = (FAKE_KEY, "synthetic-user", "synthetic-pass", "privateopaque", "models.invalid", "127.0.0.1")
URLS = [
    pytest.param("https://synthetic-user:synthetic-pass@models.invalid/v1?opaque=privateopaque", id="remote"),
    pytest.param("http://127.0.0.1:1234/v1?opaque=privateopaque", id="local"),
]


def message_is_private(error: BaseException) -> bool:
    visible = str(error) + "\n" + "".join(traceback.format_exception(error))
    return not any(marker in visible for marker in SENSITIVE)


@pytest.mark.parametrize("base_url", URLS)
@pytest.mark.parametrize("entry", ["list", "complete"])
@pytest.mark.parametrize("fault,expected", [
    (httpx.ConnectError, ModelUnavailableError),
    (httpx.ReadError, ModelUnavailableError),
    (httpx.ReadTimeout, ModelTimeoutError),
], ids=["connect", "read", "timeout"])
def test_network_failures_keep_category_and_neutral_private_message(base_url, entry, fault, expected):
    calls = []

    def fail(request):
        calls.append(1)
        raise fault("synthetic transport failure " + str(request.url) + " " + FAKE_KEY, request=request)

    provider = OpenAICompatibleProvider(base_url, "synthetic", api_key=FAKE_KEY,
        client=httpx.Client(transport=httpx.MockTransport(fail)))
    try:
        with pytest.raises(expected) as caught:
            provider.available_models() if entry == "list" else provider.complete_text("合成请求", Result)
    finally:
        provider.close()
    assert calls == [1]
    assert caught.value.code == ("model_timeout" if fault is httpx.ReadTimeout else "model_unavailable")
    message = str(caught.value)
    assert "模型服务" in message and ("检查" in message or "重试" in message)
    assert "LM Studio" not in message and "本地模型服务" not in message
    assert "原件" not in message and "结果未保存" not in message
    private = message_is_private(caught.value)
    assert private, "A model error exposed synthetic endpoint or credential information."


@pytest.mark.parametrize("entry", ["list", "complete"])
@pytest.mark.parametrize("status,expected", [(400, ModelRequestRejectedError), (503, ModelUnavailableError)])
def test_http_error_keeps_category_and_sanitizes_body_and_traceback(entry, status, expected):
    calls = []
    echoed_url = "https://synthetic-user:synthetic-pass@models.invalid/v1?opaque=privateopaque"

    def rejected(_request):
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": "服务拒绝合成请求 " + echoed_url + " " + FAKE_KEY}})

    provider = OpenAICompatibleProvider("https://models.invalid/v1", "synthetic", api_key=FAKE_KEY,
        client=httpx.Client(transport=httpx.MockTransport(rejected)))
    try:
        with pytest.raises(expected) as caught:
            provider.available_models() if entry == "list" else provider.complete_text("合成请求", Result)
    finally:
        provider.close()
    assert calls == [1]
    assert caught.value.code == ("model_request_rejected" if status == 400 else "model_unavailable")
    assert "服务拒绝合成请求" in str(caught.value)
    private = message_is_private(caught.value)
    assert private, "A model error exposed synthetic endpoint or credential information."


@pytest.mark.parametrize("entry", ["text", "image"])
def test_length_explains_incomplete_output_without_assuming_task_storage(tmp_path: Path, entry):
    def reply(_request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]})

    provider = OpenAICompatibleProvider("https://models.invalid/v1", "synthetic",
        client=httpx.Client(transport=httpx.MockTransport(reply)))
    try:
        with pytest.raises(ModelResponseError) as caught:
            if entry == "text":
                provider.complete_text("合成请求", Result)
            else:
                image = tmp_path / "synthetic.png"
                image.write_bytes(b"synthetic image bytes")
                provider.extract_image(image, "合成请求", Result)
    finally:
        provider.close()
    assert caught.value.code == "model_invalid_response"
    assert str(caught.value) == "模型输出未完成，请调整模型输出长度设置或缩小处理范围后重试。"


def test_numeric_precision_error_remains_a_model_response_error_without_task_claims():
    def reply(_request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"value":0.10000000000000001}'}}]})

    provider = OpenAICompatibleProvider("https://models.invalid/v1", "synthetic",
        client=httpx.Client(transport=httpx.MockTransport(reply)))
    try:
        with pytest.raises(ModelResponseError) as caught:
            provider.complete_text("合成请求", Result)
    finally:
        provider.close()
    assert caught.value.code == "model_invalid_response"
    assert "精度" in str(caught.value)
    assert "原件" not in str(caught.value) and "结果未保存" not in str(caught.value)
