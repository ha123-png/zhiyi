import httpx
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.schemas.model_profiles import ModelProfileBody
from document_pipeline_api.services.model_profiles import probe_model_profile


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
@pytest.mark.parametrize("requested,expected", [(7, 7), (180, 30)])
def test_profile_probe_applies_bounded_timeout_to_real_requests(tmp_path, monkeypatch, provider, requested, expected):
    calls = []
    clients = []
    real_client = httpx.Client

    def respond(request):
        calls.append((request.url.path, request.extensions["timeout"]))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "synthetic"}]})
        if request.url.path.endswith("/tags"):
            return httpx.Response(200, json={"models": [{"model": "synthetic"}]})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "message": {"content": '{"ok":true}'}, "done": True,
        })

    def client_factory(*args, **kwargs):
        client = real_client(*args, **kwargs, transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", client_factory)
    result = probe_model_profile(
        Settings(database_url="sqlite:///:memory:", storage_dir=tmp_path / "uploads"),
        ModelProfileBody(name="Synthetic", provider=provider, base_url="https://synthetic.invalid/v1",
                         model_name="synthetic", timeout_seconds=requested),
    )

    assert result.connected and result.multimodal is True
    assert len(calls) == 2
    assert [timeout["read"] for _, timeout in calls] == [expected, expected]
    assert clients and all(client.is_closed for client in clients)
