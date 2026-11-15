"""Incremental integrity cases missing from the 2026-09-30 audit matrix."""

import json
import threading
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from test_assistant import (
    ScriptedProvider,
    client as ledger_fixture,
    finish,
    select_profile,
    send,
)
from document_pipeline_api.models import (
    AssistantMessage,
    AssistantRun,
    AssistantThread,
    AssistantToolCall,
    DataRowRecord,
    DataTableRecord,
    ExtractionRecord,
    TaskRecord,
    TemplateRecord,
    TemplateVersionRecord,
)
from document_pipeline_api.models.assistant import AssistantEvent
from document_pipeline_api.services.assistant_analysis import AnalysisRequest, Filter, analyze
from document_pipeline_api.services.assistant_tools import Context, check_context, execute_tool
from document_pipeline_api.services.current_data import initialize_current, revise_current

client = ledger_fixture
MAX_SAFE_INTEGER = 9007199254740991


@pytest.mark.parametrize("value", [0, 42, MAX_SAFE_INTEGER, MAX_SAFE_INTEGER + 2, -MAX_SAFE_INTEGER - 2])
def test_filter_keeps_integer_tokens_before_matching(client, value):
    condition = Filter(field="amount", value=value)
    assert type(condition.value) is int and condition.value == value
    with client.app.state.session_factory() as session:
        session.get(DataRowRecord, 1).row_json = json.dumps({"amount": value})
        session.get(DataRowRecord, 2).row_json = json.dumps({"amount": value - 1})
        session.commit()
        result = analyze(session, AnalysisRequest(table_id="ledger", records=True, filters=[condition]))
    assert [row["row_id"] for row in result["rows"]] == [1]


@pytest.mark.parametrize("value", [True, False])
def test_boolean_filter_stays_boolean(value):
    assert Filter(field="value", value=value).value is value


@pytest.mark.parametrize("operation", ["sum", "avg", "min", "max"])
@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1])
def test_analysis_rejects_integer_results_beyond_browser_precision(client, operation, value):
    with client.app.state.session_factory() as session:
        session.get(DataRowRecord, 1).row_json = json.dumps({"amount": value})
        session.commit()
        with pytest.raises(HTTPException) as raised:
            analyze(session, AnalysisRequest(table_id="ledger", row_ids=[1],
                metrics=[{"op": operation, "field": "amount"}]))
    assert raised.value.status_code == 422
    assert "精度" in raised.value.detail and "JSON" in raised.value.detail


@pytest.mark.parametrize("operation", ["sum", "avg", "min", "max"])
@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER])
def test_analysis_accepts_exact_integer_boundary(client, operation, value):
    with client.app.state.session_factory() as session:
        session.get(DataRowRecord, 1).row_json = json.dumps({"amount": value})
        session.commit()
        result = analyze(session, AnalysisRequest(table_id="ledger", row_ids=[1],
            metrics=[{"op": operation, "field": "amount"}]))
    assert result["totals"][f"{operation}:amount"] == value


def test_analysis_keeps_ordinary_decimal_sums_and_repeating_averages(client):
    with client.app.state.session_factory() as session:
        for identifier, value in ((1, 0.1), (2, 0.2), (3, 0.0)):
            session.get(DataRowRecord, identifier).row_json = json.dumps({"amount": value})
        session.commit()
        result = analyze(session, AnalysisRequest(table_id="ledger", row_ids=[1, 2],
            metrics=[{"op": "sum", "field": "amount"}]))
        assert result["totals"]["sum:amount"] == 0.3
        result = analyze(session, AnalysisRequest(table_id="ledger", row_ids=[1, 2, 3],
            metrics=[{"op": "avg", "field": "amount"}]))
        assert result["totals"]["avg:amount"] == pytest.approx(0.1)
        session.get(DataRowRecord, 1).row_json = json.dumps({"amount": 1})
        session.get(DataRowRecord, 2).row_json = json.dumps({"amount": 0})
        session.commit()
        result = analyze(session, AnalysisRequest(table_id="ledger", row_ids=[1, 2, 3],
            metrics=[{"op": "avg", "field": "amount"}]))
        assert result["totals"]["avg:amount"] == pytest.approx(1 / 3)


def test_analysis_does_not_round_an_exact_decimal_sum_into_a_different_value(client):
    with client.app.state.session_factory() as session:
        session.get(DataRowRecord, 1).row_json = json.dumps({"amount": "1.0000000000000001"})
        session.commit()
        with pytest.raises(HTTPException) as raised:
            analyze(session, AnalysisRequest(table_id="ledger", row_ids=[1],
                metrics=[{"op": "sum", "field": "amount"}]))
    assert raised.value.status_code == 422 and "精度" in raised.value.detail


def test_analysis_checks_derived_sum_and_keeps_small_remainder_after_cancellation(client):
    with client.app.state.session_factory() as session:
        session.get(DataRowRecord, 1).row_json = json.dumps({"amount": MAX_SAFE_INTEGER})
        session.get(DataRowRecord, 2).row_json = json.dumps({"amount": 1})
        session.commit()
        with pytest.raises(HTTPException) as raised:
            analyze(session, AnalysisRequest(table_id="ledger", row_ids=[1, 2],
                metrics=[{"op": "sum", "field": "amount"}]))
        assert raised.value.status_code == 422
        for identifier, value in ((1, 10**40), (2, 1), (3, -(10**40))):
            session.get(DataRowRecord, identifier).row_json = json.dumps({"amount": value})
        session.commit()
        result = analyze(session, AnalysisRequest(table_id="ledger", row_ids=[1, 2, 3],
            metrics=[{"op": "sum", "field": "amount"}]))
        assert result["totals"]["sum:amount"] == 1


def test_assistant_rest_and_sse_keep_unsafe_integers_without_rewriting_export_snapshot(client):
    values = {"large": MAX_SAFE_INTEGER + 2, "negative": -MAX_SAFE_INTEGER - 2,
              "small": 42, "decimal": 0.25, "flag": True, "code": "00123"}
    snapshot = {"kind": "export", "analysis": {
        "columns": [{"key": key, "label": key} for key in values],
        "rows": [{"values": values}],
        "source": {"table_name": "合成快照", "generated_at": "2026-10-30", "request": {}},
    }}
    part = {"type": "tool", "id": "precision-tool", "name": "prepare_export", "result": snapshot}
    with client.app.state.session_factory() as session:
        session.add(AssistantThread(id="precision-thread", title="合成精度回读"))
        session.flush()
        session.add(AssistantMessage(id="precision-message", thread_id="precision-thread", role="assistant",
            position=0, parts_json=json.dumps([part])))
        session.flush()
        session.add(AssistantRun(id="precision-run", thread_id="precision-thread", message_id="precision-message",
            profile_id="synthetic", profile_version=1, model="synthetic", provider="synthetic",
            status="completed", stream_version=1))
        session.flush()
        session.add(AssistantToolCall(id="precision-tool", run_id="precision-run", name="prepare_export",
            arguments_json="{}", result_json=json.dumps(snapshot), status="completed"))
        session.add(AssistantEvent(run_id="precision-run", sequence=1,
            payload_json=json.dumps({"type": "tool.completed", "result": snapshot})))
        session.commit()
    expected = {**values, "large": str(MAX_SAFE_INTEGER + 2), "negative": str(-MAX_SAFE_INTEGER - 2)}
    detail = client.get("/api/v1/assistant/threads/precision-thread").json()
    assert detail["tools"][0]["result"]["analysis"]["rows"][0]["values"] == expected
    assert detail["messages"][0]["parts"][0]["result"]["analysis"]["rows"][0]["values"] == expected
    stream = client.get("/api/v1/assistant/runs/precision-run/events").text
    event = json.loads(next(line[6:] for line in stream.splitlines() if line.startswith("data: ")))
    assert event["result"]["analysis"]["rows"][0]["values"] == expected
    repeated = client.post("/api/v1/assistant/tools/precision-tool/decision", json={"approve": True}).json()
    assert repeated["result"]["analysis"]["rows"][0]["values"] == expected
    with client.app.state.session_factory() as session:
        assert json.loads(session.get(AssistantToolCall, "precision-tool").result_json) == snapshot
    assert client.get("/api/v1/assistant/tools/precision-tool/export").status_code == 422


def add_selected_file(session, *, ready):
    session.add(TemplateRecord(id="polish-synthetic-template"))
    session.flush()
    session.add(TemplateVersionRecord(template_id="polish-synthetic-template", version=1,
        name="合成所选文件模板", fields_json=session.get(DataTableRecord, "ledger").columns_json))
    task = TaskRecord(id="selected-file", filename="合成所选文件.txt", content_type="text/plain",
        size_bytes=1, sha256="synthetic-selected", storage_path="selected.txt", status="processing",
        template_mode="custom", template_id="polish-synthetic-template", template_version=1)
    session.add(task)
    session.flush()
    if ready:
        complete_selected_file(session)
    session.commit()


def complete_selected_file(session):
    schema = json.loads(session.get(DataTableRecord, "ledger").columns_json)
    initialize_current(session, task_id="selected-file", table_id="ledger",
        header={"vendor": "仅所选文件", "total": "00123"}, items=[{"amount": 7}], schema=schema)
    session.add(ExtractionRecord(task_id="selected-file", document_kind="custom", model_name="synthetic",
        template_id="polish-synthetic-template", template_version=1,
        prompt_version="synthetic", elapsed_seconds=0, result_json=json.dumps({
            "header": {"vendor": "仅所选文件", "total": "00123"}, "items": [{"amount": 7}]}),
        output_schema_json=json.dumps(schema), validation_json="[]"))
    session.get(TaskRecord, "selected-file").status = "completed"


@pytest.mark.parametrize("ready_before_send", [False, True])
def test_same_file_request_can_retry_after_table_resolution_or_extraction_completion(client, ready_before_send):
    ScriptedProvider.plan = []
    client.app.state.conversation_factory = ScriptedProvider
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=ready_before_send)
    profile = select_profile(client)
    request_id = str(uuid4())
    original = send(client, profile, request_id=request_id, context={"task_id": "selected-file"})
    finish(client, original)
    if not ready_before_send:
        with client.app.state.session_factory() as session:
            complete_selected_file(session)
            session.commit()
    repeated = send(client, profile, request_id=request_id, context={"task_id": "selected-file"})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == original.json()
    assert send(client, profile, request_id=request_id, context={"task_id": "selected-file"},
                text="改过的内容").status_code == 409
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssistantRun)) == 1


def test_file_finishing_between_tool_reads_keeps_selection_and_saved_snapshot(client):
    waiting, continue_read = threading.Event(), threading.Event()

    class FinishingProvider(ScriptedProvider):
        def stream(self, messages, tools):
            if self.step == 1:
                waiting.set()
                assert continue_read.wait(10), "Synthetic completion was not released"
            yield from super().stream(messages, tools)

    FinishingProvider.plan = [[{"type": "tool", "id": "before", "name": "read_resource",
        "arguments": {"kind": "task", "id": "selected-file"}}],
        [{"type": "tool", "id": "after", "name": "read_resource",
          "arguments": {"kind": "task", "id": "selected-file"}}]]
    client.app.state.conversation_factory = FinishingProvider
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=False)
    started = send(client, select_profile(client), context={"task_id": "selected-file"})
    try:
        assert waiting.wait(10)
        with client.app.state.session_factory() as session:
            complete_selected_file(session)
            session.commit()
    finally:
        continue_read.set()
    detail = finish(client, started)
    results = [call["result"] for call in detail["tools"]]
    before = next(result for result in results if result["status"] == "processing")
    after = next(call for call in detail["tools"] if call["result"]["status"] == "completed")
    assert "extraction" not in before
    saved = after["result"]["extraction"]
    assert saved["result"] == {"header": {"vendor": "仅所选文件", "total": "00123"}, "items": [{"amount": 7}]}
    assert saved["item_count"] == 1
    with client.app.state.session_factory() as session:
        context = check_context(session, Context(task_id="selected-file"))
        rows = execute_tool(session, context, "read_data_rows", {"table_id": "ledger"}, {})
        assert len(rows["rows"]) == 1 and rows["rows"][0]["task_id"] == "selected-file"
        revise_current(session, "selected-file", expected_version=0, header_changes={"vendor": "后续人工修订"})
        session.commit()
    reloaded = client.get(f"/api/v1/assistant/threads/{started.json()['thread_id']}").json()
    assert next(call["result"]["extraction"] for call in reloaded["tools"] if call["id"] == after["id"]) == saved
