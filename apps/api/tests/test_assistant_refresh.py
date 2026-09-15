"""Saved queries are executable data, never prompts reconstructed by a model."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from test_assistant import client as ledger_fixture
from document_pipeline_api.models import DataRowRecord, DataTableRecord
from document_pipeline_api.models.assistant import AssistantThread, AssistantMessage, AssistantRun, AssistantToolCall
from document_pipeline_api.services.assistant_tools import Context, execute_tool

client = ledger_fixture


def saved_result(client, query=None, chart=None, context=None):
    context = Context.model_validate(context or {"workspace": True})
    with client.app.state.session_factory() as session:
        result = execute_tool(session, context, "analyze_data_table", query or {
            "table_id": "ledger", "dimensions": ["vendor"], "metrics": [{"op": "sum", "field": "amount"}],
        }, artifacts := {})
        if chart:
            result = execute_tool(session, context, "render_chart", {
                "analysis_id": result["analysis_id"], **chart,
            }, artifacts)
        thread = AssistantThread(id=str(uuid4()), title="原统计", profile_id="cloud")
        session.add(thread)
        session.flush()
        message = AssistantMessage(id=str(uuid4()), thread_id=thread.id, role="assistant", position=0,
            context_json=context.model_dump_json())
        session.add(message)
        session.flush()
        run = AssistantRun(id=str(uuid4()), thread_id=thread.id, message_id=message.id, profile_id="cloud",
            profile_version=2, model="original-cloud-model", provider="openai_compatible", status="completed")
        session.add(run)
        session.flush()
        call = AssistantToolCall(id=str(uuid4()), run_id=run.id,
            name="render_chart" if chart else "analyze_data_table", arguments_json="{}", result_json=json.dumps(result))
        session.add(call)
        message.parts_json = json.dumps([{"type": "tool", "id": call.id, "name": call.name}])
        session.commit()
        return thread.id, call.id, result


def refresh(client, saved, context=None, request_id=None):
    return client.post(f"/api/v1/assistant/tools/{saved[1]}/refresh-analysis", json={
        "thread_id": saved[0], "request_id": request_id or str(uuid4()),
        "context": context if context is not None else {"workspace": True},
    })


def fresh_tool(client, saved):
    detail = client.get(f"/api/v1/assistant/threads/{saved[0]}").json()
    return detail, next(t["result"] for t in detail["tools"] if t["result"].get("refreshed_from"))


def test_refresh_recomputes_chart_preserves_query_old_snapshot_and_model_state(client, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A saved query refresh must not invoke a model")
    monkeypatch.setattr("document_pipeline_api.api.assistant.run_conversation", forbidden)
    saved = saved_result(client, chart={"type": "bar", "x": "vendor", "series": ["sum:amount"], "title": "供应方金额"})
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 1)
        value = json.loads(row.row_json)
        row.row_json = json.dumps({**value, "amount": 300})
        original = session.get(AssistantToolCall, saved[1]).result_json
        session.commit()
    request_id = str(uuid4())
    response = refresh(client, saved, request_id=request_id)
    assert response.status_code == 200, response.text
    assert refresh(client, saved, request_id=request_id).json() == response.json()
    detail, result = fresh_tool(client, saved)
    assert len(detail["messages"]) == 3 and len(detail["runs"]) == 2
    assert result["analysis"]["totals"]["sum:amount"] == 1390.5
    assert result["analysis"]["source"]["request"] == saved[2]["analysis"]["source"]["request"]
    assert result["analysis"]["analysis_id"] != saved[2]["analysis"]["analysis_id"]
    assert result["chart"]["type"] == "bar"
    assert detail["last_model_run"]["model"] == "original-cloud-model"
    assert detail["runs"][0]["execution_kind"] == "analysis_refresh"
    assert detail["profile_id"] == "cloud"
    assert "table_id" not in detail["messages"][1]["parts"][0]["text"]
    with client.app.state.session_factory() as session:
        assert session.get(AssistantToolCall, saved[1]).result_json == original
    assert refresh(client, saved, context={"table_id": "ledger"}, request_id=request_id).status_code == 409
    # Last model state remains available even when the newest page contains only local refreshes.
    page = client.get(f"/api/v1/assistant/threads/{saved[0]}?limit=2").json()
    assert page["last_model_run"]["model"] == "original-cloud-model"


@pytest.mark.parametrize("context,status", [({}, 403), ({"table_id": "ledger", "row_ids": [1]}, 409),
    ({"table_id": "ledger", "row_ids": []}, 409)])
def test_refresh_cannot_silently_narrow_or_expand_permission(client, context, status):
    saved = saved_result(client)
    response = refresh(client, saved, context)
    assert response.status_code == status, response.text
    detail = client.get(f"/api/v1/assistant/threads/{saved[0]}").json()
    assert len(detail["messages"]) == 1


def test_refresh_unknown_thread_archived_active_and_deleted_field(client):
    saved = saved_result(client)
    assert refresh(client, (str(uuid4()), saved[1], saved[2])).status_code == 404
    with client.app.state.session_factory() as session:
        run = session.scalar(select(AssistantRun).where(AssistantRun.thread_id == saved[0]))
        run.status = "running"
        session.commit()
    assert refresh(client, saved).status_code == 409
    with client.app.state.session_factory() as session:
        run = session.scalar(select(AssistantRun).where(AssistantRun.thread_id == saved[0]))
        run.status = "completed"
        table = session.get(DataTableRecord, "ledger")
        table.columns_json = json.dumps([c for c in json.loads(table.columns_json) if c["key"] != "amount"])
        session.commit()
    response = refresh(client, saved)
    assert response.status_code == 422, response.text
    assert "字段" in response.text
    client.patch(f"/api/v1/assistant/threads/{saved[0]}", json={"archived": True})
    assert refresh(client, saved).status_code == 409


def test_refresh_projects_only_previously_requested_record_columns(client):
    saved = saved_result(client, query={"table_id": "ledger", "records": True, "limit": 3})
    with client.app.state.session_factory() as session:
        call = session.get(AssistantToolCall, saved[1])
        result = json.loads(call.result_json)
        result["columns"] = [c for c in result["columns"] if c["key"] == "vendor"]
        for row in result["rows"]:
            row["values"] = {"vendor": row["values"]["vendor"]}
        call.result_json = json.dumps(result)
        session.commit()
    assert refresh(client, saved).status_code == 200
    _, result = fresh_tool(client, saved)
    assert len(result["rows"]) == 3
    assert all(set(row["values"]) == {"vendor"} for row in result["rows"])


def test_refresh_chart_fallback_explains_changed_data(client):
    saved = saved_result(client, query={"table_id": "ledger", "dimensions": ["vendor"],
        "metrics": [{"op": "sum", "field": "amount"}]},
        chart={"type": "pie", "x": "vendor", "series": ["sum:amount"], "title": "占比"})
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 3)
        row.row_json = json.dumps({**json.loads(row.row_json), "amount": -500})
        session.commit()
    assert refresh(client, saved).status_code == 200
    _, result = fresh_tool(client, saved)
    assert result["actual_chart_type"] == "table"
    assert any("不能沿用原图表" in w for w in result["analysis"]["warnings"])


def test_retired_currency_notice_removed_from_views_without_rewriting_history(client):
    with client.app.state.session_factory() as session:
        table = session.get(DataTableRecord, "ledger")
        columns = json.loads(table.columns_json)
        next(c for c in columns if c["key"] == "amount")["label"] = "金额"
        table.columns_json = json.dumps(columns)
        session.commit()
    saved = saved_result(client)
    assert not any("未记录币种" in w for w in saved[2]["warnings"])
    retired = "「总额」未记录币种，仅显示数值汇总；不能作为统一币种总额。"
    with client.app.state.session_factory() as session:
        call = session.get(AssistantToolCall, saved[1])
        result = json.loads(call.result_json)
        result["warnings"].append(retired)
        call.result_json = json.dumps(result)
        session.commit()
    detail = client.get(f"/api/v1/assistant/threads/{saved[0]}").json()
    assert retired not in detail["tools"][0]["result"]["warnings"]
    with client.app.state.session_factory() as session:
        assert retired in json.loads(session.get(AssistantToolCall, saved[1]).result_json)["warnings"]


@pytest.mark.parametrize("count", [1000, 10000])
def test_scale_refresh_uses_complete_database_range_and_bounded_results(client, count):
    with client.app.state.session_factory() as session:
        session.query(DataRowRecord).delete()
        session.add_all([DataRowRecord(table_id="ledger", item_index=i,
            row_json=json.dumps({"vendor": f"V{i % 10}", "amount": i + 1, "total": i + 1,
                                "unused": "无关长文字" * 200})) for i in range(count)])
        session.commit()
    saved = saved_result(client)
    with client.app.state.session_factory() as session:
        row = session.scalar(select(DataRowRecord).order_by(DataRowRecord.id).limit(1))
        row.row_json = json.dumps({**json.loads(row.row_json), "amount": 101})
        session.commit()
    response = refresh(client, saved)
    assert response.status_code == 200, response.text
    _, result = fresh_tool(client, saved)
    assert result["source"]["row_count"] == count
    assert result["totals"]["sum:amount"] == count * (count + 1) / 2 + 100
    assert len(result["data"]) == 10
    assert "无关长文字" not in json.dumps(result, ensure_ascii=False)
    assert len(json.dumps(result)) < 15000
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssistantRun)) == 2
