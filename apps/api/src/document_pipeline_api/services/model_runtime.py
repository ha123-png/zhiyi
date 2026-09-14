from dataclasses import replace
from document_pipeline_api.services.model_context import profile_context_budget

from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.model_secrets import (
    ModelSecretStore,
    SecretStoreError,
    create_model_secret_store,
)
from document_pipeline_api.models.model_profile import ModelProfileVersionRecord
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.services.model_profiles import (
    get_active_model_profile_version,
    get_model_profile_version,
)


MODEL_CONFIG_SNAPSHOT_VERSION = "environment-v1"


def model_snapshot_values(
    settings: Settings,
    session: Session | None = None,
) -> dict[str, str | int | float | None]:
    active_version = (
        get_active_model_profile_version(session) if session is not None else None
    )
    if active_version is not None:
        return {
            "model_config_version": "profile-v1",
            "model_profile_id": active_version.profile_id,
            "model_profile_version": active_version.version,
            "model_provider": active_version.provider,
            "model_base_url": active_version.base_url,
            "model_name": active_version.model_name,
            "model_reasoning_effort": active_version.reasoning_effort,
            "model_timeout_seconds": active_version.timeout_seconds,
            "model_context_length": profile_context_budget(active_version),
            "model_temperature": active_version.temperature,
            "model_secret_ref": active_version.secret_ref,
        }
    return {
        "model_config_version": MODEL_CONFIG_SNAPSHOT_VERSION,
        "model_profile_id": None,
        "model_profile_version": None,
        "model_provider": settings.model_provider,
        "model_base_url": settings.model_base_url,
        "model_name": settings.model_name,
        "model_reasoning_effort": settings.model_reasoning_effort,
        "model_timeout_seconds": settings.model_timeout_seconds,
        "model_context_length": settings.model_context_length,
        "model_temperature": settings.model_temperature,
        "model_secret_ref": "environment",
    }


def settings_for_task(
    settings: Settings,
    task: TaskRecord,
    secret_store: ModelSecretStore | None = None,
) -> Settings:
    if task.model_config_version == "legacy-unversioned":
        return settings
    required = (
        task.model_provider,
        task.model_base_url,
        task.model_name,
        task.model_timeout_seconds,
    )
    if any(value is None for value in required):
        raise ValueError("任务的模型配置快照不完整，不能开始处理。")
    if task.model_config_version == MODEL_CONFIG_SNAPSHOT_VERSION:
        if task.model_secret_ref != "environment":
            raise ValueError("任务引用了当前版本不支持的密钥来源。")
        api_key = settings.model_api_key
    elif task.model_config_version == "profile-v1":
        if task.model_profile_id is None or task.model_profile_version is None:
            raise ValueError("任务的模型方案版本快照不完整。")
        if task.model_secret_ref is None:
            api_key = ""
        else:
            try:
                store = secret_store or create_model_secret_store()
                api_key = store.get(task.model_secret_ref)
            except SecretStoreError as error:
                raise ValueError("任务绑定的 AI 服务密钥不可用。") from error
    else:
        raise ValueError("任务引用了当前版本不支持的模型配置。")
    replaced: dict[str, object] = {
        "model_provider": task.model_provider,
        "model_base_url": task.model_base_url,
        "model_name": task.model_name,
        "model_reasoning_effort": task.model_reasoning_effort,
        "model_timeout_seconds": task.model_timeout_seconds,
        "model_api_key": api_key,
    }
    # 上下文长度与温度在旧任务快照里可能为 NULL，回退到环境/方案默认值。
    if task.model_context_length is not None:
        replaced["model_context_length"] = task.model_context_length
    if task.model_temperature is not None:
        replaced["model_temperature"] = task.model_temperature
    return replace(settings, **replaced)


def settings_for_active_profile(
    session: Session,
    settings: Settings,
    secret_store: ModelSecretStore | None = None,
) -> Settings:
    version = get_active_model_profile_version(session)
    if version is None:
        return settings
    task = _task_snapshot_for_version(version)
    return settings_for_task(settings, task, secret_store)


def settings_for_model_profile(
    session: Session,
    settings: Settings,
    profile_id: str,
    secret_store: ModelSecretStore | None = None,
) -> Settings:
    """按指定方案（全局通用，默认取其最新版本）构造运行时设置；
    供 AI 生成模板等场景独立选择方案，与任务激活方案解耦。"""
    version = get_model_profile_version(session, profile_id)
    task = _task_snapshot_for_version(version)
    return settings_for_task(settings, task, secret_store)


def settings_for_active_profile_metadata(
    session: Session,
    settings: Settings,
) -> Settings:
    version = get_active_model_profile_version(session)
    if version is None:
        return settings
    return replace(
        settings,
        model_provider=version.provider,
        model_base_url=version.base_url,
        model_name=version.model_name,
        model_reasoning_effort=version.reasoning_effort,
        model_timeout_seconds=version.timeout_seconds,
        model_context_length=profile_context_budget(version),
        model_temperature=version.temperature,
        model_api_key="",
    )


def _task_snapshot_for_version(version: ModelProfileVersionRecord) -> TaskRecord:
    return TaskRecord(
        id="active-model-profile",
        filename="",
        content_type="image/png",
        size_bytes=0,
        page_count=1,
        sha256="",
        storage_path="",
        model_config_version="profile-v1",
        model_profile_id=version.profile_id,
        model_profile_version=version.version,
        model_provider=version.provider,
        model_base_url=version.base_url,
        model_name=version.model_name,
        model_reasoning_effort=version.reasoning_effort,
        model_timeout_seconds=version.timeout_seconds,
        model_context_length=profile_context_budget(version),
        model_temperature=version.temperature,
        model_secret_ref=version.secret_ref,
    )
