import json
import threading
import time
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import (
    DataTableRecord,
    DataRowRecord,
    DataRowRevisionRecord,
    AssistantThread,
)
from document_pipeline_api.services.assistant_analysis import AnalysisRequest, analyze, number
from document_pipeline_api.services.assistant_tools import Context, execute_tool
from document_pipeline_api.model_providers.conversation import ConversationProvider


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'document-pipeline.db'}",
            storage_dir=tmp_path / "uploads",
        )
    )
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            columns = [
                dict(key="vendor", label="供应方", section="header", value_type="text"),
                dict(key="total", label="总额", section="header", value_type="text"),
                dict(key="amount", label="明细额", section="item", value_type="number"),
                dict(key="month", label="月份", section="header", value_type="date"),
            ]
            session.add(
                DataTableRecord(
                    id="ledger",
                    name="合成账本",
                    template_key="manual:test",
                    template_version="1",
                    document_kind="custom",
                    columns_json=json.dumps(columns),
                )
            )
            session.flush()
            for index, values in enumerate(
                [
                    {
                        "vendor": "A",
                        "total": "1,200.50",
                        "amount": 200,
                        "month": "2026-01-01",
                        "__row_group": "first",
                    },
                    {
                        "vendor": "A",
                        "total": "1,200.50",
                        "amount": 1000.5,
                        "month": "2026-01-01",
                        "__row_group": "first",
                    },
                    {
                        "vendor": "B",
                        "total": "90",
                        "amount": 90,
                        "month": "2026-02-01",
                        "__row_group": "second",
                    },
                    {"vendor": "C", "total": "未知", "amount": None, "month": "2026-03-01"},
                ]
            ):
                session.add(
                    DataRowRecord(
                        table_id="ledger",
                        item_index=index,
                        row_json=json.dumps(values),
                        review_pending=index == 3,
                        input_scope_json=json.dumps({"coverage": "partial"})
                        if index == 3
                        else None,
                    )
                )
            session.commit()
        yield client


def analysis(client, **kwargs):
    with client.app.state.session_factory() as session:
        return analyze(session, AnalysisRequest(table_id="ledger", **kwargs))


def test_document_amount_not_duplicated_and_partial_evidence_visible(client):
    result = analysis(client, metrics=[{"op": "sum", "field": "total"}])
    assert result["data"][0]["sum:total"] == 1290.5
    assert result["source"]["row_count"] == 4
    assert result["source"]["document_count"] == 3
    assert result["source"]["review_pending_count"] == 1
    assert result["source"]["partial_input_count"] == 1
    assert any("无法作为数字" in warning for warning in result["warnings"])
    assert (
        analysis(client, metrics=[{"op": "sum", "field": "amount"}])["data"][0]["sum:amount"]
        == 1290.5
    )
    assert number("001234") is None
    assert number(True) is None
    assert number(float("inf")) is None


def test_topn_sorts_full_amount_and_time_keeps_chronology(client):
    result = analysis(
        client, dimensions=["vendor"], metrics=[{"op": "sum", "field": "total"}], limit=1
    )
    assert result["data"][0]["vendor"] == "A"
    assert result["group_count"] == 3 and result["truncated"]
    assert result["totals"]["sum:total"] == 1290.5
    assert "总额" in result["metric_labels"]["sum:total"]
    assert (
        analysis(client, metrics=[{"op": "count", "field": "amount"}])["totals"]["count:amount"]
        == 3
    )
    result = analysis(client, dimensions=["month"], time_bucket="month")
    assert [r["month"] for r in result["data"]] == ["2026-01", "2026-02", "2026-03"]
    with pytest.raises(HTTPException):
        analysis(client, dimensions=["amount"], metrics=[{"op": "sum", "field": "total"}])


def test_conflicting_public_dimensions_cannot_double_count(client):
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 2)
        values = json.loads(row.row_json)
        values["vendor"] = "Different"
        row.row_json = json.dumps(values)
        session.commit()
    with pytest.raises(HTTPException, match="公共字段"):
        analysis(client, dimensions=["vendor"], metrics=[{"op": "sum", "field": "total"}])


def test_context_scope_cannot_expand_and_chart_uses_snapshot(client):
    with client.app.state.session_factory() as session:
        context = Context(table_id="ledger", row_ids=[1, 2])
        result = execute_tool(
            session,
            context,
            "analyze_data_table",
            {"table_id": "ledger", "row_ids": [2, 3], "metrics": [{"op": "sum", "field": "total"}]},
            artifacts := {},
        )
        assert result["source"]["row_count"] == 1
        session.get(DataRowRecord, 2).row_json = json.dumps({"total": "8888"})
        session.commit()
        chart = execute_tool(
            session,
            context,
            "render_chart",
            {
                "analysis_id": result["analysis_id"],
                "type": "metric",
                "title": "总额",
                "series": ["sum:total"],
            },
            artifacts,
        )
        assert chart["analysis"]["data"][0]["sum:total"] == 1200.5
        with pytest.raises(HTTPException):
            execute_tool(
                session,
                Context(),
                "render_chart",
                {
                    "analysis_id": result["analysis_id"],
                    "type": "metric",
                    "title": "总额",
                    "series": ["sum:total"],
                },
                artifacts,
            )
        with pytest.raises(HTTPException):
            execute_tool(
                session,
                Context(table_id="ledger", search="不存在"),
                "propose_changes",
                {"table_id": "ledger", "row_id": 1, "changes": {"total": "50"}},
                {},
            )
        empty = execute_tool(
            session,
            Context(table_id="ledger", row_ids=[]),
            "analyze_data_table",
            {"table_id": "ledger"},
            {},
        )
        assert empty["source"]["row_count"] == 0


def test_chart_rejects_invented_metrics_and_partial_pie(client):
    with client.app.state.session_factory() as session:
        context = Context(table_id="ledger")
        result = execute_tool(
            session,
            context,
            "analyze_data_table",
            {"table_id": "ledger", "dimensions": ["vendor"], "limit": 1},
            artifacts := {},
        )
        for series, kind in [
            (["imaginary"], "bar"),
            (["count:*"], "pie"),
            (["count:*"], "scatter"),
        ]:
            with pytest.raises(HTTPException):
                execute_tool(
                    session,
                    context,
                    "render_chart",
                    {
                        "analysis_id": result["analysis_id"],
                        "type": kind,
                        "title": "统计",
                        "series": series,
                    },
                    artifacts,
                )


class ScriptedProvider:
    requests = []
    plan = []

    def __init__(self, settings, cancel):
        self.cancel = cancel
        self.step = 0

    def close(self):
        pass

    def stream(self, messages, tools):
        self.requests.append(json.loads(json.dumps(messages)))
        steps = self.plan
        if self.step < len(steps):
            events = steps[self.step]
            self.step += 1
            for event in events:
                yield event
        else:
            yield {"type": "text", "text": "这是回答。"}


def select_profile(client, remote=False):
    if remote:
        response = client.post(
            "/api/v1/models/profiles",
            json={
                "name": "远程测试",
                "provider": "openai_compatible",
                "base_url": "https://models.example.test/v1",
                "model_name": "test",
                "context_length": 32000,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()
    return client.get("/api/v1/models/profiles").json()[0]


def send(client, profile, **kwargs):
    return client.post(
        "/api/v1/assistant/runs",
        json={
            "text": "你好",
            "profile_id": profile["id"],
            "profile_version": profile["version"],
            **kwargs,
        },
    )


def finish(client, started):
    assert started.status_code == 200, started.text
    payload = started.json()
    stream = client.get(f"/api/v1/assistant/runs/{payload['run_id']}/events")
    assert "run.completed" in stream.text, stream.text
    return client.get(f"/api/v1/assistant/threads/{payload['thread_id']}").json()


def test_chat_persists_without_changing_extraction_default(client):
    ScriptedProvider.plan = []
    ScriptedProvider.requests = []
    client.app.state.conversation_factory = ScriptedProvider
    before = client.get("/api/v1/models/profiles").json()
    assert client.get("/api/v1/assistant/threads").json() == []
    assert client.get("/api/v1/assistant/settings").json()["profile_id"] is None
    p = select_profile(client)
    client.put("/api/v1/assistant/settings", json={"profile_id": p["id"]})
    detail = finish(client, send(client, p))
    assert detail["runs"][0]["status"] == "completed", detail
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["parts"][0]["text"] == "这是回答。"
    assert client.get("/api/v1/models/profiles").json() == before
    client.patch(
        f"/api/v1/assistant/threads/{detail['id']}", json={"title": "保存的会话", "archived": True}
    )
    assert send(client, p, thread_id=detail["id"]).status_code == 409
    assert len(client.get("/api/v1/assistant/threads?archived=true").json()) == 1


def test_cloud_consent_each_send_and_context_change_omits_old_business_history(client):
    ScriptedProvider.plan = []
    ScriptedProvider.requests = []
    client.app.state.conversation_factory = ScriptedProvider
    local = select_profile(client)
    detail = finish(client, send(client, local, text="保密业务词", context={"table_id": "ledger"}))
    cloud = select_profile(client, True)
    assert send(client, cloud, thread_id=detail["id"]).status_code == 403
    assert len(ScriptedProvider.requests) == 1
    detail = finish(
        client,
        send(
            client,
            cloud,
            thread_id=detail["id"],
            remote_consent=f"{cloud['id']}:{cloud['version']}",
        ),
    )
    assert detail["runs"][0]["status"] == "completed", detail
    assert "保密业务词" not in json.dumps(ScriptedProvider.requests[-1], ensure_ascii=False)
    assert send(client, cloud, thread_id=detail["id"]).status_code == 403
    assert send(client, local, profile_version=99).status_code == 409


def test_write_preview_confirmation_is_atomic_idempotent_and_checks_version(client):
    ScriptedProvider.plan = [
        [
            {
                "type": "tool",
                "id": "one",
                "name": "propose_changes",
                "arguments": {"table_id": "ledger", "row_id": 1, "changes": {"total": "100"}},
            }
        ]
    ]
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(
        client, send(client, select_profile(client), context={"table_id": "ledger", "row_ids": [1]})
    )
    assert detail["runs"][0]["status"] == "completed", detail
    tool = detail["tools"][0]
    assert tool["status"] == "pending", tool
    assert len(tool["result"]["affected"]) == 2
    with client.app.state.session_factory() as session:
        assert json.loads(session.get(DataRowRecord, 1).row_json)["total"] == "1,200.50"
    path = f"/api/v1/assistant/tools/{tool['id']}/decision"
    approved = client.post(path, json={"approve": True})
    assert approved.status_code == 200, approved.text
    assert client.post(path, json={"approve": True}).json()["status"] == "approved"
    with client.app.state.session_factory() as session:
        assert json.loads(session.get(DataRowRecord, 2).row_json)["total"] == "100"
        assert session.scalar(select(func.count()).select_from(DataRowRevisionRecord)) == 2
    detail2 = finish(client, send(client, select_profile(client), context={"table_id": "ledger"}))
    with client.app.state.session_factory() as session:
        session.get(DataRowRecord, 1).row_version += 1
        session.commit()
    assert (
        client.post(
            f"/api/v1/assistant/tools/{detail2['tools'][0]['id']}/decision", json={"approve": True}
        ).status_code
        == 409
    )
    client.delete(f"/api/v1/assistant/threads/{detail['id']}")
    assert client.get(f"/api/v1/assistant/threads/{detail['id']}").status_code == 404


def test_reference_pagination_and_deleted_source(client):
    assert (
        client.get("/api/v1/assistant/references/tables/ledger/rows/3?page_size=2").json()["page"]
        == 2
    )
    assert client.get("/api/v1/assistant/references/tables/ledger/rows/999").status_code == 404


def test_chat_snapshots_survive_backup_restore(client):
    ScriptedProvider.plan = [
        [
            {
                "type": "tool",
                "id": "a",
                "name": "analyze_data_table",
                "arguments": {"table_id": "ledger", "metrics": [{"op": "sum", "field": "total"}]},
            }
        ]
    ]
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(client, send(client, select_profile(client), context={"table_id": "ledger"}))
    created = client.post("/api/v1/backups")
    assert created.status_code == 201, created.text
    client.delete(f"/api/v1/assistant/threads/{detail['id']}")
    restored = client.post(f"/api/v1/backups/{created.json()['name']}/restore")
    assert restored.status_code == 200, restored.text
    recovered = client.get(f"/api/v1/assistant/threads/{detail['id']}").json()
    assert recovered["messages"] == detail["messages"]
    assert recovered["tools"] == detail["tools"]


def test_local_inference_wait_can_be_cancelled(tmp_path):
    from document_pipeline_api.model_providers.inference_gate import inference_slot

    settings = Settings(database_url="sqlite://", storage_dir=tmp_path / "uploads")
    cancel = threading.Event()
    waiting = threading.Event()
    finished = threading.Event()
    errors = []

    def blocked():
        try:
            with inference_slot(settings, cancel, waiting.set):
                errors.append("unexpected acquisition")
        except InterruptedError:
            finished.set()

    with inference_slot(settings):
        worker = threading.Thread(target=blocked)
        worker.start()
        assert waiting.wait(2)
        cancel.set()
        assert finished.wait(2)
        worker.join(2)
    assert not errors


def test_native_tool_advertisement_avoids_local_grammar_blowup_and_limits_fields(client):
    from document_pipeline_api.services.assistant_tools import scoped_tool_specs
    from pydantic import ValidationError

    with client.app.state.session_factory() as session:
        specs = scoped_tool_specs(
            session,
            Context(table_id="ledger", row_ids=[1, 2]),
            {"_analysis_options": {"enabled": True}},
            False,
        )
    names = {s["function"]["name"] for s in specs}
    assert "catalog" in names and "render_chart" not in names
    spec = next(
        s["function"]["parameters"] for s in specs if s["function"]["name"] == "analyze_data_table"
    )
    assert "records" not in spec["properties"]
    assert "row_id" not in spec["$defs"]["Filter"]["properties"]["field"]["enum"]
    assert '"maxItems": 2000' not in json.dumps(spec)
    with pytest.raises(ValidationError):
        AnalysisRequest(table_id="ledger", row_ids=list(range(2001)))


def test_uncertain_send_retry_is_idempotent_and_equal_timestamps_keep_order(client):
    from document_pipeline_api.models import AssistantMessage

    ScriptedProvider.plan = []
    ScriptedProvider.requests = []
    client.app.state.conversation_factory = ScriptedProvider
    profile = select_profile(client)
    identifier = str(uuid4())
    first = send(client, profile, request_id=identifier)
    detail = finish(client, first)
    again = send(client, profile, request_id=identifier)
    assert again.json() == first.json()
    assert len(ScriptedProvider.requests) == 1
    assert send(client, profile, request_id=identifier, text="不同内容").status_code == 409
    with client.app.state.session_factory() as session:
        messages = session.scalars(
            select(AssistantMessage).order_by(AssistantMessage.position)
        ).all()
        messages[1].created_at = messages[0].created_at
        session.commit()
    saved = client.get(f"/api/v1/assistant/threads/{detail['id']}").json()
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]


def test_chat_template_draft_is_removed(client):
    with client.app.state.session_factory() as session:
        with pytest.raises(HTTPException, match="模板页面"):
            execute_tool(session, Context(), "draft_template", {
                "name": "收支", "fields": [{"label": "金额", "value_type": "number"}],
            }, {})


def test_large_analysis_refuses_partial_statistics(client):
    from sqlalchemy import insert

    with client.app.state.session_factory() as session:
        session.execute(
            insert(DataRowRecord),
            [
                {
                    "table_id": "ledger",
                    "item_index": i + 10,
                    "row_json": '{"vendor":"合成","total":1}',
                }
                for i in range(100001)
            ],
        )
        session.commit()
        with pytest.raises(HTTPException, match="十万"):
            analyze(
                session,
                AnalysisRequest(table_id="ledger", metrics=[{"op": "sum", "field": "total"}]),
            )


@pytest.mark.parametrize("ollama", [False, True])
def test_native_streaming_protocol_and_no_extraction_schema(tmp_path, ollama):
    captured = []

    def transport(request):
        captured.append(json.loads(request.content))
        if ollama:
            items = [
                {
                    "message": {
                        "content": "你好",
                        "tool_calls": [
                            {"function": {"name": "catalog", "arguments": {"kind": "tables"}}}
                        ],
                    }
                },
                {"done": True, "eval_count": 3},
            ]
            content = "\n".join(json.dumps(item) for item in items)
        else:
            items = [
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "你好",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {"name": "catalog", "arguments": '{"kind":'},
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": '"tables"}'}}]
                            }
                        }
                    ]
                },
            ]
            content = (
                "\n\n".join("data: " + json.dumps(item) for item in items) + "\n\ndata: [DONE]\n\n"
            )
        return httpx.Response(200, text=content)

    settings = Settings(
        database_url="sqlite://",
        storage_dir=tmp_path,
        model_provider="ollama" if ollama else "lm_studio",
    )
    provider = ConversationProvider(settings, threading.Event())
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(transport))
    events = list(
        provider.stream(
            [{"role": "user", "content": "你好"}],
            [{"type": "function", "function": {"name": "catalog"}}],
        )
    )
    provider.close()
    assert events[0] == {"type": "text", "text": "你好"}
    assert events[-1]["arguments"] == {"kind": "tables"}
    assert "response_format" not in captured[0] and "format" not in captured[0]


def test_cancel_and_clear_do_not_resurrect_conversation(client):
    class WaitingProvider(ScriptedProvider):
        def stream(self, messages, tools):
            yield {"type": "text", "text": "已保存"}
            while not self.cancel.wait(0.01):
                pass
            raise InterruptedError("停止")

    client.app.state.conversation_factory = WaitingProvider
    started = send(client, select_profile(client)).json()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = client.get(f"/api/v1/assistant/threads/{started['thread_id']}").json()
        if current["messages"][-1]["parts"]:
            break
        time.sleep(0.01)
    cleared = client.post(
        "/api/v1/system/admin/clear-data", json={"confirm_text": "清除全部本地数据"}
    )
    assert cleared.status_code == 200, cleared.text
    assert not client.app.state.assistant_active
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssistantThread)) == 0
