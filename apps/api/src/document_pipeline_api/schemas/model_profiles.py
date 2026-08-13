from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ModelProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    provider: Literal["lm_studio", "openai_compatible", "ollama"]
    base_url: str = Field(min_length=1, max_length=1024)
    model_name: str = Field(min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    timeout_seconds: float = Field(default=180, ge=1, le=3600)
    context_length: int = Field(default=8192, ge=1024, le=262144)
    temperature: float | None = Field(default=None, ge=0, le=2)
    # None=未验证；True=支持图片；False=仅文本。仅文本方案处理图片输入时任务直接失败。
    multimodal: bool | None = Field(default=None)
    api_key: SecretStr | None = Field(default=None, max_length=2048)
    acknowledge_remote_data_transfer: bool = False


class ModelProfileCreate(ModelProfileBody):
    pass


class ModelProfileUpdate(ModelProfileBody):
    expected_version: int = Field(ge=1)
    clear_api_key: bool = False

    @model_validator(mode="after")
    def key_update_is_unambiguous(self) -> "ModelProfileUpdate":
        if self.api_key is not None and self.clear_api_key:
            raise ValueError("不能同时填写新密钥和清除密钥。")
        return self


class ModelProfileActivate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    # 云端方案首次激活时的数据离开本机确认；确认后写回方案，后续激活不再询问
    acknowledge_remote_data_transfer: bool = False


class ModelProfileRead(BaseModel):
    id: str
    version: int
    current_version: int
    name: str
    provider: str
    base_url: str
    model_name: str
    reasoning_effort: str | None
    timeout_seconds: float
    context_length: int
    temperature: float | None
    multimodal: bool | None
    has_api_key: bool
    is_remote: bool
    remote_data_acknowledged: bool
    is_active: bool
    active_version: int | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class ModelProbeResult(BaseModel):
    """方案连通性与多模态探测结果（不落库，仅用于配置页测试连接）。"""

    connected: bool
    model_listed: bool
    multimodal: bool | None
    message: str
    available_models: list[str] = Field(default_factory=list)
