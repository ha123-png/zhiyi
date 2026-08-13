from __future__ import annotations

import tempfile
from dataclasses import replace
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.model_providers import ModelProvider, build_model_provider
from document_pipeline_api.model_providers.base import (
    ModelRequestRejectedError,
    ModelServiceError,
)
from document_pipeline_api.model_secrets import ModelSecretStore, SecretStoreError
from document_pipeline_api.models import (
    ModelProfileRecord,
    ModelProfileVersionRecord,
    ModelRuntimeStateRecord,
)
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.model_profiles import (
    ModelProfileBody,
    ModelProfileCreate,
    ModelProfileRead,
    ModelProfileUpdate,
    ModelProbeResult,
)

EXPERIMENTAL_ONE_CLICK_PROFILE_ID = "zhiyi-builtin-qwen35-4b"
DEFAULT_LOCAL_PROFILE_ID = "default-local-qwen35-4b"


def ensure_default_local_profile(session: Session) -> None:
    """Seed an editable LM Studio example on a fresh or previously empty install."""
    existing = session.scalar(
        select(ModelProfileRecord.id).where(ModelProfileRecord.is_archived.is_(False))
    )
    if existing is not None:
        return
    profile = session.get(ModelProfileRecord, DEFAULT_LOCAL_PROFILE_ID)
    if profile is None:
        profile = ModelProfileRecord(id=DEFAULT_LOCAL_PROFILE_ID, current_version=1)
        version = ModelProfileVersionRecord(
            profile_id=profile.id,
            version=1,
            name="本地 AI 方案",
            provider="lm_studio",
            base_url="http://127.0.0.1:1234/v1",
            model_name="qwen3.5-4b",
            reasoning_effort="none",
            timeout_seconds=180,
            context_length=8192,
            temperature=None,
            multimodal=None,
            secret_ref=None,
            is_remote=False,
            remote_data_acknowledged=False,
        )
        session.add_all([profile, version])
    else:
        profile.is_archived = False
    state = session.get(ModelRuntimeStateRecord, 1)
    if state is None:
        state = ModelRuntimeStateRecord(id=1)
        session.add(state)
    state.active_profile_id = profile.id
    state.active_profile_version = profile.current_version
    session.commit()


def leave_experimental_one_click_profile(session: Session) -> None:
    """Stop routing production tasks through the shelved one-click experiment."""
    state = session.get(ModelRuntimeStateRecord, 1)
    experimental = session.get(ModelProfileRecord, EXPERIMENTAL_ONE_CLICK_PROFILE_ID)
    if state is not None and state.active_profile_id == EXPERIMENTAL_ONE_CLICK_PROFILE_ID:
        replacement = session.scalar(
            select(ModelProfileVersionRecord)
            .join(ModelProfileRecord, ModelProfileRecord.id == ModelProfileVersionRecord.profile_id)
            .where(
                ModelProfileVersionRecord.provider == "lm_studio",
                ModelProfileRecord.is_archived.is_(False),
                ModelProfileVersionRecord.version == ModelProfileRecord.current_version,
            )
            .order_by(ModelProfileVersionRecord.created_at.desc())
        )
        state.active_profile_id = replacement.profile_id if replacement is not None else None
        state.active_profile_version = replacement.version if replacement is not None else None
    if experimental is not None:
        experimental.is_archived = True
    if state is not None or experimental is not None:
        session.commit()


def classify_model_endpoint(base_url: str) -> tuple[str, bool]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="AI 服务地址必须是有效的 http 或 https URL。")
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(status_code=422, detail="AI 服务地址不能包含用户名、密码或 API key。")
    if parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="AI 服务地址不能包含查询参数或片段。")
    hostname = parsed.hostname.rstrip(".").lower()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    return parsed.geturl(), not is_loopback


def list_model_profiles(session: Session) -> list[ModelProfileRead]:
    state = session.get(ModelRuntimeStateRecord, 1)
    records = session.execute(
        select(ModelProfileRecord, ModelProfileVersionRecord)
        .join(
            ModelProfileVersionRecord,
            (ModelProfileVersionRecord.profile_id == ModelProfileRecord.id)
            & (ModelProfileVersionRecord.version == ModelProfileRecord.current_version),
        )
        .where(ModelProfileRecord.is_archived.is_(False))
        .order_by(ModelProfileRecord.created_at.asc())
    ).all()
    return [_as_read(profile, version, state) for profile, version in records]


def get_model_profile(session: Session, profile_id: str) -> ModelProfileRead:
    profile = session.get(ModelProfileRecord, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="没有找到这个 AI 服务方案。")
    version = _get_version(session, profile.id, profile.current_version)
    return _as_read(profile, version, session.get(ModelRuntimeStateRecord, 1))


def create_model_profile(
    session: Session,
    body: ModelProfileCreate,
    secret_store: ModelSecretStore | None,
) -> ModelProfileRead:
    base_url, is_remote = _validated_endpoint(body)
    secret_ref = _store_new_secret(body.api_key, secret_store)
    profile = ModelProfileRecord(id=str(uuid4()), current_version=1)
    version = _version_record(profile.id, 1, body, base_url, is_remote, secret_ref)
    try:
        session.add_all([profile, version])
        session.commit()
    except Exception:
        session.rollback()
        _discard_new_secret(secret_ref, secret_store)
        raise
    return _as_read(profile, version, session.get(ModelRuntimeStateRecord, 1))


def update_model_profile(
    session: Session,
    profile_id: str,
    body: ModelProfileUpdate,
    secret_store: ModelSecretStore | None,
) -> ModelProfileRead:
    profile = session.get(ModelProfileRecord, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="没有找到这个 AI 服务方案。")
    if profile.is_archived:
        raise HTTPException(status_code=409, detail="已停用的 AI 服务方案不能修改。")
    if profile.current_version != body.expected_version:
        raise HTTPException(status_code=409, detail="AI 服务方案已更新，请刷新后重试。")
    previous = _get_version(session, profile.id, profile.current_version)
    base_url, is_remote = _validated_endpoint(body)
    new_secret_ref = _store_new_secret(body.api_key, secret_store)
    secret_ref = None if body.clear_api_key else new_secret_ref or previous.secret_ref
    next_version = profile.current_version + 1
    version = _version_record(
        profile.id,
        next_version,
        body,
        base_url,
        is_remote,
        secret_ref,
    )
    profile.current_version = next_version
    profile.updated_at = utc_now()
    try:
        session.add(version)
        session.commit()
    except Exception:
        session.rollback()
        _discard_new_secret(new_secret_ref, secret_store)
        raise
    return _as_read(profile, version, session.get(ModelRuntimeStateRecord, 1))


def activate_model_profile(
    session: Session,
    profile_id: str,
    version_number: int,
    acknowledge: bool = False,
) -> ModelProfileRead:
    profile = session.get(ModelProfileRecord, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="没有找到这个 AI 服务方案。")
    if profile.is_archived:
        raise HTTPException(status_code=409, detail="已停用的 AI 服务方案不能激活。")
    version = _get_version(session, profile_id, version_number)
    # 云端方案每次激活都必须确认数据离开本机：防止用户不小心切换到了云端方案。
    # 不做"确认过一次就跳过"的记忆，确认只对这一次激活生效。
    if version.is_remote and not acknowledge:
        raise HTTPException(
            status_code=409,
            detail="此方案在云端：激活前请确认文件内容会发送到该服务。",
        )
    state = session.get(ModelRuntimeStateRecord, 1)
    if state is None:
        state = ModelRuntimeStateRecord(id=1)
        session.add(state)
    state.active_profile_id = profile_id
    state.active_profile_version = version_number
    state.updated_at = utc_now()
    session.commit()
    return _as_read(profile, _get_version(session, profile.id, profile.current_version), state)


def archive_model_profile(session: Session, profile_id: str) -> ModelProfileRead:
    profile = session.get(ModelProfileRecord, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="没有找到这个 AI 服务方案。")
    state = session.get(ModelRuntimeStateRecord, 1)
    if state is not None and state.active_profile_id == profile_id:
        raise HTTPException(
            status_code=409, detail="正在使用的 AI 服务方案不能停用，请先激活其他方案。"
        )
    profile.is_archived = True
    profile.updated_at = utc_now()
    session.commit()
    return _as_read(profile, _get_version(session, profile.id, profile.current_version), state)


def get_active_model_profile_version(
    session: Session,
) -> ModelProfileVersionRecord | None:
    state = session.get(ModelRuntimeStateRecord, 1)
    if state is None or state.active_profile_id is None or state.active_profile_version is None:
        return None
    profile = session.get(ModelProfileRecord, state.active_profile_id)
    if profile is None or profile.is_archived:
        raise ValueError("当前激活的 AI 服务方案不存在或已停用。")
    version = session.scalar(
        select(ModelProfileVersionRecord).where(
            ModelProfileVersionRecord.profile_id == state.active_profile_id,
            ModelProfileVersionRecord.version == state.active_profile_version,
        )
    )
    if version is None:
        raise ValueError("当前激活的 AI 服务方案版本不存在。")
    return version


def get_model_profile_version(
    session: Session,
    profile_id: str,
    version_number: int | None = None,
) -> ModelProfileVersionRecord:
    """按方案 ID 取版本（默认其最新版本）；供 AI 生成模板等独立选择方案使用。"""
    profile = session.get(ModelProfileRecord, profile_id)
    if profile is None or profile.is_archived:
        raise HTTPException(status_code=404, detail="没有找到这个 AI 服务方案。")
    target = version_number or profile.current_version
    version = session.scalar(
        select(ModelProfileVersionRecord).where(
            ModelProfileVersionRecord.profile_id == profile_id,
            ModelProfileVersionRecord.version == target,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="没有找到这个 AI 服务方案版本。")
    return version


def _validated_endpoint(body: ModelProfileBody) -> tuple[str, bool]:
    # 保存方案时不要求确认远端传输；确认推迟到“激活（使用）此方案”时弹出，
    # 与原型行为一致——创建/编辑云端方案是配置，激活才代表真正开始使用。
    return classify_model_endpoint(body.base_url)


def _store_new_secret(api_key, secret_store: ModelSecretStore | None) -> str | None:
    if api_key is None:
        return None
    if secret_store is None:
        raise HTTPException(status_code=503, detail="Windows 系统密钥库当前不可用。")
    try:
        return secret_store.put(api_key.get_secret_value())
    except SecretStoreError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _discard_new_secret(
    secret_ref: str | None,
    secret_store: ModelSecretStore | None,
) -> None:
    if secret_ref is None or secret_store is None:
        return
    try:
        secret_store.delete(secret_ref)
    except SecretStoreError:
        pass


def _version_record(
    profile_id: str,
    version: int,
    body: ModelProfileBody,
    base_url: str,
    is_remote: bool,
    secret_ref: str | None,
) -> ModelProfileVersionRecord:
    return ModelProfileVersionRecord(
        profile_id=profile_id,
        version=version,
        name=body.name,
        provider=body.provider,
        base_url=base_url,
        model_name=body.model_name,
        reasoning_effort=body.reasoning_effort,
        timeout_seconds=body.timeout_seconds,
        context_length=body.context_length,
        temperature=body.temperature,
        multimodal=body.multimodal,
        secret_ref=secret_ref,
        is_remote=is_remote,
        remote_data_acknowledged=(body.acknowledge_remote_data_transfer if is_remote else False),
    )


def _get_version(
    session: Session,
    profile_id: str,
    version: int,
) -> ModelProfileVersionRecord:
    record = session.scalar(
        select(ModelProfileVersionRecord).where(
            ModelProfileVersionRecord.profile_id == profile_id,
            ModelProfileVersionRecord.version == version,
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="没有找到这个 AI 服务方案版本。")
    return record


def _as_read(
    profile: ModelProfileRecord,
    version: ModelProfileVersionRecord,
    state: ModelRuntimeStateRecord | None,
) -> ModelProfileRead:
    active_version = (
        state.active_profile_version
        if state is not None and state.active_profile_id == profile.id
        else None
    )
    return ModelProfileRead(
        id=profile.id,
        version=version.version,
        current_version=profile.current_version,
        name=version.name,
        provider=version.provider,
        base_url=version.base_url,
        model_name=version.model_name,
        reasoning_effort=version.reasoning_effort,
        timeout_seconds=version.timeout_seconds,
        context_length=version.context_length,
        temperature=version.temperature,
        multimodal=version.multimodal,
        has_api_key=version.secret_ref is not None,
        is_remote=version.is_remote,
        remote_data_acknowledged=version.remote_data_acknowledged,
        is_active=active_version is not None,
        active_version=active_version,
        is_archived=profile.is_archived,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


class _ProbeReply(BaseModel):
    ok: bool


def probe_model_profile(
    settings: Settings,
    body: ModelProfileBody,
    *,
    session: Session | None = None,
    secret_store: ModelSecretStore | None = None,
    profile_id: str | None = None,
) -> ModelProbeResult:
    """连通性 + 多模态探测（不落库）：配置页“测试连接”使用。

    连通性：请求 /models 列表并检查配置的模型名是否在列。
    多模态：发送一张 1x1 测试图走视觉请求；服务端拒绝图片请求（4xx）视为仅文本，
    请求成功视为支持图片，其余错误（超时/服务不可用）视为“无法验证”。
    表单未填 API Key 但指定了已保存密钥的方案时，自动使用其已保存密钥。
    """
    try:
        base_url, _ = classify_model_endpoint(body.base_url)
    except HTTPException as error:
        return ModelProbeResult(
            connected=False,
            model_listed=False,
            multimodal=None,
            message=error.detail,
        )
    api_key = body.api_key.get_secret_value() if body.api_key is not None else ""
    if not api_key and session is not None and profile_id is not None:
        # 表单留空但方案已保存过密钥：探测用已保存密钥，避免“必须重新输入才能测试”
        try:
            version = get_model_profile_version(session, profile_id)
        except HTTPException:
            version = None
        if version is not None and version.secret_ref is not None and secret_store is not None:
            try:
                api_key = secret_store.get(version.secret_ref)
            except SecretStoreError as error:
                return ModelProbeResult(
                    connected=False,
                    model_listed=False,
                    multimodal=None,
                    message=f"已保存的密钥不可用，请重新输入后测试：{error}",
                )
    probe_settings = replace(
        settings,
        model_provider=body.provider,
        model_base_url=base_url,
        model_name=body.model_name,
        model_reasoning_effort=body.reasoning_effort,
        model_timeout_seconds=min(body.timeout_seconds, 30),
        model_temperature=body.temperature,
        model_api_key=api_key,
    )
    client = build_model_provider(probe_settings)
    try:
        try:
            available = client.available_models()
        except ModelServiceError as error:
            return ModelProbeResult(
                connected=False,
                model_listed=False,
                multimodal=None,
                message=f"无法连接模型服务：{error}",
            )
        model_listed = body.model_name in available
        multimodal = _probe_multimodal(client)
        if multimodal is False:
            detail = "连通正常，但模型拒绝了图片输入，很可能只支持文本；图片类文件无法识别。"
        elif multimodal is True:
            detail = "连通正常，模型接受了图片输入（支持多模态）。"
        else:
            detail = "连通正常，但无法确认多模态能力（建议更换模型后重试）。"
        return ModelProbeResult(
            connected=True,
            model_listed=model_listed,
            multimodal=multimodal,
            available_models=available,
            message=(
                f"模型服务可访问；配置的模型{'在' if model_listed else '不在'}服务列表中。{detail}"
            ),
        )
    finally:
        client.close()


def _probe_multimodal(client: ModelProvider) -> bool | None:
    """用一张小测试图验证模型是否接受图片输入。

    注意图片不能太小：DashScope 等云服务的视觉模型要求宽高大于 10 像素，
    1x1 的图会被当作“图片不合规”整体拒绝，导致误判为仅文本。
    """
    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe_image = Path(tmp) / "probe.png"
            Image.new("RGB", (64, 64), (255, 0, 0)).save(probe_image)
            client.extract_image(
                probe_image,
                "这张图片是纯红色。只回答 true。",
                _ProbeReply,
            )
        return True
    except ModelRequestRejectedError:
        # 4xx：服务端明确拒绝了图片请求（典型为仅文本模型不支持 image_url）
        return False
    except ModelServiceError:
        # 超时/服务异常：无法区分是否支持图片，保持“未验证”
        return None
