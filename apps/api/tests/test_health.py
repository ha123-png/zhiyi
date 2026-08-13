from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app


def test_health_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "document-pipeline-api",
        "version": "0.1.0",
    }


class StubProvider:
    model_name = "test-model"

    def available_models(self) -> list[str]:
        return ["test-model"]

    def close(self) -> None:
        pass


def test_system_status_distinguishes_worker_and_model(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "document_pipeline_api.api.system.read_worker_health",
        lambda: {"online": False, "last_seen_seconds_ago": None},
    )
    monkeypatch.setattr(
        "document_pipeline_api.api.system.build_model_provider",
        lambda _settings, timeout_seconds: StubProvider(),
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'status.db'}",
        storage_dir=tmp_path / "uploads",
        model_name="test-model",
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    assert response.json() == {
        "api": {"connected": True, "message": "API 已连接"},
        "worker": {"connected": False, "message": "任务消费者未启动"},
        "model": {"connected": True, "message": "模型 test-model 可用"},
        "model_provider": "lm_studio",
        "configured_model": "test-model",
    }
