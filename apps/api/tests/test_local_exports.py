from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from image_test_data import PNG_BYTES


def test_local_binding_is_independent_versioned_and_snapshotted(tmp_path: Path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    external = tmp_path / "external"
    external.mkdir()
    with TestClient(create_app(settings)) as client:
        template = client.get("/api/v1/templates").json()[0]
        url = f"/api/v1/templates/{template['id']}/local-export"
        assert client.get(url).json() == {"revision": 0, "enabled": False, "mode": "copy", "parent_path": None, "destination": None}
        changed = client.put(url, json={"expected_revision": 0, "enabled": True, "parent_path": str(external)})
        assert changed.status_code == 200
        assert changed.json()["destination"] == str(external / template["name"])
        assert client.get(f"/api/v1/templates/{template['id']}").json() == template
        assert str(external) not in client.get("/api/v1/templates").text
        uploaded = client.post("/api/v1/tasks", data={"template_id": template["id"]}, files={"file": ("原名.png", PNG_BYTES, "image/png")})
        assert uploaded.status_code == 201, uploaded.text
        frozen = uploaded.json()["file_export"]
        assert frozen["status"] == "awaiting_confirmation"
        assert frozen["destination"] == changed.json()["destination"]
        assert not (external / template["name"]).exists()
        assert client.put(url, json={"expected_revision": 0, "enabled": False}).status_code == 409
        assert client.put(url, json={"expected_revision": 1, "enabled": False}).status_code == 200
        with client.app.state.session_factory() as session:
            assert session.get(TaskRecord, uploaded.json()["id"]).file_export.model_dump() == frozen


def test_binding_rejects_missing_relative_and_internal_destinations(tmp_path: Path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as client:
        template = client.get("/api/v1/templates").json()[0]
        url = f"/api/v1/templates/{template['id']}/local-export"
        for path in ("relative", str(tmp_path / "missing"), str(settings.storage_dir)):
            response = client.put(url, json={"expected_revision": 0, "enabled": True, "parent_path": path})
            assert response.status_code == 422, response.text
        assert client.get(url).json()["revision"] == 0
