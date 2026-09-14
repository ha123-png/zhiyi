from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.main import create_app
from document_pipeline_api.model_providers.base import (
    ModelRequestRejectedError,
    ModelServiceError,
)
from document_pipeline_api.model_secrets import ModelSecretStore, SecretNotFoundError
from document_pipeline_api.models import (
    ModelProfileRecord,
    ModelProfileVersionRecord,
    TaskRecord,
)
from document_pipeline_api.schemas.model_profiles import ModelProfileBody
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.services.model_profiles import probe_model_profile
from document_pipeline_api.services.model_runtime import settings_for_task
from image_test_data import PNG_BYTES


class MemoryCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def write(self, target: str, secret: bytes) -> None:
        self.values[target] = secret

    def read(self, target: str) -> bytes:
        try:
            return self.values[target]
        except KeyError as error:
            raise SecretNotFoundError("missing") from error

    def delete(self, target: str) -> None:
        if target not in self.values:
            raise SecretNotFoundError("missing")
        del self.values[target]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'profiles.db'}",
        storage_dir=tmp_path / "uploads",
    )


def _profile_body(*, key: str | None = "cloud-secret-one") -> dict[str, object]:
    body: dict[str, object] = {
        "name": "远端视觉模型",
        "provider": "openai_compatible",
        "base_url": "https://models.example.test/v1",
        "model_name": "vision-model-v1",
        "reasoning_effort": "none",
        "timeout_seconds": 90,
        "acknowledge_remote_data_transfer": True,
    }
    if key is not None:
        body["api_key"] = key
    return body


def test_remote_profile_confirmation_is_required_on_every_activation(tmp_path: Path) -> None:
    """云端方案每次激活都必须确认：确认只对这一次激活生效，不做一次性记忆。"""
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    body = _profile_body()
    body["acknowledge_remote_data_transfer"] = False

    with TestClient(create_app(_settings(tmp_path), model_secret_store=store)) as client:
        response = client.post("/api/v1/models/profiles", json=body)
        assert response.status_code == 201
        profile = response.json()
        assert profile["is_remote"] is True
        assert profile["remote_data_acknowledged"] is False
        assert backend.values  # 密钥已保存

        # 第一次激活：未确认被拒绝
        rejected = client.post(
            f"/api/v1/models/profiles/{profile['id']}/activate",
            json={"version": 1},
        )
        assert rejected.status_code == 409
        assert "云端" in rejected.json()["detail"]

        # 带确认激活成功，且不写回"已确认"
        activated = client.post(
            f"/api/v1/models/profiles/{profile['id']}/activate",
            json={"version": 1, "acknowledge_remote_data_transfer": True},
        )
        assert activated.status_code == 200
        assert activated.json()["active_version"] == 1

        # 第二次激活：即使已经激活过，仍必须再次确认（防止误切到云端）
        rejected_again = client.post(
            f"/api/v1/models/profiles/{profile['id']}/activate",
            json={"version": 1},
        )
        assert rejected_again.status_code == 409
        assert "云端" in rejected_again.json()["detail"]


def test_invalid_profile_request_never_echoes_api_key(tmp_path: Path) -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    body = _profile_body(key="must-never-return-in-validation")
    del body["provider"]

    with TestClient(create_app(_settings(tmp_path), model_secret_store=store)) as client:
        response = client.post("/api/v1/models/profiles", json=body)

    assert response.status_code == 422
    assert "must-never-return-in-validation" not in response.text
    assert '"input"' not in response.text
    assert backend.values == {}


def test_profile_versions_activation_and_tasks_keep_exact_secret(tmp_path: Path) -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    settings = _settings(tmp_path)

    with TestClient(create_app(settings, model_secret_store=store)) as client:
        created = client.post("/api/v1/models/profiles", json=_profile_body())
        assert created.status_code == 201
        first = created.json()
        assert first["version"] == 1
        assert first["has_api_key"] is True
        assert first["is_active"] is False
        assert "api_key" not in first
        assert "secret_ref" not in first

        activated = client.post(
            f"/api/v1/models/profiles/{first['id']}/activate",
            json={"version": 1, "acknowledge_remote_data_transfer": True},
        )
        assert activated.status_code == 200
        assert activated.json()["active_version"] == 1

        first_task_response = client.post(
            "/api/v1/tasks",
            data={"template_mode": "smart"},
            files={"file": ("first.png", PNG_BYTES, "image/png")},
        )
        assert first_task_response.status_code == 201
        first_task_id = first_task_response.json()["id"]

        updated_body = _profile_body(key="cloud-secret-two")
        updated_body.update(
            {
                "expected_version": 1,
                "model_name": "vision-model-v2",
                "clear_api_key": False,
            }
        )
        updated = client.put(
            f"/api/v1/models/profiles/{first['id']}",
            json=updated_body,
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert updated.json()["active_version"] == 1

        second_activation = client.post(
            f"/api/v1/models/profiles/{first['id']}/activate",
            json={"version": 2, "acknowledge_remote_data_transfer": True},
        )
        assert second_activation.status_code == 200
        second_task_response = client.post(
            "/api/v1/tasks",
            data={"template_mode": "smart"},
            files={"file": ("second.png", PNG_BYTES, "image/png")},
        )
        assert second_task_response.status_code == 201
        second_task_id = second_task_response.json()["id"]

        assert client.post(f"/api/v1/models/profiles/{first['id']}/archive").status_code == 409

        with client.app.state.session_factory() as session:
            first_task = session.get(TaskRecord, first_task_id)
            second_task = session.get(TaskRecord, second_task_id)
            assert first_task is not None and second_task is not None
            assert first_task.model_config_version == "profile-v1"
            assert first_task.model_profile_id == first["id"]
            assert first_task.model_profile_version == 1
            assert second_task.model_profile_version == 2
            assert first_task.model_secret_ref != second_task.model_secret_ref
            assert settings_for_task(settings, first_task, store).model_api_key == (
                "cloud-secret-one"
            )
            assert settings_for_task(settings, second_task, store).model_api_key == (
                "cloud-secret-two"
            )
            versions = (
                session.query(ModelProfileVersionRecord)
                .filter(ModelProfileVersionRecord.profile_id == first["id"])
                .all()
            )
            assert [version.version for version in versions] == [1, 2]
            assert all("cloud-secret" not in repr(version.__dict__) for version in versions)

    assert sorted(backend.values.values()) == [b"cloud-secret-one", b"cloud-secret-two"]
    with sqlite3.connect(tmp_path / "profiles.db") as connection:
        dump = "\n".join(connection.iterdump())
    assert "cloud-secret-one" not in dump
    assert "cloud-secret-two" not in dump


def test_local_profile_can_clear_key_and_url_credentials_are_rejected(tmp_path: Path) -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    settings = _settings(tmp_path)
    local: dict[str, object] = {
        "name": "本机 Ollama",
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model_name": "qwen-local",
        "timeout_seconds": 120,
    }
    with TestClient(create_app(settings, model_secret_store=store)) as client:
        rejected = client.post(
            "/api/v1/models/profiles",
            json={**local, "base_url": "https://user:key@example.test/v1"},
        )
        assert rejected.status_code == 422
        assert backend.values == {}
        query_rejected = client.post(
            "/api/v1/models/profiles",
            json={**local, "base_url": "https://example.test/v1?api_key=secret"},
        )
        assert query_rejected.status_code == 422
        assert backend.values == {}

        created = client.post(
            "/api/v1/models/profiles",
            json={**local, "api_key": "local-secret"},
        )
        assert created.status_code == 201
        profile = created.json()
        assert profile["is_remote"] is False
        assert profile["has_api_key"] is True
        cleared = client.put(
            f"/api/v1/models/profiles/{profile['id']}",
            json={
                **local,
                "expected_version": 1,
                "clear_api_key": True,
            },
        )
        assert cleared.status_code == 200
        profile = cleared.json()
        assert profile["version"] == 2
        assert profile["has_api_key"] is False
        assert (
            client.post(
                f"/api/v1/models/profiles/{profile['id']}/activate",
                json={"version": 2},
            ).status_code
            == 200
        )


def test_profile_round_trips_context_length_and_temperature(tmp_path: Path) -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    settings = _settings(tmp_path)
    body = _profile_body()
    body.update({"context_length": 8192, "temperature": 0.3})

    with TestClient(create_app(settings, model_secret_store=store)) as client:
        created = client.post("/api/v1/models/profiles", json=body)
        assert created.status_code == 201
        profile = created.json()
        assert profile["context_length"] == 8192
        assert profile["temperature"] == 0.3

        assert (
            client.post(
                f"/api/v1/models/profiles/{profile['id']}/activate",
                json={"version": 1, "acknowledge_remote_data_transfer": True},
            ).status_code
            == 200
        )
        task_response = client.post(
            "/api/v1/tasks",
            data={"template_mode": "smart"},
            files={"file": ("ctx.png", PNG_BYTES, "image/png")},
        )
        assert task_response.status_code == 201

    with client.app.state.session_factory() as session:
        task = session.query(TaskRecord).one()
        assert task.model_context_length == 8192
        assert task.model_temperature == 0.3
        resolved = settings_for_task(settings, task, store)
        assert resolved.model_context_length == 8192
        assert resolved.model_temperature == 0.3


def test_default_profile_uses_default_context_and_no_temperature(
    tmp_path: Path,
) -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    with TestClient(create_app(_settings(tmp_path), model_secret_store=store)) as client:
        created = client.post("/api/v1/models/profiles", json=_profile_body())
        assert created.status_code == 201
        profile = created.json()
        assert profile["context_length"] == 32768
        assert profile["context_policy"] == "auto"
        assert profile["temperature"] is None


def test_fresh_install_seeds_active_qwen35_example_profile(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        profiles = client.get("/api/v1/models/profiles").json()
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["name"] == "本地 AI 方案"
    assert profile["provider"] == "lm_studio"
    assert profile["model_name"] == "qwen3.5-4b"
    assert profile["context_length"] == 8192
    assert profile["is_active"] is True


def test_local_model_panel_remains_available_with_a_remote_profile(tmp_path: Path) -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    settings = _settings(tmp_path)
    local: dict[str, object] = {
        "name": "本机 Ollama",
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model_name": "qwen-local",
        "timeout_seconds": 120,
    }

    with TestClient(create_app(settings, model_secret_store=store)) as client:
        remote = client.post("/api/v1/models/profiles", json=_profile_body())
        assert remote.status_code == 201
        assert (
            client.post(
                f"/api/v1/models/profiles/{remote.json()['id']}/activate",
                json={"version": 1, "acknowledge_remote_data_transfer": True},
            ).status_code
            == 200
        )
        # 本地模型管理是独立工具；切到云端方案后仍可管理 LM Studio。
        status = client.get("/api/v1/system/local-model/status")
        assert status.status_code == 200
        assert status.json()["provider"] == "lm_studio"

        local_profile = client.post("/api/v1/models/profiles", json=local)
        assert local_profile.status_code == 201
        assert (
            client.post(
                f"/api/v1/models/profiles/{local_profile.json()['id']}/activate",
                json={"version": 1},
            ).status_code
            == 200
        )
        # Ollama 未运行时应优雅返回未运行状态而不是 500
        status = client.get("/api/v1/system/local-model/status")
        assert status.status_code == 200
        assert status.json()["provider"] == "ollama"
        assert status.json()["running"] is False


def test_build_local_model_manager_rejects_non_local_provider() -> None:
    from document_pipeline_api.model_providers.local_model_manager import (
        build_local_model_manager,
    )

    try:
        build_local_model_manager("openai_compatible", "http://127.0.0.1:1234/v1")
    except ValueError:
        pass
    else:
        raise AssertionError("openai_compatible 不应支持本地模型管理")


class _ProbeClient:
    model_name = "probe-model"

    def __init__(self, *, multimodal: bool | None = True) -> None:
        self._multimodal = multimodal

    def available_models(self) -> list[str]:
        return [self.model_name]

    def extract_image(self, _path, _prompt, result_type):
        if self._multimodal is False:
            raise ModelRequestRejectedError("image_url is not supported")
        return result_type.model_validate({"ok": True})

    def close(self) -> None:
        pass


def test_probe_model_profile_reports_connectivity_and_multimodal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from document_pipeline_api import services

    settings = _settings(tmp_path)
    body = ModelProfileBody(
        name="探测",
        provider="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        model_name="probe-model",
    )

    monkeypatch.setattr(
        services.model_profiles,
        "build_model_provider",
        lambda probe_settings: _ProbeClient(multimodal=True),
    )
    result = probe_model_profile(settings, body)
    assert result.connected is True
    assert result.model_listed is True
    assert result.multimodal is True

    monkeypatch.setattr(
        services.model_profiles,
        "build_model_provider",
        lambda probe_settings: _ProbeClient(multimodal=False),
    )
    result = probe_model_profile(settings, body)
    assert result.connected is True
    assert result.multimodal is False

    class _UnreachableClient:
        model_name = "probe-model"

        def available_models(self) -> list[str]:
            raise ModelServiceError("无法连接模型服务")

        def extract_image(self, _path, _prompt, result_type):
            raise AssertionError("连通失败不应继续探测多模态")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        services.model_profiles,
        "build_model_provider",
        lambda probe_settings: _UnreachableClient(),
    )
    result = probe_model_profile(settings, body)
    assert result.connected is False
    assert result.multimodal is None


class _NeverCalledClient:
    model_name = "never-called"

    def extract_image(self, _path, _prompt, _result_type):
        raise AssertionError("仅文本方案应在调用模型前被拦截。")

    def close(self) -> None:
        pass


def test_text_only_profile_fails_image_task_with_clear_code(tmp_path: Path) -> None:
    database = tmp_path / "guard.db"
    database_url = f"sqlite:///{database}"
    storage_dir = tmp_path / "uploads"
    storage_dir.mkdir()
    settings = Settings(database_url=database_url, storage_dir=storage_dir)

    engine = build_engine(database_url)
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(ModelProfileRecord(id="prof-text", current_version=1))
            session.add(
                ModelProfileVersionRecord(
                    profile_id="prof-text",
                    version=1,
                    name="纯文本",
                    provider="openai_compatible",
                    base_url="http://127.0.0.1:9/v1",
                    model_name="text-only",
                    timeout_seconds=30,
                    multimodal=False,
                    is_remote=False,
                )
            )
            session.add(
                TaskRecord(
                    id="task-text",
                    filename="a.png",
                    content_type="image/png",
                    size_bytes=1,
                    page_count=1,
                    sha256="sha-text",
                    storage_path="a.png",
                    template_mode="smart",
                    model_config_version="profile-v1",
                    model_profile_id="prof-text",
                    model_profile_version=1,
                    model_provider="openai_compatible",
                    model_base_url="http://127.0.0.1:9/v1",
                    model_name="text-only",
                    model_timeout_seconds=30,
                )
            )
            session.commit()
        with Session(engine) as session:
            process_task(session, settings, "task-text", client=_NeverCalledClient())
            failed = session.get(TaskRecord, "task-text")
            assert failed is not None
            assert failed.status == TaskStatus.FAILED.value
            assert failed.failure_code == "model_not_multimodal"
            assert "多模态模型" in failed.failure_message
    finally:
        engine.dispose()
