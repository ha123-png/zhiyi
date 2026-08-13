from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.runtime_paths import api_resource_dir, default_data_dir


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "uploads",
    )


def test_bundled_web_is_same_origin_spa_without_shadowing_api(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    (web_dir / "assets").mkdir(parents=True)
    (web_dir / "index.html").write_text("<h1>Document Pipeline</h1>", encoding="utf-8")
    (web_dir / "assets" / "app.js").write_text("window.ready=true", encoding="utf-8")

    with TestClient(create_app(_settings(tmp_path), web_dir=web_dir)) as client:
        assert client.get("/").text == "<h1>Document Pipeline</h1>"
        assert client.get("/templates/custom").text == "<h1>Document Pipeline</h1>"
        assert client.get("/assets/app.js").text == "window.ready=true"
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert client.get("/api/not-a-route").status_code == 404


def test_runtime_paths_can_be_explicitly_isolated(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    api_dir = tmp_path / "api-runtime"
    monkeypatch.setenv("DOCUMENT_PIPELINE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DOCUMENT_PIPELINE_API_RESOURCE_DIR", str(api_dir))

    assert default_data_dir() == data_dir.resolve()
    assert api_resource_dir() == api_dir.resolve()
