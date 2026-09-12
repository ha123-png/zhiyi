import json
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from document_pipeline_api.model_providers import (
    OllamaProvider,
    OpenAICompatibleProvider,
)
from document_pipeline_api.model_providers.base import (
    ModelRequestRejectedError,
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from document_pipeline_api.schemas.extraction import DocumentExtraction


@pytest.mark.parametrize("visual", [False, True])
def test_custom_system_prompt_keeps_json_instruction(tmp_path: Path, visual: bool) -> None:
    class Result(BaseModel):
        answer: str

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        assert "遵循原件" in system
        assert "JSON" in system
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"answer":"OK"}'}}],
        })

    provider = OpenAICompatibleProvider(
        "http://local.test/v1", "test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    if visual:
        source = tmp_path / "source.png"
        source.write_bytes(b"image")
        result = provider.extract_images(
            [source], "读取", Result, system_prompt="遵循原件",
        )
    else:
        result = provider.complete_text("读取", Result, system_prompt="遵循原件")
    assert result.answer == "OK"


def test_lists_available_models() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [{"id": "local-vision"}]})
    )
    client = OpenAICompatibleProvider(
        "http://local.test/v1",
        "local-vision",
        client=httpx.Client(transport=transport),
    )

    assert client.available_models() == ["local-vision"]


def test_connect_timeout_is_short_while_read_keeps_full_timeout() -> None:
    """没连上应立即失败：connect 短超时，read 保持模型推理所需时长。"""
    client = OpenAICompatibleProvider("http://local.test/v1", "vision")
    timeout = client._client.timeout
    assert timeout.connect == 5.0
    assert timeout.read == 180.0

    ollama = OllamaProvider("http://local.test/v1", "qwen3-vl:8b")
    assert ollama._client.timeout.connect == 5.0
    assert ollama._client.timeout.read == 180.0


def test_extract_image_requests_non_reasoning_structured_output(tmp_path: Path) -> None:
    image = tmp_path / "delivery.png"
    second_image = tmp_path / "delivery-page-2.png"
    image.write_bytes(b"image")
    second_image.write_bytes(b"image-2")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        result = {
            "document_type": "送货单",
            "seller_name": "示例供应商",
            "buyer_name": "示例客户",
            "document_number": "NO-1",
            "document_date": "2026-07-30",
            "amount_before_tax": None,
            "tax_amount": None,
            "total_amount": 10,
            "items": [
                {
                    "name": "零件",
                    "specification": None,
                    "unit": "个",
                    "quantity": 2,
                    "unit_price": 5,
                    "amount": 10,
                    "tax_rate": None,
                    "tax_amount": None,
                }
            ],
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result)}}]},
        )

    client = OpenAICompatibleProvider(
        "http://local.test/v1",
        "local-vision",
        reasoning_effort="none",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.extract_images(
        [image, second_image],
        "提取送货单",
        DocumentExtraction,
    )

    assert result.total_amount == 10
    assert captured["reasoning_effort"] == "none"
    assert captured["response_format"]["type"] == "json_schema"
    assert sum(
        item["type"] == "image_url"
        for item in captured["messages"][1]["content"]
    ) == 2


def test_unwraps_wrapped_scalar_values_like_value_17_percent(tmp_path: Path) -> None:
    """模型把税率包成 {"value": "17%"} 时，软件解包为标量，不因 schema 校验失败。"""
    image = tmp_path / "invoice.png"
    image.write_bytes(b"image")

    def handler(_request: httpx.Request) -> httpx.Response:
        result = {
            "document_type": "发票",
            "seller_name": "示例供应商",
            "buyer_name": "示例客户",
            "document_number": "NO-1",
            "document_date": "2026-07-30",
            "amount_before_tax": 100,
            "tax_amount": 17,
            "total_amount": 117,
            "items": [
                {
                    "name": "零件",
                    "specification": None,
                    "unit": "个",
                    "quantity": 1,
                    "unit_price": 100,
                    "amount": 100,
                    "tax_rate": {"value": "17%"},
                    "tax_amount": {"value": 17},
                }
            ],
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result)}}]},
        )

    client = OpenAICompatibleProvider(
        "http://local.test/v1",
        "local-vision",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.extract_images([image], "提取发票", DocumentExtraction)

    assert result.items[0].tax_rate == "17%"
    assert result.items[0].tax_amount == 17
    assert result.total_amount == 117


def test_openai_compatible_omits_optional_reasoning_and_sends_api_key(
    tmp_path: Path,
) -> None:
    image = tmp_path / "invoice.jpg"
    image.write_bytes(b"image")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": DocumentExtraction(
                                document_type="发票",
                                seller_name=None,
                                buyer_name=None,
                                document_number=None,
                                document_date=None,
                                amount_before_tax=None,
                                tax_amount=None,
                                total_amount=None,
                                items=[],
                            ).model_dump_json()
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleProvider(
        "https://cloud.test/v1",
        "vision-model",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.extract_image(image, "提取", DocumentExtraction)

    assert captured["authorization"] == "Bearer secret"
    assert "reasoning_effort" not in captured


def test_ollama_uses_native_vision_and_json_schema(tmp_path: Path) -> None:
    image = tmp_path / "delivery.png"
    second_image = tmp_path / "delivery-page-2.png"
    image.write_bytes(b"image")
    second_image.write_bytes(b"image-2")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"model": "qwen3-vl:8b"}]})
        captured.update(json.loads(request.content))
        result = DocumentExtraction(
            document_type="送货单",
            seller_name=None,
            buyer_name=None,
            document_number=None,
            document_date=None,
            amount_before_tax=None,
            tax_amount=None,
            total_amount=None,
            items=[],
        )
        return httpx.Response(
            200,
            json={"message": {"content": result.model_dump_json()}},
        )

    client = OllamaProvider(
        "http://ollama.test/v1",
        "qwen3-vl:8b",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.available_models() == ["qwen3-vl:8b"]
    result = client.extract_images(
        [image, second_image],
        "提取送货单",
        DocumentExtraction,
    )

    assert result.document_type == "送货单"
    assert captured["format"]["type"] == "object"
    assert len(captured["messages"][1]["images"]) == 2


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (httpx.Response(503, json={"error": "busy"}), ModelUnavailableError),
        (httpx.Response(400, json={"error": "unsupported schema"}), ModelRequestRejectedError),
        (
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not-json"}}]},
            ),
            ModelResponseError,
        ),
    ],
)
def test_openai_compatible_classifies_model_failures(
    tmp_path: Path,
    response: httpx.Response,
    expected_error: type[Exception],
) -> None:
    image = tmp_path / "invoice.png"
    image.write_bytes(b"image")
    provider = OpenAICompatibleProvider(
        "http://local.test/v1",
        "vision",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: response)),
    )

    with pytest.raises(expected_error):
        provider.extract_image(image, "提取", DocumentExtraction)


def test_openai_compatible_classifies_timeout(tmp_path: Path) -> None:
    image = tmp_path / "invoice.png"
    image.write_bytes(b"image")

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    provider = OpenAICompatibleProvider(
        "http://local.test/v1",
        "vision",
        client=httpx.Client(transport=httpx.MockTransport(timeout)),
    )

    with pytest.raises(ModelTimeoutError):
        provider.extract_image(image, "提取", DocumentExtraction)


def test_openai_compatible_connect_refused_gives_plain_actionable_message(
    tmp_path: Path,
) -> None:
    """LM Studio 未打开（连接被拒）时应给出小白可读、带服务地址的操作提示。"""
    image = tmp_path / "invoice.png"
    image.write_bytes(b"image")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OpenAICompatibleProvider(
        "http://127.0.0.1:1234/v1",
        "qwen3.5-4b",
        client=httpx.Client(transport=httpx.MockTransport(refuse)),
    )

    with pytest.raises(ModelUnavailableError) as caught:
        provider.extract_image(image, "提取", DocumentExtraction)
    message = str(caught.value)
    assert "无法连接模型服务" in message
    assert "127.0.0.1:1234" in message
    assert "LM Studio" in message
    assert "结构化输出" not in message


def test_openai_compatible_http_error_includes_server_reply(
    tmp_path: Path,
) -> None:
    """4xx 拒绝时应带上模型服务端返回的具体原因，而不是笼统的"模型能力"。"""
    image = tmp_path / "invoice.png"
    image.write_bytes(b"image")

    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "model 'qwen3.5-4b' not found"}},
        )

    provider = OpenAICompatibleProvider(
        "http://local.test/v1",
        "qwen3.5-4b",
        client=httpx.Client(transport=httpx.MockTransport(rejected)),
    )

    with pytest.raises(ModelRequestRejectedError) as caught:
        provider.extract_image(image, "提取", DocumentExtraction)
    message = str(caught.value)
    assert "模型服务拒绝了请求" in message
    assert "model 'qwen3.5-4b' not found" in message
    assert "模型名称" in message
    assert "结构化输出" not in message


def test_complete_endpoint_is_normalized_and_length_stop_is_not_success():
    class Result(BaseModel):
        value: str
    def reply(request):
        assert str(request.url) == "https://provider.test/v1/chat/completions"
        assert "max_tokens" not in json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": '{"value":"partial"}'}}]})
    provider = OpenAICompatibleProvider("https://provider.test/v1/chat/completions/", "example",
        client=httpx.Client(transport=httpx.MockTransport(reply)))
    with pytest.raises(ModelResponseError, match="输出未完成"):
        provider.complete_text("读取", Result)


def test_ollama_gateway_key_and_native_context_are_sent():
    class Result(BaseModel):
        value: str
    def reply(request):
        assert str(request.url) == "https://gateway.test/api/chat"
        assert request.headers["authorization"] == "Bearer synthetic-test-key"
        assert json.loads(request.content)["options"]["num_ctx"] == 16384
        return httpx.Response(200, json={"message": {"content": '{"value":"ok"}'}})
    provider = OllamaProvider("https://gateway.test/api/chat", "local", api_key="synthetic-test-key", context_length=16384,
        client=httpx.Client(transport=httpx.MockTransport(reply)))
    assert provider.complete_text("读取", Result).value == "ok"
