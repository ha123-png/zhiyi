from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel


SchemaModel = TypeVar("SchemaModel", bound=BaseModel)


class ModelServiceError(RuntimeError):
    code = "model_error"
    retryable = False


class ModelTimeoutError(ModelServiceError):
    code = "model_timeout"
    retryable = True


class ModelUnavailableError(ModelServiceError):
    code = "model_unavailable"
    retryable = True


class ModelRequestRejectedError(ModelServiceError):
    code = "model_request_rejected"


class ModelResponseError(ModelServiceError):
    code = "model_invalid_response"


class ModelProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    def available_models(self) -> list[str]: ...

    def extract_image(
        self,
        image_path: Path,
        prompt: str,
        result_type: type[SchemaModel],
        *,
        system_prompt: str | None = None,
    ) -> SchemaModel: ...

    def extract_images(
        self,
        image_paths: list[Path],
        prompt: str,
        result_type: type[SchemaModel],
        *,
        system_prompt: str | None = None,
    ) -> SchemaModel: ...

    def complete_text(
        self,
        prompt: str,
        result_type: type[SchemaModel],
        *,
        system_prompt: str | None = None,
    ) -> SchemaModel: ...

    def close(self) -> None: ...
