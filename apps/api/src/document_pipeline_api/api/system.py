from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import get_session
from document_pipeline_api.model_providers import (
    ModelServiceError,
    build_model_provider,
)
from document_pipeline_api.model_providers.local_model_manager import (
    build_local_model_manager,
)
from document_pipeline_api.model_secrets import (
    ModelSecretStore,
    SecretStoreError,
    create_model_secret_store,
)
from document_pipeline_api.services.model_runtime import (
    settings_for_active_profile,
    settings_for_active_profile_metadata,
)
from document_pipeline_api.services.worker_health import read_worker_health


router = APIRouter(prefix="/system", tags=["system"])
SessionDependency = Annotated[Session, Depends(get_session)]


class ComponentStatus(BaseModel):
    connected: bool
    message: str


class SystemStatus(BaseModel):
    api: ComponentStatus
    worker: ComponentStatus
    model: ComponentStatus
    model_provider: str
    configured_model: str


@router.get("/status", response_model=SystemStatus)
def system_status(request: Request, session: SessionDependency) -> SystemStatus:
    settings: Settings = request.app.state.settings
    worker = read_worker_health()
    # 模型连接状态要以激活的模型方案为准（而非环境默认配置），否则自定义方案
    # 激活后状态页永远误报"未连接"。优先用含密钥的完整配置（云端服务无 key 会拒
    # 绝探测），密钥不可用时回退到不带密钥的元数据配置再探测。
    try:
        active_settings = settings_for_active_profile(
            session,
            settings,
            _secret_store(request, required=False),
        )
    except Exception:
        try:
            active_settings = settings_for_active_profile_metadata(session, settings)
        except Exception:
            active_settings = settings
    model_client = build_model_provider(active_settings, timeout_seconds=2)
    try:
        models = model_client.available_models()
    except ModelServiceError:
        models = []
    finally:
        model_client.close()

    model_connected = active_settings.model_name in models
    return SystemStatus(
        api=ComponentStatus(connected=True, message="API 已连接"),
        worker=ComponentStatus(
            connected=worker["online"],
            message="任务消费者在线" if worker["online"] else "任务消费者未启动",
        ),
        model=ComponentStatus(
            connected=model_connected,
            message=(
                f"模型 {active_settings.model_name} 可用"
                if model_connected
                else f"模型 {active_settings.model_name} 未连接"
            ),
        ),
        model_provider=active_settings.model_provider,
        configured_model=active_settings.model_name,
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


class LocalModelStatus(BaseModel):
    provider: str
    base_url: str
    running: bool
    message: str
    loaded: list[str] = []
    installed: bool = True
    install_url: str | None = None


class LocalModelAction(BaseModel):
    ok: bool
    message: str
    detail: str | None = None


class LocalModelList(BaseModel):
    provider: str
    available: list[str]
    loaded: list[str]


class LocalModelLoad(BaseModel):
    model_name: str = Field(min_length=1, max_length=256)
    context_length: int = Field(default=8192, ge=1024, le=262144)


class LocalModelUnload(BaseModel):
    model_name: str = Field(min_length=1, max_length=256)


def _local_manager_for_active_profile(
    request: Request,
    session: Session,
) -> tuple[str, str, object]:
    settings: Settings = request.app.state.settings
    try:
        metadata = settings_for_active_profile_metadata(session, settings)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    # The local manager is a utility panel, not the active extraction route.
    # Keep LM Studio manageable even while a cloud or one-click profile is active.
    if metadata.model_provider not in {"lm_studio", "ollama"}:
        return "lm_studio", "http://127.0.0.1:1234/v1", build_local_model_manager(
            "lm_studio", "http://127.0.0.1:1234/v1"
        )
    return (
        metadata.model_provider,
        metadata.model_base_url,
        build_local_model_manager(metadata.model_provider, metadata.model_base_url),
    )


@router.get("/local-model/status", response_model=LocalModelStatus)
def local_model_status(request: Request, session: SessionDependency) -> LocalModelStatus:
    provider, base_url, manager = _local_manager_for_active_profile(request, session)
    status = manager.server_status()
    return LocalModelStatus(
        provider=provider,
        base_url=base_url,
        running=bool(status.get("running")),
        message=status.get("message", ""),
        loaded=status.get("loaded", []),
        installed=bool(status.get("installed", True)),
        install_url=status.get("install_url"),
    )


@router.post("/local-model/start", response_model=LocalModelAction)
def local_model_start(request: Request, session: SessionDependency) -> LocalModelAction:
    _provider, _base_url, manager = _local_manager_for_active_profile(request, session)
    result = manager.start_server()
    return LocalModelAction(
        ok=bool(result.get("ok")),
        message=result.get("message", ""),
        detail=result.get("detail"),
    )


@router.post("/local-model/stop", response_model=LocalModelAction)
def local_model_stop(request: Request, session: SessionDependency) -> LocalModelAction:
    _provider, _base_url, manager = _local_manager_for_active_profile(request, session)
    result = manager.stop_server()
    return LocalModelAction(
        ok=bool(result.get("ok")),
        message=result.get("message", ""),
        detail=result.get("detail"),
    )


@router.get("/local-model/models", response_model=LocalModelList)
def local_model_models(
    request: Request,
    session: SessionDependency,
) -> LocalModelList:
    provider, _base_url, manager = _local_manager_for_active_profile(request, session)
    return LocalModelList(
        provider=provider,
        available=manager.list_models(),
        loaded=manager.loaded_models(),
    )


@router.post("/local-model/load", response_model=LocalModelAction)
def local_model_load(
    request: Request,
    session: SessionDependency,
    body: LocalModelLoad,
) -> LocalModelAction:
    _provider, _base_url, manager = _local_manager_for_active_profile(request, session)
    result = manager.load_model(body.model_name, body.context_length)
    return LocalModelAction(
        ok=bool(result.get("ok")),
        message=result.get("message", ""),
        detail=result.get("detail"),
    )


@router.post("/local-model/unload", response_model=LocalModelAction)
def local_model_unload(
    request: Request,
    session: SessionDependency,
    body: LocalModelUnload,
) -> LocalModelAction:
    _provider, _base_url, manager = _local_manager_for_active_profile(request, session)
    result = manager.unload_model(body.model_name)
    return LocalModelAction(
        ok=bool(result.get("ok")),
        message=result.get("message", ""),
        detail=result.get("detail"),
    )
