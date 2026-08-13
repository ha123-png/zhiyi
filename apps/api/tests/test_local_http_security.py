from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app


def _client(tmp_path: Path, *, development_origins_enabled: bool = False) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'security.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
        development_origins_enabled=development_origins_enabled,
    )
    return TestClient(create_app(settings))


def test_untrusted_host_is_rejected_before_local_routes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get(
            "/api/v1/health",
            headers={"host": "attacker.example"},
        )

    assert response.status_code == 400


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_external_browser_origin_cannot_reach_local_write_routes(
    tmp_path: Path,
    method: str,
) -> None:
    with _client(tmp_path) as client:
        response = client.request(
            method,
            "/api/v1/tasks/not-known/cancel",
            headers={"origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "已拒绝来自其他网页的本机写入请求。"


def test_same_origin_and_non_browser_clients_remain_compatible(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        same_origin = client.post(
            "/api/v1/tasks/not-known/cancel",
            headers={
                "host": "127.0.0.1:8765",
                "origin": "http://127.0.0.1:8765",
            },
        )
        no_origin = client.post("/api/v1/tasks/not-known/cancel")

    assert same_origin.status_code == 404
    assert no_origin.status_code == 404


def test_development_origin_is_disabled_in_release_and_explicit_in_dev(
    tmp_path: Path,
) -> None:
    headers = {"origin": "http://127.0.0.1:5180"}
    with _client(tmp_path / "release") as release_client:
        release = release_client.post("/api/v1/tasks/not-known/cancel", headers=headers)
        release_cors = release_client.get("/api/v1/health", headers=headers)
    with _client(
        tmp_path / "development",
        development_origins_enabled=True,
    ) as development_client:
        development = development_client.post(
            "/api/v1/tasks/not-known/cancel",
            headers=headers,
        )
        development_cors = development_client.get("/api/v1/health", headers=headers)

    assert release.status_code == 403
    assert "access-control-allow-origin" not in release_cors.headers
    assert development.status_code == 404
    assert development_cors.headers["access-control-allow-origin"] == headers["origin"]
