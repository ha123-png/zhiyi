from document_pipeline_api.config import Settings
from document_pipeline_api.model_providers.base import ModelProvider
from document_pipeline_api.model_providers.ollama import OllamaProvider
from document_pipeline_api.model_providers.openai_compatible import (
    OpenAICompatibleProvider,
)


def build_model_provider(
    settings: Settings,
    *,
    timeout_seconds: float = 180,
) -> ModelProvider:
    if settings.model_provider == "ollama":
        return OllamaProvider(
            settings.model_base_url,
            settings.model_name,
            context_length=settings.model_context_length,
            api_key=settings.model_api_key,
            temperature=settings.model_temperature,
            timeout_seconds=timeout_seconds,
        )
    if settings.model_provider in {"lm_studio", "openai_compatible"}:
        return OpenAICompatibleProvider(
            settings.model_base_url,
            settings.model_name,
            api_key=settings.model_api_key,
            reasoning_effort=settings.model_reasoning_effort,
            temperature=settings.model_temperature,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"不支持的模型服务类型：{settings.model_provider}")
