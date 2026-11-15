"""Controlled assistant timing at a selected file's completion boundary."""

import json
import threading
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from document_pipeline_api.model_providers.base import ModelTimeoutError
from document_pipeline_api.db import build_engine
from document_pipeline_api.models import DataRowRecord, ExtractionRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.services.current_data import current_snapshot, initialize_current, revise_current
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.services.assistant_memory import scope_identity
from document_pipeline_api.services.assistant_tools import Context, check_context, execute_tool
from test_assistant import ScriptedProvider, client as ledger_fixture, finish, select_profile, send
from test_polish_data_integrity import add_selected_file, complete_selected_file

client = ledger_fixture


def tool_event(identifier, *, kind="task", **arguments):
    return {"type": "tool", "id": identifier, "name": "read_resource",
            "arguments": {"kind": kind, "id": "selected-file", **arguments}}


@pytest.mark.parametrize("ready_before_send", [False, True])
def test_selected_file_completion_before_first_tool_stays_within_file(client, ready_before_send):
    entered, release = threading.Event(), threading.Event()

    class HeldProvider(ScriptedProvider):
        requests = []
        plan = [[tool_event("selected")]]

        def stream(self, messages, tools):
            if self.step == 0:
                entered.set()
                assert release.wait(10)
            yield from super().stream(messages, tools)

    client.app.state.conversation_factory = HeldProvider
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=ready_before_send)
    started = send(client, select_profile(client), context={"task_id": "selected-file"})
    try:
        assert entered.wait(10)
        if not ready_before_send:
            with client.app.state.session_factory() as session:
                complete_selected_file(session)
                session.commit()
    finally:
        release.set()
    detail = finish(client, started)
    result = detail["tools"][0]["result"]
    assert result["status"] == "completed"
    assert result["extraction"]["result"] == {
        "header": {"vendor": "仅所选文件", "total": "00123"}, "items": [{"amount": 7}],
    }
    assert result["extraction"]["item_count"] == 1
    model_visible = json.dumps(HeldProvider.requests, ensure_ascii=False)
    assert "1,200.50" not in model_visible and "仅所选文件" in model_visible


@pytest.mark.parametrize("scope_key", ["task_id", "task_ids"])
def test_same_selected_file_keeps_prior_history_after_completion(client, scope_key):
    class RecordingProvider(ScriptedProvider):
        requests = []
        plan = []

    client.app.state.conversation_factory = RecordingProvider
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=False)
    context = {scope_key: "selected-file" if scope_key == "task_id" else ["selected-file"]}
    profile = select_profile(client)
    first = finish(client, send(client, profile, context=context,
                                text="请记住这份文件后续只核对合成编号 D02-HISTORY-ONLY。"))
    with client.app.state.session_factory() as session:
        complete_selected_file(session)
        session.commit()
    finish(client, send(client, profile, context=context, thread_id=first["id"], text="请继续刚才的要求。"))
    assert "D02-HISTORY-ONLY" in json.dumps(RecordingProvider.requests[-1], ensure_ascii=False)


def test_long_task_read_keeps_current_version_in_provider_context(client):
    class RecordingProvider(ScriptedProvider):
        requests = []
        plan = [[tool_event("long-current")]]

    client.app.state.conversation_factory = RecordingProvider
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=True)
        revise_current(session, "selected-file", expected_version=0,
                       header_changes={"vendor": "合成长内容" * 3000})
        session.commit()
    detail = finish(client, send(client, select_profile(client), context={"task_id": "selected-file"}))
    saved = detail["tools"][0]["result"]
    visible = [json.loads(message["content"]) for message in RecordingProvider.requests[-1]
               if message["role"] == "tool"][-1]
    assert saved["extraction"]["version"] == 1
    assert visible.get("version", visible.get("extraction", {}).get("version")) == 1


@pytest.mark.parametrize("stop_action", ["scope", "cancel"])
def test_scope_change_or_cancel_blocks_late_read_after_file_completion(client, stop_action):
    entered, release = threading.Event(), threading.Event()

    class HeldProvider(ScriptedProvider):
        requests = []
        plan = [[tool_event("late-selected")]]

        def close(self):
            release.set()

        def stream(self, messages, tools):
            if self.step == 0:
                entered.set()
                assert release.wait(10)
            yield from super().stream(messages, tools)

    client.app.state.conversation_factory = HeldProvider
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=False)
    profile = select_profile(client)
    started = send(client, profile, context={"task_id": "selected-file"})
    try:
        assert entered.wait(10)
        with client.app.state.session_factory() as session:
            complete_selected_file(session)
            session.commit()
        payload = started.json()
        if stop_action == "scope":
            response = client.put(f"/api/v1/assistant/threads/{payload['thread_id']}/authorization", json={
                "context": {}, "profile_id": profile["id"], "profile_version": profile["version"],
                "remote_authorized": False,
            })
        else:
            response = client.post(f"/api/v1/assistant/runs/{payload['run_id']}/cancel")
        assert response.status_code == 200, response.text
    finally:
        release.set()
    detail = finish(client, started)
    assert detail["runs"][0]["status"] == "cancelled"
    assert not detail["tools"]
    assert "仅所选文件" not in json.dumps(HeldProvider.requests, ensure_ascii=False)


@pytest.mark.parametrize("first_kind", ["task", "file_data"])
def test_current_file_continuation_rejects_revision_that_would_skip_a_detail(client, first_kind):
    entered, release = threading.Event(), threading.Event()

    class HeldProvider(ScriptedProvider):
        requests = []
        plan = [[tool_event("page-one", kind=first_kind, page=1, limit=1)],
                [tool_event("page-two", kind="file_data", page=2, limit=1)]]

        def stream(self, messages, tools):
            if self.step == 1:
                entered.set()
                assert release.wait(10)
            yield from super().stream(messages, tools)

    client.app.state.conversation_factory = HeldProvider
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=True)
        revise_current(session, "selected-file", expected_version=0, new_items=[{"amount": 8}, {"amount": 9}])
        session.commit()
        initial = current_snapshot(session, "selected-file")
    started = send(client, select_profile(client), context={"task_id": "selected-file"})
    try:
        assert entered.wait(10)
        with client.app.state.session_factory() as session:
            revise_current(session, "selected-file", expected_version=1, deleted_ids=[initial["items"][0]["id"]])
            session.commit()
    finally:
        release.set()
    detail = finish(client, started)
    results = [call["result"] for call in detail["tools"]]
    first_result = next(result for result in results if result.get("page", result.get("extraction", {}).get("page")) == 1)
    first = first_result["extraction"] if first_kind == "task" else first_result
    assert first["version"] == 1
    assert (first["result"]["items"][0] if first_kind == "task" else first["items"][0]["values"])["amount"] == 7
    conflict = next((result for result in results if "error" in result), {})
    assert "版本" in conflict.get("error", "") and "第一页" in conflict["error"]
    assert not any(result.get("page") == 2 for result in results)
    with client.app.state.session_factory() as session:
        assert [json.loads(row.row_json)["amount"] for row in session.scalars(select(DataRowRecord)
            .where(DataRowRecord.task_id == "selected-file").order_by(DataRowRecord.item_index))] == [8, 9]


@pytest.mark.parametrize("task_action", ["cancel", "retry"])
def test_actual_processing_cancel_or_retry_changes_only_selected_task_reads(client, task_action):
    """Upload/process/control/read use product code; only model output is synthetic."""
    created = client.post("/api/v1/templates", json={"name": "D02 合成时序", "fields": [
        {"key": "marker", "label": "编号", "section": "header", "value_type": "text"},
        {"key": "amount", "label": "金额", "section": "item", "value_type": "number"},
    ]})
    assert created.status_code == 201, created.text
    uploaded = client.post("/api/v1/tasks", files={"file": ("d02.txt", b"Synthetic D02 only", "text/plain")},
                           data={"template_id": created.json()["id"]})
    assert uploaded.status_code == 201, uploaded.text
    task_id = uploaded.json()["id"]
    extraction_entered, extraction_release = threading.Event(), threading.Event()
    assistant_entered, assistant_release = threading.Event(), threading.Event()
    worker_errors = []

    class ExtractionStub:
        model_name = "synthetic-d02"

        def complete_text(self, prompt, result_type, **kwargs):
            extraction_entered.set()
            if task_action == "cancel":
                assert extraction_release.wait(10)
            return result_type.model_validate({"header": {"marker": "D02-NEW-CURRENT"}, "items": [{"amount": 7}]})

    class FailingExtraction(ExtractionStub):
        def complete_text(self, *args, **kwargs):
            raise ModelTimeoutError("Synthetic model timeout")

    def process():
        try:
            with client.app.state.session_factory() as session:
                process_task(session, client.app.state.settings, task_id, client=ExtractionStub())
        except Exception as error:
            worker_errors.append(error)

    class HeldProvider(ScriptedProvider):
        requests = []
        plan = [[tool_event("before-control", id=task_id)], [tool_event("after-control", id=task_id)]]

        def stream(self, messages, tools):
            if self.step == 1:
                assistant_entered.set()
                assert assistant_release.wait(10)
            yield from super().stream(messages, tools)

    client.app.state.conversation_factory = HeldProvider
    worker = None
    try:
        if task_action == "retry":
            with client.app.state.session_factory() as session:
                with pytest.raises(ModelTimeoutError):
                    process_task(session, client.app.state.settings, task_id, client=FailingExtraction())
        else:
            worker = threading.Thread(target=process, daemon=True)
            worker.start()
            assert extraction_entered.wait(10)
        started = send(client, select_profile(client), context={"task_id": task_id})
        assert assistant_entered.wait(10)
        response = client.post(f"/api/v1/tasks/{task_id}/{task_action}")
        assert response.status_code == 200, response.text
        if task_action == "retry":
            process()
        else:
            extraction_release.set()
            worker.join(10)
            assert not worker.is_alive()
    finally:
        extraction_release.set()
        assistant_release.set()
        if worker:
            worker.join(10)
    assert not worker_errors
    detail = finish(client, started)
    reads = [call["result"] for call in detail["tools"]]
    before_status = "processing" if task_action == "cancel" else "failed"
    before = next(result for result in reads if result["status"] == before_status)
    assert "extraction" not in before
    after = next(result for result in reads if result["status"] != before_status)
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        if task_action == "cancel":
            assert after["status"] == task.status == "cancelled"
            assert "extraction" not in after
            assert session.get(ExtractionRecord, task_id) is None
            assert session.get(FileCurrentDataRecord, task_id) is None
            assert "D02-NEW-CURRENT" not in json.dumps(HeldProvider.requests)
        else:
            assert after["status"] == task.status == "completed"
            assert after["extraction"]["result"] == {"header": {"marker": "D02-NEW-CURRENT"}, "items": [{"amount": 7}]}
            assert after["extraction"]["item_count"] == 1


@pytest.mark.parametrize("extra_scope", [
    {"table_id": "unrelated-table"}, {"table_ids": ["ledger"]}, {"row_ids": []},
    {"row_ids": [1]}, {"source_keys": []}, {"search": "限定"}, {"view_id": "view"},
    {"task_ids": ["other-file"]}, {"workspace": True}, {"template_ids": ["template"]},
])
def test_effective_file_identity_keeps_every_explicit_other_boundary(client, extra_scope):
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=True)
        unmaterialized_shape = {"task_id": "selected-file"}
        resolved_shape = {"task_id": "selected-file", "table_id": "ledger"}
        expected = scope_identity(unmaterialized_shape, session=session)
        assert scope_identity(resolved_shape, session=session) == expected
        assert scope_identity({**resolved_shape, **extra_scope}, session=session) != expected
        # A nonexistent file cannot assert a relationship to strip a table grant.
        assert scope_identity({"task_id": "missing", "table_id": "ledger"}, session=session)["table_id"] == "ledger"


@pytest.mark.parametrize("kind", ["task", "file_data"])
@pytest.mark.parametrize("continuation_version", [None, 2])
def test_file_page_anchor_starts_on_any_page_and_refreshes_only_on_first_page(client, kind, continuation_version):
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=True)
        revise_current(session, "selected-file", expected_version=0, new_items=[{"amount": 8}, {"amount": 9}])
        session.commit()
    artifacts = {}

    def read(page, version=None):
        with client.app.state.session_factory() as session:
            context = check_context(session, Context(task_id="selected-file"))
            arguments = {"kind": kind, "id": "selected-file", "page": page, "limit": 1}
            if version is not None:
                arguments["version"] = version
            return execute_tool(session, context, "read_resource", arguments, artifacts)

    def content(result):
        return result["extraction"] if kind == "task" else result

    first = read(2)
    assert content(first)["version"] == 1
    assert "第 2 页" in json.dumps(first, ensure_ascii=False)
    with client.app.state.session_factory() as session:
        revise_current(session, "selected-file", expected_version=1, header_changes={"vendor": "新版本"})
        session.commit()
    with pytest.raises(HTTPException) as conflict:
        read(3, continuation_version)
    assert conflict.value.status_code == 409 and "第一页" in conflict.value.detail
    with pytest.raises(HTTPException) as explicit_conflict:
        read(1, 1)
    assert explicit_conflict.value.status_code == 409
    fresh = read(1)
    assert content(fresh)["version"] == 2
    assert "旧页" in json.dumps(fresh, ensure_ascii=False)
    last = read(3)
    assert content(last)["version"] == 2
    assert content(first)["version"] == 1
    assert all(not key.startswith("_") for result in (first, fresh, last) for key in result)


def test_pending_task_read_does_not_anchor_until_current_data_exists(client):
    artifacts = {}
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=False)
        context = check_context(session, Context(task_id="selected-file"))
        before = execute_tool(session, context, "read_resource", {"kind": "task", "id": "selected-file"}, artifacts)
        assert before["status"] == "processing" and "extraction" not in before
    assert not artifacts.get("_file_read_versions")
    with client.app.state.session_factory() as session:
        complete_selected_file(session)
        session.commit()
    with client.app.state.session_factory() as session:
        after = execute_tool(session, context, "read_resource", {"kind": "file_data", "id": "selected-file"}, artifacts)
    assert after["version"] == 0
    assert artifacts.get("_file_read_versions") == {"selected-file": 0}


@pytest.mark.parametrize("kind", ["task", "file_data"])
def test_file_page_version_count_and_rows_share_one_sqlite_snapshot(client, kind):
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=True)
        revise_current(session, "selected-file", expected_version=0, new_items=[{"amount": 8}, {"amount": 9}])
        session.commit()
        initial = current_snapshot(session, "selected-file")
        engine = session.get_bind()
    other = build_engine(str(engine.url))
    fired = False

    def revise_after_current_select(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if fired or not statement.lstrip().upper().startswith("SELECT") or "file_current_data" not in statement:
            return
        fired = True
        with Session(other) as writer:
            revise_current(writer, "selected-file", expected_version=1, header_changes={"vendor": "并发新值"},
                           deleted_ids=[initial["items"][0]["id"]])
            writer.commit()

    artifacts = {}
    event.listen(engine, "after_cursor_execute", revise_after_current_select)
    try:
        with client.app.state.session_factory() as session:
            result = execute_tool(session, Context(task_id="selected-file"), "read_resource", {
                "kind": kind, "id": "selected-file", "page": 2, "limit": 1,
            }, artifacts)
        content = result["extraction"] if kind == "task" else result
        assert fired and content["version"] == 1
        assert (content["item_count"] if kind == "task" else content["total"]) == 3
        values = content["result"] if kind == "task" else content
        assert values["header"]["vendor"] == "仅所选文件"
        assert (values["items"][0] if kind == "task" else values["items"][0]["values"])["amount"] == 8
        # A new transaction sees the edit and refuses to continue the old pages.
        with client.app.state.session_factory() as session:
            with pytest.raises(HTTPException) as conflict:
                execute_tool(session, Context(task_id="selected-file"), "read_resource", {
                    "kind": kind, "id": "selected-file", "page": 3, "limit": 1,
                }, artifacts)
        assert conflict.value.status_code == 409
    finally:
        event.remove(engine, "after_cursor_execute", revise_after_current_select)
        other.dispose()


def test_cancelled_run_replay_does_not_read_and_new_run_starts_new_file_version(client):
    entered, release = threading.Event(), threading.Event()

    class HeldProvider(ScriptedProvider):
        requests = []
        plan = [[tool_event("old-page", kind="file_data", limit=1)],
                [tool_event("cancelled-page", kind="file_data", page=2, limit=1)]]

        def close(self):
            release.set()

        def stream(self, messages, tools):
            if self.step == 1:
                entered.set()
                assert release.wait(10)
            yield from super().stream(messages, tools)

    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=True)
    client.app.state.conversation_factory = HeldProvider
    profile, request_id = select_profile(client), str(uuid4())
    started = send(client, profile, request_id=request_id, context={"task_id": "selected-file"})
    try:
        assert entered.wait(10)
        assert client.post(f"/api/v1/assistant/runs/{started.json()['run_id']}/cancel").status_code == 200
    finally:
        release.set()
    first = finish(client, started)
    assert first["runs"][0]["status"] == "cancelled"
    assert len(first["tools"]) == 1 and first["tools"][0]["result"]["version"] == 0
    with client.app.state.session_factory() as session:
        revise_current(session, "selected-file", expected_version=0, new_items=[{"amount": 8}])
        session.commit()
    request_count = len(HeldProvider.requests)
    repeated = send(client, profile, request_id=request_id, context={"task_id": "selected-file"})
    assert repeated.status_code == 200 and repeated.json() == started.json()
    assert client.get(f"/api/v1/assistant/runs/{started.json()['run_id']}/events").status_code == 200
    assert len(HeldProvider.requests) == request_count

    class FreshProvider(ScriptedProvider):
        requests = []
        plan = [[tool_event("new-second-page", kind="file_data", page=2, limit=1)]]

    client.app.state.conversation_factory = FreshProvider
    next_run = send(client, profile, thread_id=first["id"], text="新一轮只读第二页。", context={"task_id": "selected-file"})
    detail = finish(client, next_run)
    assert next_run.json()["run_id"] != started.json()["run_id"]
    second = next(call["result"] for call in detail["tools"] if call["result"].get("page") == 2)
    assert second["version"] == 1 and second["items"][0]["values"]["amount"] == 8
    assert "第 2 页" in second["notice"]
    assert "_file_read_versions" not in json.dumps(detail)
    assert "_file_read_versions" not in json.dumps(FreshProvider.requests)


def test_current_page_anchor_is_independent_for_each_selected_file(client):
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=True)
        schema = json.loads(session.get(FileCurrentDataRecord, "selected-file").schema_json)
        session.add(TaskRecord(id="second-file", filename="合成第二份.txt", content_type="text/plain",
            size_bytes=1, sha256="synthetic-second", storage_path="second.txt", status="completed"))
        session.flush()
        initialize_current(session, task_id="second-file", table_id="ledger", header={"vendor": "第二份"},
                           items=[{"amount": 12}, {"amount": 13}], schema=schema)
        session.commit()
    context = Context(task_ids=["selected-file", "second-file"])
    artifacts = {}
    with client.app.state.session_factory() as session:
        execute_tool(session, context, "read_resource", {"kind": "file_data", "id": "selected-file"}, artifacts)
    with client.app.state.session_factory() as session:
        revise_current(session, "selected-file", expected_version=0, header_changes={"vendor": "第一份新值"})
        session.commit()
    with client.app.state.session_factory() as session:
        second = execute_tool(session, context, "read_resource", {
            "kind": "file_data", "id": "second-file", "page": 2, "limit": 1,
        }, artifacts)
        assert second["version"] == 0 and second["items"][0]["values"]["amount"] == 13
        with pytest.raises(HTTPException) as conflict:
            execute_tool(session, context, "read_resource", {"kind": "file_data", "id": "selected-file", "page": 2}, artifacts)
        assert conflict.value.status_code == 409


@pytest.mark.parametrize("selected_rows", [None, [1, 2]])
def test_completion_changes_live_table_read_only_within_grant_and_keeps_prior_snapshot(client, selected_rows):
    entered, release = threading.Event(), threading.Event()

    class HeldProvider(ScriptedProvider):
        requests = []
        first_analysis_id = None
        plan = [[{"type": "tool", "id": "before", "name": "read_data_rows",
                 "arguments": {"table_id": "ledger", "limit": 20}}],
                [{"type": "tool", "id": "after", "name": "read_data_rows",
                  "arguments": {"table_id": "ledger", "limit": 20}}]]

        def stream(self, messages, tools):
            if self.step == 1:
                entered.set()
                assert release.wait(10)
            if self.step == 2:
                self.requests.append(json.loads(json.dumps(messages)))
                first = next(json.loads(message["content"]) for message in messages if message["role"] == "tool")
                type(self).first_analysis_id = first["analysis_id"]
                self.step += 1
                yield {"type": "tool", "id": "old-snapshot", "name": "read_analysis_snapshot",
                       "arguments": {"analysis_id": first["analysis_id"], "limit": 50}}
            else:
                yield from super().stream(messages, tools)

    client.app.state.conversation_factory = HeldProvider
    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=False)
    context = {"table_id": "ledger", **({"row_ids": selected_rows} if selected_rows is not None else {})}
    started = send(client, select_profile(client), context=context)
    try:
        assert entered.wait(10)
        with client.app.state.session_factory() as session:
            complete_selected_file(session)
            session.commit()
    finally:
        release.set()
    detail = finish(client, started)
    reads = [call["result"] for call in detail["tools"] if call["name"] == "read_data_rows"]
    before = next(result for result in reads if result["analysis_id"] == HeldProvider.first_analysis_id)
    after = next(result for result in reads if result["analysis_id"] != HeldProvider.first_analysis_id)
    expected_before, expected_after = (4, 5) if selected_rows is None else (2, 2)
    assert before["source"]["row_count"] == expected_before
    assert after["source"]["row_count"] == expected_after
    assert any(row["task_id"] == "selected-file" for row in after["rows"]) is (selected_rows is None)
    snapshot = next(call["result"] for call in detail["tools"] if call["name"] == "read_analysis_snapshot")
    assert snapshot["snapshot_id"] == before["analysis_id"]
    assert snapshot["source"]["row_count"] == expected_before
    assert len(snapshot["items"]) == expected_before
    assert not any(row["task_id"] == "selected-file" for row in snapshot["items"])


@pytest.mark.parametrize("kind", ["file_data", "task"])
def test_current_page_one_refresh_warning_survives_provider_context_budget(client, kind):
    entered, release = threading.Event(), threading.Event()

    def first_page(identifier):
        return {"type": "tool", "id": identifier, "name": "read_resource",
                "arguments": {"kind": kind, "id": "selected-file", "page": 1, "limit": 1}}

    class HeldProvider(ScriptedProvider):
        requests = []
        plan = [[first_page("old-first-page")], [first_page("fresh-first-page")]]

        def stream(self, messages, tools):
            if self.step == 1:
                entered.set()
                assert release.wait(10)
            yield from super().stream(messages, tools)

    with client.app.state.session_factory() as session:
        add_selected_file(session, ready=True)
        revise_current(session, "selected-file", expected_version=0,
                       header_changes={"vendor": "合成长字段" * 3000})
        if kind == "task":
            session.get(FileCurrentDataRecord, "selected-file").validation_json = json.dumps(
                [{"message": "合成长校验说明" * 3000}])
        session.commit()
    client.app.state.conversation_factory = HeldProvider
    started = send(client, select_profile(client), context={"task_id": "selected-file"})
    try:
        assert entered.wait(10)
        with client.app.state.session_factory() as session:
            revise_current(session, "selected-file", expected_version=1,
                           header_changes={"vendor": "合成新长字段" * 3000})
            if kind == "task":
                session.get(FileCurrentDataRecord, "selected-file").validation_json = json.dumps(
                    [{"message": "合成长校验说明" * 3000}])
            session.commit()
    finally:
        release.set()
    detail = finish(client, started)
    saved = next(call["result"] for call in detail["tools"]
                 if call["result"].get("version", call["result"].get("extraction", {}).get("version")) == 2)
    visible = [json.loads(message["content"]) for message in HeldProvider.requests[-1]
               if message["role"] == "tool"][-1]
    notice = saved["note"] if kind == "task" else saved["notice"]
    assert "旧页" in notice
    assert visible["context_limited"] is True
    assert visible["version"] == 2
    assert "旧页" in json.dumps(visible, ensure_ascii=False)
