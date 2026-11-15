from document_pipeline_api.model_diagnostics import service_error_diagnostic
import base64
import json
import re
from pathlib import Path

import httpx
from pydantic import ValidationError
from document_pipeline_api.schemas.json_numbers import NumericPrecisionError, loads_json_numbers
from document_pipeline_api.services.autonomous_extraction import AutonomousModelResult

from document_pipeline_api.model_providers.base import (
    ModelRequestRejectedError,
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    SchemaModel,
)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# 连接阶段（TCP 握手）的超时：本地模型服务没打开时应当立即失败，
# 而不是复用 read 超时（最长 180 秒）干等。
CONNECT_TIMEOUT_SECONDS = 5.0


def _extract_json_text(content: str) -> str:
    """从模型输出中提取 JSON 对象：去掉 markdown 围栏，并剔除
    “好的，让我来生成xxx：”这类前缀废话（取第一个 { 到最后一个 }）。"""
    fence = _JSON_FENCE_RE.search(content)
    if fence:
        content = fence.group(1)
    match = _JSON_OBJECT_RE.search(content)
    if match:
        return match.group(0)
    return content


def _validate_payload(result_type: type[SchemaModel], payload: object) -> SchemaModel:
    """Accept valid business JSON first. Repair wrappers only at scalar leaves."""
    for attempt in range(2):
        try:
            return result_type.model_validate(payload)
        except ValidationError as error:
            for detail in error.errors():
                cause = detail.get("ctx", {}).get("error")
                if isinstance(cause, NumericPrecisionError):
                    raise cause from error
            if attempt:
                raise
            schema = result_type.model_json_schema()
            payload = _unwrap_scalar_wrappers(payload, schema, schema)


def parse_structured_content(content: object, result_type: type[SchemaModel]) -> SchemaModel:
    if not isinstance(content, str) or not content.strip():
        raise ModelResponseError("模型没有返回可读取的结果。")
    try:
        payload = loads_json_numbers(
            _extract_json_text(content), keep_unrepresentable=issubclass(result_type, AutonomousModelResult),
        )
    except json.JSONDecodeError as error:
        raise ModelResponseError(
            "模型返回内容无法解析为 JSON，可以重试。",
            diagnostic=f"模型输出的 JSON 不完整或格式无效。解析停在第 {error.lineno} 行、第 {error.colno} 列。",
        ) from None
    try:
        return _validate_payload(result_type, payload)
    except ValidationError as error:
        issue = error.errors(include_url=False, include_context=False, include_input=False)[0]
        parts = result_type.model_fields.keys() | {"label", "value_type", "value", "fields"}
        path = ".".join(
            str(segment + 1) if isinstance(segment, int) else segment if segment in parts else "字段"
            for segment in issue["loc"]
        )
        reason = {
            "missing": "缺少必填字段",
            "extra_forbidden": "包含未定义字段",
            "literal_error": "字段类型标记无效",
            "list_type": "需要列表",
            "dict_type": "需要对象",
            "autonomous_row_width": "明细的值数量与明细字段数量不一致",
            "autonomous_field_label": "同一分区内的字段名称重复或为空",
            "autonomous_number_value": "数值字段没有填入可解析的数字",
        }.get(issue["type"], "值或字段定义不符合提取要求")
        raise ModelResponseError(
            "模型返回内容不符合字段结构，可以重试或更换模型。",
            diagnostic=f"模型已返回 JSON，但结构校验未通过。{path or '提取结果'}：{reason}。",
        ) from None


def _unwrap_scalar_wrappers(value: object, schema: dict, root: dict) -> object:
    reference = schema.get("$ref", "")
    if reference.startswith("#/$defs/"):
        schema = root.get("$defs", {}).get(reference.removeprefix("#/$defs/"), {})
    alternatives = schema.get("anyOf", schema.get("oneOf", [schema]))
    shapes = [part for part in alternatives if part.get("type") != "null"]
    if len(shapes) == 1 and shapes[0] is not schema:
        return _unwrap_scalar_wrappers(value, shapes[0], root)
    kind = schema.get("type")
    if kind == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", {})
        return {key: _unwrap_scalar_wrappers(child, properties.get(key, extra if isinstance(extra, dict) else {}), root)
                for key, child in value.items()}
    if kind == "array" and isinstance(value, list):
        return [_unwrap_scalar_wrappers(child, schema.get("items", {}), root) for child in value]
    scalar_types = {"string", "number", "integer", "boolean"}
    if kind in scalar_types or (shapes and all(part.get("type") in scalar_types for part in shapes)):
        # A bounded repair cannot reinterpret an object-valued business field.
        for _ in range(8):
            if not isinstance(value, dict) or set(value) != {"value"}:
                break
            value = value["value"]
    return value


_DEFAULT_SYSTEM_PROMPT = (
    "你是文件结构化助手。只依据提供的文字、图片和当前要求回答；"
    "未知值使用 null，不猜测，不补写未提供的内容。"
)


def _response_error_detail(response: httpx.Response) -> str:
    """从模型服务端返回中提取具体错误信息，供小白可读的报错提示。"""
    try:
        data = response.json()
    except (ValueError, TypeError):
        data = None
    message = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
        elif isinstance(error, str):
            message = error
        elif error is None:
            message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        text = response.text or ""
        message = text.strip()
    try:
        authorization = response.request.headers.get("Authorization", "")
    except RuntimeError:
        authorization = ""
    secret = authorization.removeprefix("Bearer ")
    message = service_error_diagnostic(message, secret)
    # The shared diagnostic removes URL authority; do not expose a remaining
    # path or query from an untrusted model-service error response either.
    message = re.sub(r"(?i)\[服务地址\](?:[/?#][^\s\"'<>]*)?", "[服务地址]", message)
    return f"（服务端返回：{message}）" if message else ""


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        timeout_seconds: float = 180,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/").removesuffix("/chat/completions")
        self.model_name = model
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self.temperature = temperature
        # 连接阶段用短超时：本地模型服务没打开时立即失败，而不是等满 read 超时
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=timeout_seconds,
                write=CONNECT_TIMEOUT_SECONDS,
                pool=CONNECT_TIMEOUT_SECONDS,
            ),
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def available_models(self) -> list[str]:
        try:
            response = self._client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid model list envelope")
            models = [item["id"] for item in payload.get("data", [])]
            if any(not isinstance(model, str) for model in models):
                raise ValueError("Invalid model identifier")
            return models
        except httpx.TimeoutException:
            raise ModelTimeoutError(
                "连接模型服务超时，请检查服务地址、网络和服务状态后重试。"
            ) from None
        except httpx.HTTPStatusError as error:
            if error.response.status_code >= 500:
                raise ModelUnavailableError(
                    "模型服务暂时不可用"
                    + _response_error_detail(error.response)
                    + "，请稍后重试。"
                ) from None
            raise ModelRequestRejectedError(
                "模型服务拒绝了当前配置"
                + _response_error_detail(error.response)
                + "，请检查模型地址、密钥和模型名称设置。"
            ) from None
        except httpx.ConnectError:
            raise ModelUnavailableError(
                "无法连接模型服务，请检查服务地址、网络和服务是否已启动。"
            ) from None
        except httpx.RequestError:
            raise ModelUnavailableError(
                "连接模型服务时出现问题，请检查服务状态和网络后重试。"
            ) from None
        except (KeyError, TypeError, ValueError):
            raise ModelResponseError(
                "模型服务返回的模型列表格式不正确，可以重试或更换模型服务。"
            ) from None

    def extract_image(
        self,
        image_path: Path,
        prompt: str,
        result_type: type[SchemaModel],
        *,
        system_prompt: str | None = None,
    ) -> SchemaModel:
        return self.extract_images(
            [image_path],
            prompt,
            result_type,
            system_prompt=system_prompt,
        )

    def extract_images(
        self,
        image_paths: list[Path],
        prompt: str,
        result_type: type[SchemaModel],
        *,
        system_prompt: str | None = None,
    ) -> SchemaModel:
        if not image_paths:
            raise ValueError("至少需要一张图片。")
        user_content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    f"{prompt}\n以下 {len(image_paths)} 张图片按所列输入范围顺序排列，"
                    "这些范围可能不连续；仅依据提供内容理解，不推测或拼接缺失部分。"
                ),
            }
        ]
        for image_path in image_paths:
            mime_type = _image_mime_type(image_path)
            image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{image_data}",
                    },
                }
            )
        payload = {
            "model": self.model_name,
            "temperature": self.temperature if self.temperature is not None else 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (system_prompt or _DEFAULT_SYSTEM_PROMPT) + "\n仅返回符合指定结构的 JSON 对象。",
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": result_type.__name__,
                    "strict": True,
                    "schema": result_type.model_json_schema(),
                },
            },
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        return self._post_completion(payload, result_type)

    def complete_text(
        self,
        prompt: str,
        result_type: type[SchemaModel],
        *,
        system_prompt: str | None = None,
    ) -> SchemaModel:
        payload = {
            "model": self.model_name,
            "temperature": self.temperature if self.temperature is not None else 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (system_prompt or _DEFAULT_SYSTEM_PROMPT) + "\n仅返回符合指定结构的 JSON 对象。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": result_type.__name__,
                    "strict": True,
                    "schema": result_type.model_json_schema(),
                },
            },
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        return self._post_completion(payload, result_type)

    def _post_completion(
        self,
        payload: dict[str, object],
        result_type: type[SchemaModel],
    ) -> SchemaModel:
        self.last_usage = {}
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            response_body = response.json()
            if not isinstance(response_body, dict):
                raise ValueError("Invalid completion envelope")
            self.last_usage = response_body.get("usage") or {}
            choices = response_body.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("Invalid completion choices")
            choice = choices[0]
            if choice.get("finish_reason") == "content_filter":
                raise ModelRequestRejectedError("模型服务因内容限制拒绝了本次请求。请查看服务的内容限制或联系服务方。")
            if choice.get("finish_reason") == "length":
                raise ModelResponseError("模型输出未完成，请调整模型输出长度设置或缩小处理范围后重试。")
            return parse_structured_content(choice["message"]["content"], result_type)
        except httpx.TimeoutException:
            raise ModelTimeoutError(
                "模型服务响应超时，请检查服务状态和网络后重试。"
            ) from None
        except httpx.HTTPStatusError as error:
            if error.response.status_code >= 500:
                raise ModelUnavailableError(
                    "模型服务暂时不可用"
                    + _response_error_detail(error.response)
                    + "，请稍后重试。"
                ) from None
            raise ModelRequestRejectedError(
                "模型服务拒绝了请求"
                + _response_error_detail(error.response)
                + "，请检查当前模型方案的鉴权、模型名称和接口支持情况。"
            ) from None
        except httpx.ConnectError:
            raise ModelUnavailableError(
                "无法连接模型服务，请检查服务地址、网络和服务是否已启动。"
            ) from None
        except httpx.RequestError:
            raise ModelUnavailableError(
                "连接模型服务时出现问题，请检查服务状态和网络后重试。"
            ) from None
        except NumericPrecisionError:
            raise ModelResponseError("模型返回的数值精度超出支持范围，请检查输入数值或字段类型后重试。") from None
        except (KeyError, TypeError, ValueError):
            raise ModelResponseError("模型服务响应缺少预期的结果内容。") from None


def _image_mime_type(image_path: Path) -> str:
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(image_path.suffix.lower())
    if mime_type is None:
        raise ValueError("模型适配器当前仅接受 JPG 和 PNG 图片。")
    return mime_type
