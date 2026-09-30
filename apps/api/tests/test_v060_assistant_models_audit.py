"""Targeted regressions from the v0.6 external assistant/model audit."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import (
    AssistantMessage,
    AssistantRun,
    AssistantThread,
    DataRowRecord,
    DataTableRecord,
    ModelProfileRecord,
    ModelProfileVersionRecord,
    ModelRuntimeStateRecord,
)
from document_pipeline_api.schemas.templates import TemplateCreate, TemplateRead
from document_pipeline_api.services.assistant_analysis import (
    AnalysisRequest,
    Filter,
    analyze,
    matches,
)
from document_pipeline_api.services.assistant_memory import model_result
from document_pipeline_api.services.assistant_operations import Plan, preview_plan
from document_pipeline_api.services.assistant_tools import Context, execute_tool
from document_pipeline_api.services.assistant_events import recover_interrupted_runs
from document_pipeline_api.services.dashboard_usage import usage_summary
from document_pipeline_api.services.assistant_usage import tracked_stream
from document_pipeline_api.services.template_processing import (
    build_template_extraction_model,
    build_template_extraction_prompt,
)
from test_assistant import client as ledger_fixture, ScriptedProvider, select_profile, send, finish


client = ledger_fixture


@pytest.mark.parametrize(
    "operations",
    [
        [
            {"kind": "delete_rows", "table_id": "ledger", "row_ids": [1]},
            {
                "kind": "increment_rows",
                "table_id": "ledger",
                "row_ids": [1],
                "changes": {"amount": 1},
            },
        ],
        [
            {
                "kind": "increment_rows",
                "table_id": "ledger",
                "row_ids": [1],
                "changes": {"amount": 1},
            },
            {"kind": "delete_rows", "table_id": "ledger", "row_ids": [1]},
        ],
    ],
)
def test_ask_01_delete_and_increment_of_same_row_are_rejected_in_preview(client, operations):
    with client.app.state.session_factory() as session:
        before = [
            (row.id, row.row_version, row.row_json)
            for row in session.scalars(
                select(DataRowRecord)
                .where(DataRowRecord.table_id == "ledger")
                .order_by(DataRowRecord.id)
            )
        ]
    operation = Plan(
        title="冲突操作",
        operations=operations,
    )
    with client.app.state.session_factory() as session, pytest.raises(HTTPException) as error:
        preview_plan(
            session,
            Context(table_ids=["ledger"], mode="delegate", capabilities=["data"]),
            operation,
        )
    assert error.value.status_code == 422
    with client.app.state.session_factory() as session:
        after = [
            (row.id, row.row_version, row.row_json)
            for row in session.scalars(
                select(DataRowRecord)
                .where(DataRowRecord.table_id == "ledger")
                .order_by(DataRowRecord.id)
            )
        ]
    assert after == before


@pytest.mark.parametrize("budget", [1500, 6000])
def test_ask_02_budgeted_tool_result_page_continues_original_identity(client, budget):
    original = {"content": "0123456789甲😀" * 1500}
    artifacts = {"_tool_results": {"original": original}}
    with client.app.state.session_factory() as session:
        page = execute_tool(
            session,
            Context(table_id="ledger"),
            "read_tool_result",
            {"tool_id": "original"},
            artifacts,
        )
        artifacts["_tool_results"]["reader-1"] = page
        small = model_result(page, "reader-1", budget)
        assert small["tool_id"] == "original"
        continuation = execute_tool(
            session,
            Context(table_id="ledger"),
            "read_tool_result",
            {"tool_id": small["tool_id"], "offset": small["next_offset"]},
            artifacts,
        )
    serialized = json.dumps(original, ensure_ascii=False, default=str)
    assert continuation["total_characters"] == len(serialized)
    assert continuation["text"] == serialized[small["next_offset"] : small["next_offset"] + 4000]


def test_ask_03_internal_refresh_does_not_inflate_legacy_model_calls(client):
    with client.app.state.session_factory() as session:
        thread = AssistantThread(id="usage-coverage", title="合成用量")
        session.add(thread)
        session.flush()
        legacy_message = AssistantMessage(
            id="legacy-message", thread_id=thread.id, role="assistant", position=0
        )
        internal_message = AssistantMessage(
            id="internal-message", thread_id=thread.id, role="assistant", position=1
        )
        session.add_all([legacy_message, internal_message])
        session.flush()
        session.add_all(
            [
                AssistantRun(
                    id="legacy-run",
                    thread_id=thread.id,
                    message_id=legacy_message.id,
                    profile_id="legacy",
                    profile_version=1,
                    model="old model",
                    provider="lm_studio",
                    usage_json="{}",
                ),
                AssistantRun(
                    id="internal-run",
                    thread_id=thread.id,
                    message_id=internal_message.id,
                    profile_id="legacy",
                    profile_version=1,
                    model="知意统计",
                    provider="zhiyi_internal",
                    usage_json="{}",
                ),
            ]
        )
        session.commit()
    response = client.get("/api/v1/stats/overview")
    assert response.status_code == 200, response.text
    usage = response.json()["model_usage"]
    assert usage["calls"] == 0
    assert usage["legacy_unmeasured_runs"] == 1
    with client.app.state.session_factory() as session:
        assert usage_summary(session, None)["legacy_unmeasured_runs"] == 1


def _make_zero_table(client):
    with client.app.state.session_factory() as session:
        session.add(
            DataTableRecord(
                id="zero-table",
                name="合成零值表",
                template_key="audit:stats-zero",
                template_version="1",
                document_kind="custom",
                columns_json=json.dumps(
                    [
                        {
                            "key": "quantity",
                            "label": "数量",
                            "section": "item",
                            "value_type": "number",
                        },
                    ]
                ),
            )
        )
        session.commit()
        session.add_all(
            [
                DataRowRecord(table_id="zero-table", item_index=0, row_json='{"quantity":0}'),
                DataRowRecord(table_id="zero-table", item_index=1, row_json='{"quantity":10}'),
            ]
        )
        session.commit()


def test_stats_02_assistant_sse_tool_chain_contains_zero(client):
    _make_zero_table(client)
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [
        [{"type": "tool", "id": "options", "name": "enable_analysis_options", "arguments": {}}],
        [
            {
                "type": "tool",
                "id": "analysis",
                "name": "analyze_data_table",
                "arguments": {
                    "table_id": "zero-table",
                    "filters": [{"field": "quantity", "op": "contains", "value": "0"}],
                    "metrics": [{"op": "count"}],
                },
            }
        ],
    ]
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(
        client,
        send(
            client,
            select_profile(client),
            text="统计数量包含0的记录",
            context={"table_id": "zero-table"},
        ),
    )
    result = next(
        tool["result"] for tool in detail["tools"] if tool["name"] == "analyze_data_table"
    )
    assert result["source"]["row_count"] == 2
    assert result["totals"]["count:*"] == 2


def test_stats_02_contains_defines_zero_false_null_and_empty_string_explicitly():
    contains_zero = Filter(field="value", op="contains", value="0")
    contains_false = Filter(field="value", op="contains", value="false")
    contains_empty = Filter(field="value", op="contains", value="")
    assert matches(0, contains_zero)
    assert matches("10", contains_zero)
    assert not matches(False, contains_zero)
    assert matches(False, contains_false)
    assert not matches(None, contains_zero)
    assert not matches("", contains_zero)
    # An empty contains needle is deliberately a no-op and therefore matches all values.
    assert matches(None, contains_empty)
    assert matches("", contains_empty)


def test_stats_03_invalid_rows_are_counted_once_across_metrics(client):
    with client.app.state.session_factory() as session:
        session.execute(DataRowRecord.__table__.delete().where(DataRowRecord.table_id == "ledger"))
        for index, value in enumerate(["bad-a", "bad-b", "bad-c", "2"]):
            session.add(
                DataRowRecord(
                    table_id="ledger", item_index=index, row_json=json.dumps({"amount": value})
                )
            )
        session.commit()
    with client.app.state.session_factory() as session:
        multiple = analyze(
            session,
            AnalysisRequest(
                table_id="ledger",
                metrics=[
                    {"op": "sum", "field": "amount"},
                    {"op": "avg", "field": "amount"},
                    {"op": "min", "field": "amount"},
                    {"op": "max", "field": "amount"},
                ],
            ),
        )
        single = analyze(
            session, AnalysisRequest(table_id="ledger", metrics=[{"op": "sum", "field": "amount"}])
        )
    multiple_warning = next(w for w in multiple["warnings"] if "无法作为数字" in w)
    single_warning = next(w for w in single["warnings"] if "无法作为数字" in w)
    assert "3 个非空值" in multiple_warning
    assert "3 个非空值" in single_warning
    assert multiple["totals"] == {
        "sum:amount": 2.0,
        "avg:amount": 2.0,
        "min:amount": 2.0,
        "max:amount": 2.0,
    }
    assert single["totals"]["sum:amount"] == 2.0


@pytest.mark.parametrize("delay", [0.0, 0.9])
def test_ask_04_forced_child_exit_keeps_text_and_provider_usage_once(model_client, delay):
    run_id = f"crash-usage-{str(delay).replace('.', '-')}"
    thread_id = f"thread-{run_id}"
    message_id = f"message-{run_id}"
    with model_client.app.state.session_factory() as session:
        session.add(AssistantThread(id=thread_id, title="崩溃恢复合成测试"))
        session.flush()
        session.add(
            AssistantMessage(
                id=message_id,
                thread_id=thread_id,
                role="assistant",
                position=0,
                parts_json='[{"type":"text","text":""}]',
            )
        )
        session.flush()
        session.add(
            AssistantRun(
                id=run_id,
                thread_id=thread_id,
                message_id=message_id,
                profile_id="synthetic",
                profile_version=1,
                model="protocol-fixture",
                provider="synthetic",
                status="running",
                stream_version=1,
            )
        )
        session.commit()
        database_url = str(session.get_bind().url)

    child = r"""
import os, sys, time, threading
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from document_pipeline_api.services.assistant_events import EventWriter
from document_pipeline_api.services.assistant_memory import encode
from document_pipeline_api.services.assistant_usage import tracked_stream

url, run_id, delay = sys.argv[1], sys.argv[2], float(sys.argv[3])
engine = create_engine(url, connect_args={"check_same_thread": False})
factory = sessionmaker(engine, expire_on_commit=False)
parts = [{"type": "text", "text": ""}]
usage = {}
writer = EventWriter(factory, run_id, parts, usage, threading.Event())
writer.emit("status", message="synthetic stream started")

class Provider:
    def stream(self, messages, specs):
        yield {"type": "usage", "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}}
        yield {"type": "text", "text": "persisted synthetic answer"}

for event in tracked_stream(usage, Provider(), [], [], on_checkpoint=writer.checkpoint):
    if event["type"] == "text":
        parts[0]["text"] += event["text"]
        writer.emit("text.delta", text=event["text"])
        if delay:
            time.sleep(delay)
            writer.emit("status", message="after delayed text checkpoint")
        os._exit(73)
"""
    repo = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    source = str(repo / "apps" / "api" / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c", child, database_url, run_id, str(delay)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 73, completed.stderr

    with model_client.app.state.session_factory() as session:
        recover_interrupted_runs(session)
    with model_client.app.state.session_factory() as session:
        run = session.get(AssistantRun, run_id)
        message = session.get(AssistantMessage, message_id)
        saved_usage = json.loads(run.usage_json)
        assert run.status == "interrupted"
        assert json.loads(message.parts_json)[0]["text"] == "persisted synthetic answer"
        assert len(saved_usage["calls"]) == 1
        assert saved_usage["calls"][0]["status"] == "interrupted"
        assert (
            saved_usage["prompt_tokens"],
            saved_usage["completion_tokens"],
            saved_usage["total_tokens"],
        ) == (20, 4, 24)
        recovery_usage = usage_summary(session, None)
        assert (
            recovery_usage["calls"],
            recovery_usage["prompt_tokens"],
            recovery_usage["completion_tokens"],
            recovery_usage["total_tokens"],
        ) == (1, 20, 4, 24)
        parts_after_first_recovery = message.parts_json
        usage_after_first_recovery = run.usage_json
        recover_interrupted_runs(session)
        session.refresh(message)
        session.refresh(run)
        assert message.parts_json == parts_after_first_recovery
        assert run.usage_json == usage_after_first_recovery


def test_ask_04_repeated_usage_snapshots_failure_and_retry_are_accounted_once():
    class SnapshotThenFail:
        def stream(self, messages, specs):
            yield {
                "type": "usage",
                "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
            }
            yield {
                "type": "usage",
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            }
            raise RuntimeError("synthetic provider interruption")

    class Retry:
        def stream(self, messages, specs):
            yield {
                "type": "usage",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
            yield {
                "type": "usage",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
            yield {"type": "text", "text": "retry result"}

    usage = {}
    checkpoints = []

    def checkpoint():
        checkpoints.append(json.loads(json.dumps(usage)))

    with pytest.raises(RuntimeError, match="synthetic provider interruption"):
        list(tracked_stream(usage, SnapshotThenFail(), [], [], on_checkpoint=checkpoint))
    assert usage["calls"][0]["status"] == "interrupted"
    assert (
        usage["calls"][0]["prompt_tokens"],
        usage["calls"][0]["completion_tokens"],
        usage["calls"][0]["total_tokens"],
    ) == (20, 4, 24)

    list(tracked_stream(usage, Retry(), [], [], on_checkpoint=checkpoint))
    assert [call["status"] for call in usage["calls"]] == ["interrupted", "completed"]
    assert len(usage["calls"]) == 2
    assert (usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]) == (
        30,
        6,
        36,
    )
    assert all(snapshot["model_calls"] == len(snapshot["calls"]) for snapshot in checkpoints)


@pytest.fixture
def model_client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'model-races.db'}",
        storage_dir=tmp_path / "model-uploads",
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as test_client:
        yield test_client


def _create_local_profile(client, name="race"):
    response = client.post(
        "/api/v1/models/profiles",
        json={
            "name": name,
            "provider": "lm_studio",
            "base_url": "http://127.0.0.1:1234/v1",
            "model_name": "synthetic-model",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _coordinate_model_profile_writers(monkeypatch):
    import document_pipeline_api.services.model_profiles as model_profiles

    barrier = Barrier(2)
    begin = model_profiles.begin_write_snapshot

    def synchronized(session):
        # Both actual HTTP requests reach the service before opening their
        # SQLite write transaction, making the contested read window repeatable.
        barrier.wait(timeout=10)
        return begin(session)

    monkeypatch.setattr(model_profiles, "begin_write_snapshot", synchronized)


def test_model_01_concurrent_same_version_saves_return_one_success_and_one_conflict(
    model_client, monkeypatch
):
    profile = _create_local_profile(model_client)
    _coordinate_model_profile_writers(monkeypatch)
    barrier = Barrier(2)
    bodies = [
        {
            "name": "winner-a",
            "provider": "lm_studio",
            "base_url": "http://127.0.0.1:1234/v1",
            "model_name": "synthetic-a",
            "expected_version": 1,
        },
        {
            "name": "winner-b",
            "provider": "lm_studio",
            "base_url": "http://127.0.0.1:1234/v1",
            "model_name": "synthetic-b",
            "expected_version": 1,
        },
    ]

    def request(index):
        return model_client.put(f"/api/v1/models/profiles/{profile['id']}", json=bodies[index])

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(lambda i=i: (barrier.wait(timeout=10), request(i))[1]) for i in range(2)
        ]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409], [
        r.text for r in responses
    ]
    with model_client.app.state.session_factory() as session:
        saved = session.get(ModelProfileRecord, profile["id"])
        versions = session.scalars(
            select(ModelProfileVersionRecord)
            .where(
                ModelProfileVersionRecord.profile_id == profile["id"],
            )
            .order_by(ModelProfileVersionRecord.version)
        ).all()
        assert saved.current_version == 2
        assert [version.version for version in versions] == [1, 2]
        assert versions[1].name in {"winner-a", "winner-b"}


def test_model_02_concurrent_activate_archive_never_leaves_archived_active_profile(
    model_client, monkeypatch
):
    profile = _create_local_profile(model_client, "activation-race")
    _coordinate_model_profile_writers(monkeypatch)
    barrier = Barrier(2)

    def activate():
        barrier.wait(timeout=10)
        return model_client.post(
            f"/api/v1/models/profiles/{profile['id']}/activate", json={"version": 1}
        )

    def archive():
        barrier.wait(timeout=10)
        return model_client.post(f"/api/v1/models/profiles/{profile['id']}/archive")

    with ThreadPoolExecutor(max_workers=2) as executor:
        activation = executor.submit(activate)
        archiving = executor.submit(archive)
        activation_response = activation.result(timeout=20)
        archive_response = archiving.result(timeout=20)
    assert sorted([activation_response.status_code, archive_response.status_code]) == [200, 409], (
        activation_response.text,
        archive_response.text,
    )
    with model_client.app.state.session_factory() as session:
        stored = session.get(ModelProfileRecord, profile["id"])
        runtime = session.get(ModelRuntimeStateRecord, 1)
        assert not stored.is_archived or runtime.active_profile_id != profile["id"]


def _template_payload(name="  有效模板  ", label="  开票日期  ", value_type="date"):
    return {
        "name": name,
        "fields": [{"key": "date", "label": label, "section": "header", "value_type": value_type}],
    }


@pytest.mark.parametrize(
    "payload",
    [
        _template_payload(name="   "),
        _template_payload(label=" \t\u2003 "),
    ],
)
def test_template_01_api_rejects_whitespace_only_import_names_and_labels(client, payload):
    response = client.post("/api/v1/templates", json=payload)
    assert response.status_code == 422, response.text
    names = [template["name"] for template in client.get("/api/v1/templates").json()]
    assert "" not in names and "   " not in names


def test_template_01_schema_strips_edges_and_keeps_meaningful_unicode(client):
    payload = _template_payload("  门店发票  ", "  开票日期  ", "date")
    body = TemplateCreate.model_validate_json(json.dumps(payload, ensure_ascii=False))
    assert body.name == "门店发票"
    assert body.fields[0].label == "开票日期"
    response = client.post("/api/v1/templates", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "门店发票"
    assert response.json()["fields"][0]["label"] == "开票日期"


def test_template_02_date_schema_prompt_and_values_match_storage_contract():
    template = TemplateRead.model_validate(
        {
            "id": "synthetic-date",
            "version": 1,
            "is_system": False,
            "is_active": True,
            "builtin_key": None,
            "source_template_id": None,
            "name": "日期模板",
            "description": "",
            "fields": [
                {
                    "key": "issue_date",
                    "label": "开票日期",
                    "section": "header",
                    "value_type": "date",
                }
            ],
            "created_at": "2026-09-30T00:00:00Z",
            "updated_at": "2026-09-30T00:00:00Z",
        }
    )
    extraction = build_template_extraction_model(template)
    schema = extraction.model_json_schema()
    prompt = build_template_extraction_prompt(template)
    assert '"format": "date"' in json.dumps(schema)
    assert "YYYY-MM-DD" in prompt
    good = extraction.model_validate({"header": {"issue_date": "2026-09-30"}, "items": []})
    assert good.model_dump()["header"]["issue_date"] == "2026-09-30"
    with pytest.raises(ValidationError):
        extraction.model_validate({"header": {"issue_date": "2026年9月30日"}, "items": []})
