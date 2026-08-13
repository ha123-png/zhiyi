from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app


def test_onboarding_state_persists_outside_browser_storage(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/user-state/onboarding").json() == {"completed_version": 0}
        response = client.put(
            "/api/v1/user-state/onboarding", json={"completed_version": 2}
        )
        assert response.status_code == 200
        assert response.json() == {"completed_version": 2}

    with TestClient(create_app(settings)) as restarted_client:
        assert restarted_client.get("/api/v1/user-state/onboarding").json() == {
            "completed_version": 2
        }
