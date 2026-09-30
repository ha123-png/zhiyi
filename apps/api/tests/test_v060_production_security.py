"""New production-boundary regressions; synthetic credentials and loopback only."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_secrets import ModelSecretStore
from document_pipeline_api.models import ModelProfileVersionRecord
from document_pipeline_api.services.model_runtime import settings_for_active_profile, settings_for_model_profile
from test_model_profiles_api import MemoryCredentialBackend


SYNTHETIC_KEY = "synthetic-production-test-original-provider-only"


@contextmanager
def provider_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.reply({"data": [{"id": "synthetic-model"}]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.reply({"choices": [{"message": {"content": '{"ok":true}'}}]})

        def reply(self, payload):
            requests.append({
                "path": self.path,
                "method": self.command,
                "original_credential_received": self.headers.get("Authorization")
                == f"Bearer {SYNTHETIC_KEY}",
                "has_authorization": "Authorization" in self.headers,
            })
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def profile_body(base_url, **overrides):
    return {"name": "Synthetic endpoint", "provider": "openai_compatible",
            "base_url": base_url, "model_name": "synthetic-model", **overrides}


@contextmanager
def client_with_store(tmp_path: Path):
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'security.db'}",
                        storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings, model_secret_store=store),
                    raise_server_exceptions=False) as client:
        yield client, store, backend


def test_changed_probe_destination_does_not_receive_saved_credential(tmp_path: Path):
    with provider_server() as (first_url, first_requests), provider_server() as (second_url, second_requests):
        with client_with_store(tmp_path) as (client, _store, _backend):
            created = client.post("/api/v1/models/profiles", json=profile_body(first_url, api_key=SYNTHETIC_KEY))
            assert created.status_code == 201
            profile = created.json()
            response = client.post("/api/v1/models/profiles/probe", params={"profile_id": profile["id"]},
                                   json=profile_body(second_url))
            assert response.status_code == 200
            assert response.json()["connected"] is True
            assert first_requests == []
            assert second_requests and not any(item["has_authorization"] for item in second_requests)


@pytest.mark.parametrize("change", ["host", "port", "path", "scheme", "provider"])
def test_changed_service_detaches_secret_but_preserves_old_version(tmp_path: Path, change: str):
    original = profile_body("https://one.example.test/v1", api_key=SYNTHETIC_KEY)
    changed = profile_body(original["base_url"], expected_version=1)
    changed.update({
        "host": {"base_url": "https://two.example.test/v1"},
        "port": {"base_url": "https://one.example.test:8443/v1"},
        "path": {"base_url": "https://one.example.test/other-tenant/v1"},
        "scheme": {"base_url": "http://one.example.test/v1"},
        "provider": {"provider": "lm_studio"},
    }[change])
    with client_with_store(tmp_path) as (client, store, backend):
        created = client.post("/api/v1/models/profiles", json=original)
        assert created.status_code == 201
        profile_id = created.json()["id"]
        updated = client.put(f"/api/v1/models/profiles/{profile_id}", json=changed)
        assert updated.status_code == 200
        assert updated.json()["has_api_key"] is False
        with client.app.state.session_factory() as session:
            versions = session.scalars(select(ModelProfileVersionRecord)
                                       .where(ModelProfileVersionRecord.profile_id == profile_id)
                                       .order_by(ModelProfileVersionRecord.version)).all()
            assert len(versions) == 2
            assert store.get(versions[0].secret_ref) == SYNTHETIC_KEY
            assert versions[1].secret_ref is None
        assert len(backend.values) == 1


def test_same_endpoint_normalization_keeps_credential(tmp_path: Path):
    with client_with_store(tmp_path) as (client, _store, backend):
        profile = client.post("/api/v1/models/profiles", json=profile_body(
            "https://ONE.example.test:443/v1/", api_key=SYNTHETIC_KEY)).json()
        updated = client.put(f"/api/v1/models/profiles/{profile['id']}", json=profile_body(
            "https://one.example.test/v1", model_name="renamed-model", expected_version=1))
        assert updated.status_code == 200
        assert updated.json()["has_api_key"] is True
        assert len(backend.values) == 1


def test_probe_normalizes_completion_suffix_before_using_same_service_key(tmp_path: Path):
    with provider_server() as (url, requests), client_with_store(tmp_path) as (client, _store, _backend):
        profile = client.post("/api/v1/models/profiles", json=profile_body(url, api_key=SYNTHETIC_KEY)).json()
        response = client.post("/api/v1/models/profiles/probe", params={"profile_id": profile["id"]},
                               json=profile_body(url + "/chat/completions"))
        assert response.status_code == 200
        assert response.json()["connected"] is True
        assert requests and all(item["original_credential_received"] for item in requests)
        assert requests[0]["path"] == "/v1/models"


def test_changed_service_requires_explicit_key_and_does_not_activate_or_fall_back(tmp_path: Path):
    from dataclasses import replace

    with client_with_store(tmp_path) as (client, store, backend):
        original = profile_body("https://one.example.test/v1", api_key=SYNTHETIC_KEY)
        profile = client.post("/api/v1/models/profiles", json=original).json()
        assert client.post(f"/api/v1/models/profiles/{profile['id']}/activate",
                           json={"version": 1, "acknowledge_remote_data_transfer": True}).status_code == 200
        changed = profile_body("https://two.example.test/v1", expected_version=1)
        assert client.put(f"/api/v1/models/profiles/{profile['id']}", json=changed).status_code == 200
        settings = replace(client.app.state.settings, model_api_key="synthetic-environment-fallback-must-not-be-used")
        with client.app.state.session_factory() as session:
            active = settings_for_active_profile(session, settings, store)
            latest = settings_for_model_profile(session, settings, profile["id"], store)
            assert active.model_base_url == original["base_url"]
            assert active.model_api_key == SYNTHETIC_KEY
            assert latest.model_base_url == changed["base_url"]
            assert latest.model_api_key == ""
        updated = client.put(f"/api/v1/models/profiles/{profile['id']}", json={
            **changed, "expected_version": 2, "api_key": "synthetic-explicit-new-key"})
        assert updated.status_code == 200
        assert updated.json()["has_api_key"] is True
        assert len(backend.values) == 2
        with client.app.state.session_factory() as session:
            latest = settings_for_model_profile(session, settings, profile["id"], store)
            assert latest.model_api_key == "synthetic-explicit-new-key"


@pytest.mark.parametrize("base_url", ["http://[broken", "http://localhost:invalid/v1", "http://localhost:99999/v1"])
def test_malformed_endpoint_is_rejected_without_credential_side_effect(tmp_path: Path, base_url: str):
    with client_with_store(tmp_path) as (client, _store, backend):
        response = client.post("/api/v1/models/profiles", json=profile_body(base_url, api_key=SYNTHETIC_KEY))
        assert response.status_code == 422
        assert not backend.values
        assert SYNTHETIC_KEY not in response.text


def test_non_ascii_bearer_is_rejected_and_valid_credentials_keep_working(tmp_path: Path):
    from document_pipeline_api.services.integration_config import write_integration_config

    with client_with_store(tmp_path) as (client, _store, _backend):
        write_integration_config(tmp_path, {"read_token": SYNTHETIC_KEY})
        rejected = client.get("/api/integration/v1/tables", headers=[
            (b"Authorization", b"Bearer synthetic-invalid-\xff"),
        ])
        assert rejected.status_code == 401
        assert rejected.headers["WWW-Authenticate"] == "Bearer"
        valid = client.get("/api/integration/v1/tables", headers={
            "Authorization": f"Bearer {SYNTHETIC_KEY}",
        })
        assert valid.status_code == 200
        assert SYNTHETIC_KEY not in rejected.text


def test_non_ascii_configured_key_does_not_break_other_key(tmp_path: Path):
    from document_pipeline_api.services.integration_config import write_integration_config

    with client_with_store(tmp_path) as (client, _store, _backend):
        write_integration_config(tmp_path, {"write_token": "invalid-非ASCII" * 4,
                                           "read_token": SYNTHETIC_KEY})
        response = client.get("/api/integration/v1/tables", headers={
            "Authorization": f"Bearer {SYNTHETIC_KEY}",
        })
        assert response.status_code == 200


@pytest.mark.parametrize("token", ["非ASCII" * 10, "bad key" * 10, "bad\r\nkey" * 10])
def test_invalid_environment_key_fails_startup_without_echoing_key(token: str, tmp_path: Path):
    from document_pipeline_api.api.integration_auth import validate_integration_tokens

    with pytest.raises(RuntimeError) as error:
        validate_integration_tokens(Settings(database_url="sqlite:///:memory:",
                                             storage_dir=tmp_path / "uploads",
                                             integration_read_token=token))
    assert token not in str(error.value)
