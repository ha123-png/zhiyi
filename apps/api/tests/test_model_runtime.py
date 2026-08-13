from pathlib import Path

import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.model_secrets import ModelSecretStore, SecretNotFoundError
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.services.model_runtime import (
    model_snapshot_values,
    settings_for_task,
)


def _task(**overrides) -> TaskRecord:
    values = {
        "id": "snapshot-task",
        "filename": "sample.png",
        "content_type": "image/png",
        "size_bytes": 1,
        "sha256": "digest",
        "storage_path": "sample.png",
        "model_config_version": "environment-v1",
        "model_provider": "ollama",
        "model_base_url": "http://127.0.0.1:11434",
        "model_name": "snapshot-model",
        "model_reasoning_effort": None,
        "model_timeout_seconds": 77,
        "model_secret_ref": "environment",
    }
    values.update(overrides)
    return TaskRecord(**values)


def test_task_snapshot_overrides_later_global_model_configuration(tmp_path: Path) -> None:
    current = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path,
        model_provider="lm_studio",
        model_base_url="http://later.example/v1",
        model_name="later-model",
        model_api_key="secret-never-copied",
        model_reasoning_effort="none",
        model_timeout_seconds=180,
    )

    effective = settings_for_task(current, _task())

    assert effective.model_provider == "ollama"
    assert effective.model_base_url == "http://127.0.0.1:11434"
    assert effective.model_name == "snapshot-model"
    assert effective.model_timeout_seconds == 77
    assert effective.model_api_key == "secret-never-copied"
    snapshot = model_snapshot_values(current)
    assert "model_api_key" not in snapshot
    assert "secret-never-copied" not in snapshot.values()


def test_legacy_task_uses_startup_configuration_and_invalid_snapshot_fails(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path,
    )
    assert settings_for_task(
        settings,
        _task(model_config_version="legacy-unversioned"),
    ) is settings
    with pytest.raises(ValueError, match="不完整"):
        settings_for_task(settings, _task(model_name=None))
    with pytest.raises(ValueError, match="密钥来源"):
        settings_for_task(settings, _task(model_secret_ref="unknown-secret"))


class MissingCredentialBackend:
    def write(self, target: str, secret: bytes) -> None:
        raise AssertionError("not used")

    def read(self, target: str) -> bytes:
        raise SecretNotFoundError("missing")

    def delete(self, target: str) -> None:
        raise AssertionError("not used")


def test_profile_task_fails_safely_when_its_exact_secret_is_missing(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path,
        model_api_key="later-global-secret",
    )
    profile_task = _task(
        model_config_version="profile-v1",
        model_profile_id="profile-id",
        model_profile_version=2,
        model_secret_ref="wincred:12345678-1234-4123-8123-123456789abc",
    )

    with pytest.raises(ValueError, match="密钥不可用"):
        settings_for_task(
            settings,
            profile_task,
            ModelSecretStore(MissingCredentialBackend()),
        )
