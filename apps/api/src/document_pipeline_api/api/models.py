from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import get_session
from document_pipeline_api.model_secrets import (
    ModelSecretStore,
    SecretStoreError,
    create_model_secret_store,
)
from document_pipeline_api.model_providers import ModelServiceError, build_model_provider
from document_pipeline_api.schemas.model_profiles import (
    ModelProfileActivate,
    ModelProfileBody,
    ModelProfileCreate,
    ModelProfileRead,
    ModelProfileUpdate,
    ModelProbeResult,
)
from document_pipeline_api.services.model_profiles import (
    activate_model_profile,
    archive_model_profile,
    create_model_profile,
    list_model_profiles,
    probe_model_profile,
    update_model_profile,
)
from document_pipeline_api.services.model_runtime import (
    settings_for_active_profile,
    settings_for_active_profile_metadata,
)


router = APIRouter(prefix="/models", tags=["models"])
SessionDependency = Annotated[Session, Depends(get_session)]


class ModelStatus(BaseModel):
    connected: bool
    provider: str
    configured_model: str
    available_models: list[str]
    configuration_error: str | None = None


@router.get("/profiles", response_model=list[ModelProfileRead])
def profiles(session: SessionDependency) -> list[ModelProfileRead]:
    return list_model_profiles(session)


@router.post("/profiles", response_model=ModelProfileRead, status_code=201)
def create_profile(
    request: Request,
    body: ModelProfileCreate,
    session: SessionDependency,
) -> ModelProfileRead:
    return create_model_profile(
        session,
        body,
        _secret_store(request, required=body.api_key is not None),
    )


@router.put("/profiles/{profile_id}", response_model=ModelProfileRead)
def update_profile(
    request: Request,
    profile_id: str,
    body: ModelProfileUpdate,
    session: SessionDependency,
) -> ModelProfileRead:
    return update_model_profile(
        session,
        profile_id,
        body,
        _secret_store(request, required=body.api_key is not None),
    )


@router.post("/profiles/probe", response_model=ModelProbeResult)
def probe_profile(
    request: Request,
    body: ModelProfileBody,
    session: SessionDependency,
    profile_id: str | None = Query(default=None),
) -> ModelProbeResult:
    """连通性 + 多模态探测：用表单当前配置测试（不落库），供配置页“测试连接”使用。
    表单未填 API Key 时可传 profile_id，探测自动使用该方案已保存的密钥。"""
    settings: Settings = request.app.state.settings
    # 传了 profile_id 时可能要用该方案已保存的密钥，此时必须保证密钥库存在
    return probe_model_profile(
        settings,
        body,
        session=session,
        secret_store=_secret_store(request, required=profile_id is not None),
        profile_id=profile_id,
    )


@router.post("/profiles/{profile_id}/activate", response_model=ModelProfileRead)
def activate_profile(
    profile_id: str,
    body: ModelProfileActivate,
    session: SessionDependency,
) -> ModelProfileRead:
    return activate_model_profile(
        session,
        profile_id,
        body.version,
        acknowledge=body.acknowledge_remote_data_transfer,
    )


@router.post("/profiles/{profile_id}/archive", response_model=ModelProfileRead)
def archive_profile(profile_id: str, session: SessionDependency) -> ModelProfileRead:
    return archive_model_profile(session, profile_id)


@router.get("/status", response_model=ModelStatus)
def model_status(request: Request, session: SessionDependency) -> ModelStatus:
    startup_settings: Settings = request.app.state.settings
    metadata_settings = settings_for_active_profile_metadata(session, startup_settings)
    configuration_error = None
    try:
        settings = settings_for_active_profile(
            session,
            startup_settings,
            _secret_store(request, required=False),
        )
    except ValueError:
        settings = metadata_settings
        configuration_error = "当前 AI 服务方案的系统密钥不可用，请重新输入密钥。"
    if configuration_error is not None:
        return ModelStatus(
            connected=False,
            provider=settings.model_provider,
            configured_model=settings.model_name,
            available_models=[],
            configuration_error=configuration_error,
        )
    client = build_model_provider(settings, timeout_seconds=2)
    try:
        models = client.available_models()
    except ModelServiceError:
        models = []
    finally:
        client.close()
    configuration_error = None
    if models and settings.model_name not in models:
        # 服务可达但配置名与列表不一致：直接列出可选项，用户照着改即可
        shown = "、".join(models[:5])
        configuration_error = (
            f"模型服务可达，但配置的模型名「{settings.model_name}」不在服务列表中。"
            f"请到模型配置里把模型名改为：{shown}"
        )
    return ModelStatus(
        connected=settings.model_name in models,
        provider=settings.model_provider,
        configured_model=settings.model_name,
        available_models=models,
        configuration_error=configuration_error,
    )


def _secret_store(request: Request, *, required: bool) -> ModelSecretStore | None:
    store = request.app.state.model_secret_store
    if store is not None or not required:
        return store
    try:
        store = create_model_secret_store()
    except SecretStoreError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    request.app.state.model_secret_store = store
    return store
