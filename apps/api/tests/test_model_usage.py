import json

import httpx
from pydantic import BaseModel
import pytest
from sqlalchemy import func, select

from document_pipeline_api.models import AssistantMessage, AssistantRun, AssistantThread
from document_pipeline_api.models.dashboard import ModelUsageRecord
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.model_providers.openai_compatible import OpenAICompatibleProvider
from document_pipeline_api.model_providers.ollama import OllamaProvider
from document_pipeline_api.model_providers.base import ModelServiceError
from document_pipeline_api.services.model_usage import model_call
from test_dashboard_cards import make_client


class Result(BaseModel):
    value: int


class StubProvider:
    model_name = "synthetic"
    last_usage = {}


def test_usage_survives_business_rollback_and_readonly_close_without_secrets(tmp_path):
    client, _ = make_client(tmp_path)
    provider = StubProvider()
    with client:
        with client.app.state.session_factory() as session:
            provider.last_usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15,
                                   "prompt_tokens_details": {"cached_tokens": 4}, "prompt": "must-not-store"}
            with model_call(session, provider, "extraction", "openai_compatible"):
                pass
            session.commit()
            provider.last_usage = {}
            with pytest.raises(RuntimeError):
                with model_call(session, provider, "matching", "ollama"):
                    raise RuntimeError("synthetic failure with must-not-store")
            session.rollback()
            with model_call(session, provider, "template_generation", "ollama"):
                pass
        with client.app.state.session_factory() as session:
            rows = session.scalars(select(ModelUsageRecord)).all()
            assert len(rows) == 3
            assert sorted(row.status for row in rows) == ["completed", "completed", "failed"]
            assert "must-not-store" not in json.dumps([row.usage_json for row in rows])
        result = client.get("/api/v1/stats/overview?days=7").json()["model_usage"]
        assert result["calls"] == 3 and result["failed"] == 1
        assert result["calls_with_usage"] == 1
        assert result["calls_with_cache_usage"] == 1
        assert result["prompt_tokens"] == 12 and result["total_tokens"] == 15
        assert result["cache_hit_ratio"] == 4 / 12
        assert {item["purpose"] for item in result["by_purpose"]} == {"extraction", "matching", "template_generation"}
        unknown = next(item for item in result["by_purpose"] if item["purpose"] == "matching")
        assert unknown["prompt_tokens"] is None and unknown["cached_tokens"] is None


def test_openai_tracks_invalid_answer_tokens_but_resets_after_connection_failure(tmp_path):
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
        raise httpx.ConnectError("synthetic", request=request)

    provider = OpenAICompatibleProvider("http://synthetic/v1", "test", client=httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(ModelServiceError):
        provider.complete_text("synthetic", Result)
    assert provider.last_usage == {"prompt_tokens": 10, "completion_tokens": 2}
    with pytest.raises(ModelServiceError):
        provider.complete_text("synthetic", Result)
    assert provider.last_usage == {}
    provider.close()


def test_ollama_usage_is_normalized_without_inventing_cache_counts():
    payloads = [
        {"message": {"content": '{"value":1}'}, "prompt_eval_count": 4, "eval_count": 2},
        {"message": {"content": '{"value":2}'}}
    ]
    provider = OllamaProvider("http://synthetic", "test", client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payloads.pop(0)))))
    assert provider.complete_text("synthetic", Result).value == 1
    assert provider.last_usage == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
    assert provider.complete_text("synthetic", Result).value == 2
    assert provider.last_usage == {}
    provider.close()


def test_assistant_attempts_merge_without_counting_legacy_runs_as_calls(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        with client.app.state.session_factory() as session:
            session.add(AssistantThread(id="thread", title="synthetic"))
            session.flush()
            for index, usage in enumerate([
                {"calls": [{"status": "completed", "elapsed_ms": 20, "prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10, "cached_tokens": 0},
                           {"status": "interrupted", "elapsed_ms": 3}]},
                {"model_calls": 1, "prompt_tokens": 999},  # old aggregate is not individual evidence
                {"calls": [{"status": "running"}]}  # interrupted restart/restore, no duration
            ]):
                session.add(AssistantMessage(id=f"m{index}", thread_id="thread", role="assistant", position=index))
                session.flush()
                session.add(AssistantRun(id=f"run{index}", thread_id="thread", message_id=f"m{index}", profile_id="local", profile_version=1, model="synthetic", provider="ollama", status="interrupted" if index == 2 else "failed", usage_json=json.dumps(usage)))
            session.commit()
        summary = client.get("/api/v1/stats/overview").json()["model_usage"]
        assert summary["calls"] == 3 and summary["failed"] == 1
        assert summary["interrupted"] == 1 and summary["running"] == 0
        assert summary["elapsed_ms"] == 23
        assert summary["elapsed_sample_count"] == 2
        assert summary["average_elapsed_ms"] == 11.5
        assert summary["calls_with_usage"] == 1
        assert summary["prompt_tokens"] == 8
        assert summary["cached_tokens"] == 0 and summary["cache_hit_ratio"] == 0
        assert summary["legacy_unmeasured_runs"] == 1


def test_usage_is_not_lost_when_existing_business_write_is_rolled_back(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        with client.app.state.session_factory() as session:
            session.add(ModelUsageRecord(id="rolled-back", purpose="extraction", model="test", provider="ollama", status="completed", elapsed_ms=1, usage_json="{}", created_at=utc_now()))
            session.flush()
            with model_call(session, StubProvider(), "extraction", "ollama"):
                pass
            session.rollback()
        with client.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(ModelUsageRecord)) == 1
            assert session.get(ModelUsageRecord, "rolled-back") is None
