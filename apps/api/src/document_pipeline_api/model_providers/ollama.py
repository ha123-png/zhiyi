import base64
import json
from pathlib import Path

import httpx

from document_pipeline_api.model_providers.base import (
    ModelRequestRejectedError,
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    SchemaModel,
)
from document_pipeline_api.model_providers.openai_compatible import (
    CONNECT_TIMEOUT_SECONDS,
    _DEFAULT_SYSTEM_PROMPT,
    _extract_json_text,
    _image_mime_type,
    _response_error_detail,
    _unwrap_scalar_wrappers,
)


class OllamaProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        context_length: int = 8192,
        temperature: float | None = None,
        timeout_seconds: float = 180,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        self.base_url = normalized_url.removesuffix("/v1")
        self.model_name = model
        self.context_length = context_length
        self.temperature = temperature
        # 连接阶段用短超时：Ollama 没打开时立即失败，而不是等满 read 超时
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

    def complete_text(
        self,
        prompt: str,
        result_type: type[SchemaModel],
        *,
        system_prompt: str | None = None,
    ) -> SchemaModel:
        payload = {
            "model": self.model_name,
            "stream": False,
            "format": result_type.model_json_schema(),
            "options": {
                "temperature": self.temperature if self.temperature is not None else 0.1,
                "num_ctx": self.context_length,
            },
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
        }
        try:
            response = self._client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
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
                    "Ollama 暂时不可用"
                    + _response_error_detail(error.response)
                    + "，请稍后重试。"
                ) from error
            raise ModelRequestRejectedError(
                "Ollama 拒绝了请求"
                + _response_error_detail(error.response)
                + "，请检查模型名称是否与 Ollama 中实际安装的模型一致。"
            ) from error
        except httpx.ConnectError as error:
            raise ModelUnavailableError(
                "无法连接 Ollama 服务"
                f"（{self.base_url}）。请确认已启动 Ollama、拉取并加载了模型，"
                "再重新上传文件。"
            ) from error
        except httpx.RequestError as error:
            raise ModelUnavailableError(
                "连接 Ollama 服务出错"
                f"（{self.base_url}）。请检查 Ollama 是否正常运行、网络是否通畅。"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ModelResponseError(
                "Ollama 返回内容不符合字段结构，可以重试或更换模型。"
            ) from error

    def available_models(self) -> list[str]:
        try:
            response = self._client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            return [item["model"] for item in response.json().get("models", [])]
        except httpx.TimeoutException as error:
            raise ModelTimeoutError(
                "连接 Ollama 超时，请确认 Ollama 已启动并已拉取模型，然后重试。"
            ) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code >= 500:
                raise ModelUnavailableError(
                    "Ollama 暂时不可用"
                    + _response_error_detail(error.response)
                    + "，请稍后重试。"
                ) from error
            raise ModelRequestRejectedError(
                "Ollama 拒绝了当前配置"
                + _response_error_detail(error.response)
                + "，请检查 Ollama 地址和模型名称设置。"
            ) from error
        except httpx.ConnectError as error:
            raise ModelUnavailableError(
                "无法连接 Ollama 服务"
                f"（{self.base_url}）。请确认已启动 Ollama 并拉取加载了模型，"
                "再回到这里重试。"
            ) from error
        except httpx.RequestError as error:
            raise ModelUnavailableError(
                "连接 Ollama 服务出错"
                f"（{self.base_url}）。请检查 Ollama 是否正常运行、网络是否通畅。"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ModelResponseError(
                "Ollama 模型列表响应格式不正确，可以重试或更换模型服务。"
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
        for image_path in image_paths:
            _image_mime_type(image_path)
        image_data = [
            base64.b64encode(image_path.read_bytes()).decode("ascii")
            for image_path in image_paths
        ]
        payload = {
            "model": self.model_name,
            "stream": False,
            "format": result_type.model_json_schema(),
            "options": {
                "temperature": self.temperature if self.temperature is not None else 0.1,
                "num_ctx": self.context_length,
            },
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or (
                        "你是文件结构化助手。只依据图片内容和当前要求回答；"
                        "未知值使用 null，不猜测，不补写图片中不存在的内容。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n以下 {len(image_paths)} 张图片按文件页码顺序排列，"
                        "请合并理解为同一份文件，不要遗漏后续页。"
                    ),
                    "images": image_data,
                },
            ],
        }
        try:
            response = self._client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
            payload = json.loads(_extract_json_text(content))
            return result_type.model_validate(_unwrap_scalar_wrappers(payload))
        except httpx.TimeoutException as error:
            raise ModelTimeoutError(
                "Ollama 理解文件超时：模型可能未加载、正在加载或响应缓慢，"
                "请稍后重试，或在设置里更换更快的模型。"
            ) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code >= 500:
                raise ModelUnavailableError(
                    "Ollama 暂时不可用"
                    + _response_error_detail(error.response)
                    + "，请稍后重试。"
                ) from error
            raise ModelRequestRejectedError(
                "Ollama 拒绝了请求"
                + _response_error_detail(error.response)
                + "，请检查模型名称是否与 Ollama 中实际安装的模型一致。"
            ) from error
        except httpx.ConnectError as error:
            raise ModelUnavailableError(
                "无法连接 Ollama 服务"
                f"（{self.base_url}）。请确认已启动 Ollama、拉取并加载了模型，"
                "再重新上传文件。"
            ) from error
        except httpx.RequestError as error:
            raise ModelUnavailableError(
                "连接 Ollama 服务出错"
                f"（{self.base_url}）。请检查 Ollama 是否正常运行、网络是否通畅。"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ModelResponseError(
                "Ollama 返回内容不符合字段结构，可以重试或更换模型。"
            ) from error
