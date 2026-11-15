import hashlib

from fastapi.testclient import TestClient
import httpx
import pytest

from document_pipeline_api.api import updates
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app


@pytest.mark.parametrize("asset_result", ["verified", "wrong_digest", "wrong_host"])
def test_update_download_publishes_only_a_verified_official_asset(tmp_path, monkeypatch, asset_result):
    package = b"synthetic installer"
    digest = hashlib.sha256(package).hexdigest()
    version = "9999.0.0"
    name = f"Zhiyi-{version}-win-x64-setup.exe"
    host = "example.test" if asset_result == "wrong_host" else "release-assets.githubusercontent.com"
    asset_url = f"https://{host}/{name}"
    initial_url = f"{updates.RELEASE_PAGE}/download/v{version}/{name}"
    monkeypatch.setattr(updates, "_last", None)
    monkeypatch.setattr(updates, "_fetch_releases", lambda: [{
        "draft": False, "tag_name": f"v{version}", "assets": [{
            "name": name, "state": "uploaded", "size": len(package),
            "digest": "sha256:" + ("0" * 64 if asset_result == "wrong_digest" else digest),
        }],
    }])

    def transport(request):
        if str(request.url) == initial_url:
            return httpx.Response(302, headers={"Location": asset_url})
        assert str(request.url) == asset_url and host != "example.test"
        return httpx.Response(200, content=package)

    monkeypatch.setattr(updates, "_asset_client", lambda: httpx.Client(
        transport=httpx.MockTransport(transport), follow_redirects=True,
        event_hooks={"request": [updates._check_asset_host]},
    ))
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}",
                        storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as client:
        base = "/api/v1/updates"
        check = client.post(f"{base}/check").json()
        assert check["available"] and check["downloadable"] and "_asset" not in check
        response = client.post(f"{base}/download")
        cache = updates.download_directory(tmp_path)
        assert not list(cache.glob("*.part"))
        if asset_result == "verified":
            assert response.json() == {"downloaded_version": version}
            assert (cache / "update.exe").read_bytes() == package
            assert client.get(base).json()["downloaded_version"] == version
        else:
            assert response.status_code == 503
            assert not (cache / "update.exe").exists()
            assert client.get(base).json()["downloaded_version"] is None
