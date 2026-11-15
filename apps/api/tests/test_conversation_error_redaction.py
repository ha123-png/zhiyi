import json
import threading
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.model_providers.base import ModelServiceError
from document_pipeline_api.model_providers.conversation import ConversationProvider
from document_pipeline_api.model_providers.ollama import OllamaProvider
from document_pipeline_api.model_providers.openai_compatible import OpenAICompatibleProvider
from document_pipeline_api.models.assistant import AssistantEvent, AssistantRun
from test_assistant import client as ledger_fixture, select_profile, send

client = ledger_fixture


def credential(escaped=False, slash=False):
    # Deliberately lacks the prefixes and field names recognized by generic regexes.
    return "fixture-" + "opaque-" + ("back\\slash-" if escaped else "plain-") + ("slash/" if slash else "") + "sensitive-value"


def response_for(case, secret):
    if case == "http_plain":
        return httpx.Response(401, text="service refused " + secret)
    if case == "http_json":
        return httpx.Response(401, json={"message": "service refused " + secret})
    if case == "http_json_slash":
        body = json.dumps({"message": "service refused " + secret}).replace("/", r"\/")
        return httpx.Response(401, text=body)
    if case == "http_partial_json_slash":
        encoded = json.dumps(secret)[1:-1].replace("/", r"\/")
        return httpx.Response(401, text='{"message":"service refused ' + encoded + "x" * 6000)
    if case == "http_cross_boundary":
        return httpx.Response(401, text="x" * 1995 + secret)
    if case == "targeted":
        return httpx.Response(422, text="target rejected " + secret)
    error = "service refused " + secret
    if case in {"sse_nested", "ollama_nested"}:
        error = {"message": error, "info": ["synthetic"]}
    wire = json.dumps({"error": error}, ensure_ascii=False)
    if case.startswith("sse_"):
        wire = "data: " + wire + "\n\n"
    else:
        wire += "\n"
    return httpx.Response(200, text=wire)


def contains_credential(value, secret):
    if isinstance(value, str):
        encoded = json.dumps(secret, ensure_ascii=False)[1:-1]
        forms = (secret, encoded, encoded.replace("/", r"\/"), repr(secret)[1:-1])
        return any(form in value for form in forms)
    if isinstance(value, list):
        return any(contains_credential(item, secret) for item in value)
    if isinstance(value, dict):
        return any(contains_credential(key, secret) or contains_credential(item, secret)
                   for key, item in value.items())
    return False


@pytest.mark.parametrize("case,escaped", [
    ("http_plain", False), ("http_json", True), ("targeted", False),
    ("http_json_slash", False), ("http_partial_json_slash", False), ("http_cross_boundary", False),
    ("sse_string", False), ("sse_nested", True), ("ollama_nested", True),
])
def test_service_errors_never_expose_the_explicit_credential(tmp_path, case, escaped):
    secret = credential(escaped, case in {"http_json_slash", "http_partial_json_slash"})
    settings = Settings(
        database_url="sqlite://", storage_dir=tmp_path, model_api_key=secret,
        model_provider="ollama" if case.startswith("ollama") else "openai_compatible",
        model_base_url="http://fixture.invalid/v1",
    )
    provider = ConversationProvider(settings, threading.Event())
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(lambda request: response_for(case, secret)))
    try:
        try:
            choice = {"type": "function", "function": {"name": "finish"}} if case == "targeted" else None
            list(provider.stream([{"role": "user", "content": "synthetic"}], [], tool_choice=choice))
        except ValueError as error:
            leaked = contains_credential(str(error), secret)
            useful = "服务" in str(error)
            boundary_redacted = case != "http_cross_boundary" or "[凭证已隐藏]" in str(error)
        else:
            pytest.fail("expected the synthetic provider response to fail", pytrace=False)
    finally:
        provider.close()
    if leaked:
        pytest.fail("synthetic credential was exposed in a service error", pytrace=False)
    if not boundary_redacted:
        pytest.fail("the diagnostic was truncated before credential redaction", pytrace=False)
    if not useful:
        pytest.fail("the error no longer explains the service failure", pytrace=False)


@pytest.mark.parametrize("case", ["http_plain", "sse_nested"])
def test_service_errors_are_redacted_in_persisted_run_and_events(client, case):
    secret = credential(case == "sse_nested")

    class ErrorProvider(ConversationProvider):
        def __init__(self, settings, cancel):
            super().__init__(replace(settings, model_api_key=secret), cancel)
            self.client.close()
            self.client = httpx.Client(transport=httpx.MockTransport(lambda request: response_for(case, secret)))

    client.app.state.conversation_factory = ErrorProvider
    started = send(client, select_profile(client))
    if started.status_code != 200:
        pytest.fail("synthetic assistant run did not start", pytrace=False)
    payload = started.json()
    stream = client.get(f"/api/v1/assistant/runs/{payload['run_id']}/events")
    if stream.status_code != 200 or "run.completed" not in stream.text:
        pytest.fail("synthetic assistant event stream did not complete", pytrace=False)
    with client.app.state.session_factory() as session:
        run = session.get(AssistantRun, payload["run_id"])
        events = session.scalars(select(AssistantEvent).where(AssistantEvent.run_id == payload["run_id"])).all()
        if run is None or run.status != "failed" or not events:
            pytest.fail("synthetic assistant failure was not persisted", pytrace=False)
        persisted = [run.error, json.loads(run.usage_json), json.loads(run.events_json)]
        persisted.extend(json.loads(event.payload_json) for event in events)
    if contains_credential(persisted, secret) or contains_credential(stream.text, secret):
        pytest.fail("synthetic credential was exposed in persisted diagnostics or events", pytrace=False)


@pytest.mark.parametrize("kind", ["openai", "ollama"])
def test_extraction_provider_fallback_error_body_never_exposes_credential(kind):
    secret = credential(escaped=True, slash=True)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"details": "service refused " + secret}})
    )
    provider_type = OpenAICompatibleProvider if kind == "openai" else OllamaProvider
    provider = provider_type(
        "http://fixture.invalid/v1", "fixture", api_key=secret,
        client=httpx.Client(transport=transport),
    )
    try:
        try:
            provider.available_models()
        except ModelServiceError as error:
            leaked = contains_credential(str(error), secret)
            useful = "服务端返回" in str(error)
        else:
            pytest.fail("expected the synthetic provider response to fail", pytrace=False)
    finally:
        provider.close()
    if leaked:
        pytest.fail("synthetic credential was exposed in an extraction service error", pytrace=False)
    if not useful:
        pytest.fail("the extraction error lost its service detail", pytrace=False)
