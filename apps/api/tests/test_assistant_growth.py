import json
import threading
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, func

from document_pipeline_api.models import (
    AssistantThread,
    AssistantMessage,
    AssistantRun,
    DataRowRecord,
    DataTableRecord,
    TaskRecord,
)
from document_pipeline_api.models.assistant import AssistantEvent
from document_pipeline_api.services.assistant_analysis import AnalysisRequest, Metric, analyze
from document_pipeline_api.services.assistant_events import EventWriter, materialized_parts
from document_pipeline_api.services.assistant_memory import encode, model_result
from document_pipeline_api.services.assistant_tools import Context, execute_tool
from document_pipeline_api.services.file_names import fixed_file_name
from test_assistant import client as ledger_fixture, ScriptedProvider, select_profile, send, finish

client = ledger_fixture


@pytest.mark.parametrize("size", [1000, 10000])
def test_large_analysis_keeps_complete_facts_without_retaining_unrelated_long_cells(client, size):
    factory = client.app.state.session_factory
    with factory() as session:
        session.execute(delete(DataRowRecord))
        table = session.get(DataTableRecord, "ledger")
        table.columns_json = json.dumps(
            [
                dict(key="amount", label="金额（元）", section="item", value_type="number"),
                dict(key="vendor", label="供应商", section="item", value_type="text"),
                dict(key="note", label="备注", section="item", value_type="text"),
            ]
        )
        for start in range(0, size, 500):
            session.execute(
                DataRowRecord.__table__.insert(),
                [
                    dict(
                        table_id="ledger",
                        item_index=i,
                        row_json=json.dumps(
                            {
                                "vendor": f"供应商{i % 10}",
                                "amount": i + 1,
                                "note": "仅在读取备注时需要。" * 400,
                            },
                            ensure_ascii=False,
                        ),
                    )
                    for i in range(start, min(start + 500, size))
                ],
            )
        session.commit()
    tracemalloc.start()
    try:
        with factory() as session:
            result = analyze(
                session,
                AnalysisRequest(
                    table_id="ledger",
                    dimensions=["vendor"],
                    metrics=[Metric(op="sum", field="amount"), Metric(op="count")],
                ),
            )
            assert result["source"]["row_count"] == size
            assert result["totals"] == {"sum:amount": size * (size + 1) / 2, "count:*": size}
            result["analysis_id"] = "snapshot"
            preview = model_result(result, "tool")
            assert len(preview["data"]) == 3
            assert preview["totals"] == result["totals"]
            assert "仅在读取备注" not in encode(preview)
            assert len(encode(preview)) < 6000
            rows = execute_tool(
                session,
                Context(table_id="ledger"),
                "read_data_rows",
                {
                    "table_id": "ledger",
                    "fields": ["amount"],
                    "sort": "amount",
                    "descending": True,
                },
                {},
            )
            assert rows["source"]["row_count"] == size
            assert [r["values"] for r in rows["rows"]] == [{"amount": size - i} for i in range(3)]
            assert rows["truncated"]
        assert tracemalloc.get_traced_memory()[1] < 60_000_000
    finally:
        tracemalloc.stop()


def test_transport_budget_applies_to_wide_metadata_and_preserves_text_pagination():
    huge = '汉字😀\\"' * 10000
    for budget in (1500, 6000):
        result = model_result(
            {
                "error": huge,
                "reference": {"label": huge},
                "rows": [{str(i): huge for i in range(100)}],
            },
            "tool",
            budget,
        )
        assert len(encode(result)) <= budget
        original = {
            "offset": 4000,
            "text": huge,
            "total_characters": 4000 + len(huge),
            "next_offset": None,
        }
        page = model_result(original, "tool", budget)
        assert len(encode(page)) <= budget
        assert original["text"].startswith(page["text"])
        assert page["next_offset"] == 4000 + len(page["text"])


def test_stream_events_are_append_only_replayable_and_checkpoint_independent(client):
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(AssistantThread(id="stream-thread", title="流式边界"))
        session.flush()
        session.add(
            AssistantMessage(
                id="stream-message", thread_id="stream-thread", position=0, role="assistant"
            )
        )
        session.flush()
        session.add(
            AssistantRun(
                id="stream-run",
                thread_id="stream-thread",
                message_id="stream-message",
                profile_id="local",
                profile_version=1,
                model="fixture",
                provider="fixture",
                stream_version=1,
            )
        )
        session.commit()
    parts = [{"type": "text", "text": ""}]
    writer = EventWriter(factory, "stream-run", parts, {}, threading.Event())
    for _ in range(1000):
        parts[0]["text"] += "文字😀0123456789"
        writer.emit("text.delta", text="文字😀0123456789")
    writer.emit("status", message="done")
    with factory() as session:
        run = session.get(AssistantRun, "stream-run")
        assert run.events_json == "[]"
        assert session.scalar(select(func.count()).select_from(AssistantEvent)) == 1001
        assert session.scalar(select(func.sum(func.length(AssistantEvent.payload_json)))) < 150_000
        run.snapshot_sequence = 0
        session.get(AssistantMessage, "stream-message").parts_json = "[]"
        assert materialized_parts(session, run, []) == parts
        assert materialized_parts(session, run, [dict(parts[0])]) == parts
        run.status = "completed"
        session.commit()
    replay = client.get(
        "/api/v1/assistant/runs/stream-run/events", headers={"Last-Event-ID": "997"}
    ).text
    assert "id: 998\n" in replay and "id: 997\n" not in replay and "id: 1001\n" in replay


def test_history_paginates_and_recalls_older_scope_without_replaying_entire_thread(client):
    with client.app.state.session_factory() as session:
        session.add_all(
            [AssistantThread(id=f"thread-{i}", title=f"历史对话{i:04d}") for i in range(350)]
        )
        session.flush()
        session.add_all(
            [
                AssistantMessage(
                    id=str(uuid4()),
                    thread_id="thread-0",
                    position=i,
                    role="user" if i % 2 == 0 else "assistant",
                    context_json=json.dumps({"table_id": "ledger"}),
                    parts_json=json.dumps([{"type": "text", "text": f"历史资料{i:04d}"}]),
                )
                for i in range(620)
            ]
        )
        session.commit()
    assert len(client.get("/api/v1/assistant/threads").json()) == 60
    pages = [
        client.get("/api/v1/assistant/threads", params={"offset": offset}).json()
        for offset in range(0, 350, 60)
    ]
    assert len({row["id"] for page in pages for row in page}) == 350
    assert len(client.get("/api/v1/assistant/threads?search=历史对话0000").json()) == 1
    page = client.get("/api/v1/assistant/threads/thread-0").json()
    assert page["oldest_position"] == 560 and page["has_more"]
    older = client.get("/api/v1/assistant/threads/thread-0?before=560").json()
    assert [m["position"] for m in older["messages"]] == list(range(500, 560))
    with client.app.state.session_factory() as session:
        artifacts = {
            "_history_thread": "thread-0",
            "_history_before": 620,
            "_history_context": {"table_id": "ledger"},
        }
        recalled = execute_tool(
            session,
            Context(table_id="ledger"),
            "read_conversation_history",
            {"search": "历史资料0000"},
            artifacts,
        )
        assert recalled["position"] == 0
        artifacts["_history_context"] = {"table_id": "another-table"}
        assert (
            execute_tool(
                session,
                Context(table_id="another-table"),
                "read_conversation_history",
                {"search": "历史资料0000"},
                artifacts,
            )["message"]
            is None
        )
    ScriptedProvider.plan = []
    ScriptedProvider.requests = []
    client.app.state.conversation_factory = ScriptedProvider
    detail = finish(
        client,
        send(client, select_profile(client), thread_id="thread-0", context={"table_id": "ledger"}),
    )
    assert detail["runs"][0]["status"] == "completed"
    assert detail["messages"][-1]["position"] == 621
    assert "历史资料0000" not in encode(ScriptedProvider.requests)


def test_fixed_names_are_unique_concurrently_and_stable_across_sessions(client):
    template = SimpleNamespace(id="fixed-template", name="送货单")
    ids = [
        client.post(
            "/api/v1/tasks", files={"file": (f"original-{i}.txt", b"test", "text/plain")}
        ).json()["id"]
        for i in range(24)
    ]

    def allocate(identifier):
        with client.app.state.session_factory() as session:
            task = session.get(TaskRecord, identifier)
            state = fixed_file_name(session, task, template)
            task.file_name_json = state.model_dump_json()
            session.commit()
            return state.confirmed_filename

    with ThreadPoolExecutor(max_workers=4) as pool:
        names = list(pool.map(allocate, ids))
    assert len(set(names)) == 24
    assert {name.rsplit("_", 1)[1] for name in names} == {f"{i:03d}.txt" for i in range(1, 25)}
    assert [allocate(identifier) for identifier in ids] == names
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, ids[0]).filename == "original-0.txt"
