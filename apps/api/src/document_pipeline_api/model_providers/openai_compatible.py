import base64
import json
import re
from pathlib import Path

import httpx

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


def _unwrap_scalar_wrappers(value: object) -> object:
    """模型偶尔会把标量包装成 {"value": ...}（附带单位或说明），
    递归解包这种单键包装，避免严格 schema 校验失败或把 JSON 文本当值显示。"""
    if isinstance(value, dict):
        if len(value) == 1 and "value" in value:
            return _unwrap_scalar_wrappers(value["value"])
        return {key: _unwrap_scalar_wrappers(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_unwrap_scalar_wrappers(child) for child in value]
    return value


_DEFAULT_SYSTEM_PROMPT = (
    "你是文件结构化助手。只依据图片内容和当前要求回答；"
    "未知值使用 null，不猜测，不补写图片中不存在的内容。"
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
    message = message.strip().replace("\n", " ")[:200]
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
        self.base_url = base_url.rstrip("/")
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
            return [item["id"] for item in response.json().get("data", [])]
        except httpx.TimeoutException as error:
            raise ModelTimeoutError(
                "连接模型服务超时，请确认本地模型服务已打开并已加载模型，然后重试。"
            ) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code >= 500:
                raise ModelUnavailableError(
                    "模型服务暂时不可用"
                    + _response_error_detail(error.response)
                    + "，请稍后重试。"
                ) from error
            raise ModelRequestRejectedError(
                "模型服务拒绝了当前配置"
                + _response_error_detail(error.response)
                + "，请检查模型地址、密钥和模型名称设置。"
            ) from error
        except httpx.ConnectError as error:
            raise ModelUnavailableError(
                "无法连接本地模型服务"
                f"（{self.base_url}）。请确认已打开 LM Studio 并启动本地服务器、"
                "加载了模型，再回到这里重试。"
            ) from error
        except httpx.RequestError as error:
            raise ModelUnavailableError(
                "连接本地模型服务出错"
                f"（{self.base_url}）。请检查模型服务是否正常运行、网络是否通畅。"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ModelResponseError(
                "模型服务返回的模型列表格式不正确，可以重试或更换模型服务。"
            ) from error

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
                    f"{prompt}\n以下 {len(image_paths)} 张图片按文件页码顺序排列，"
                    "请合并理解为同一份文件，不要遗漏后续页。"
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
            "max_tokens": 2000,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or _DEFAULT_SYSTEM_PROMPT,
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
            "max_tokens": 2000,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or _DEFAULT_SYSTEM_PROMPT,
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
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            payload = json.loads(_extract_json_text(content))
            return result_type.model_validate(_unwrap_scalar_wrappers(payload))
        except httpx.TimeoutException as error:
            raise ModelTimeoutError(
                "处理超时：模型可能未加载、正在加载或响应缓慢，"
                "请稍后重试，或在设置里更换更快的模型。"
            ) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code >= 500:
                raise ModelUnavailableError(
                    "模型服务暂时不可用"
                    + _response_error_detail(error.response)
                    + "，请稍后重试。"
                ) from error
            raise ModelRequestRejectedError(
                "模型服务拒绝了请求"
                + _response_error_detail(error.response)
                + "，请检查模型名称是否与本地模型服务中实际加载的模型一致。"
            ) from error
        except httpx.ConnectError as error:
            raise ModelUnavailableError(
                "无法连接本地模型服务"
                f"（{self.base_url}）。请确认已打开 LM Studio 并启动本地服务器、"
                "加载了模型，再重新上传文件。"
            ) from error
        except httpx.RequestError as error:
            raise ModelUnavailableError(
                "连接本地模型服务出错"
                f"（{self.base_url}）。请检查模型服务是否正常运行、网络是否通畅。"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ModelResponseError(
                "模型返回内容不符合字段结构，可以重试或更换模型。"
            ) from error


def _image_mime_type(image_path: Path) -> str:
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(image_path.suffix.lower())
    if mime_type is None:
        raise ValueError("模型适配器当前仅接受 JPG 和 PNG 图片。")
    return mime_type
