"""Synthetic scope, model-response growth and concurrent-snapshot regressions."""
import asyncio
import json
import time
import tracemalloc

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import DataRowRecord, DataTableRecord, DataViewRecord, ExtractionRecord, TaskRecord, TemplateRecord, TemplateVersionRecord
from document_pipeline_api.services.assistant_operations import Plan, apply_plan, preview_plan
from document_pipeline_api.services.assistant_tools import Context, Resource
from document_pipeline_api.services.current_data import current_snapshot, initialize_current, revise_current, row_values
from document_pipeline_api.services.extraction import get_extraction
from document_pipeline_api.services.task_result_reads import read_task_result_page, read_task_result_value


SCHEMA = [
    {"key": "customer", "label": "客户", "section": "header", "value_type": "text"},
    {"key": "product", "label": "商品", "section": "item", "value_type": "text"},
    {"key": "quantity", "label": "数量", "section": "item", "value_type": "number"},
]


def seed(engine, size=3, *, independent=False, schema=None, items=None, header=None):
    schema = schema or SCHEMA
    header = {"customer": "原客户"} if header is None else header
    items = [{"product": f"合成商品{index}", "quantity": index} for index in range(size)] if items is None else items
    with Session(engine) as session:
        session.add(DataTableRecord(id="table", name="合成表", template_key="test-boundary", template_version="1",
                                   document_kind="custom", columns_json=json.dumps(schema)))
        session.flush()
        if independent:
            for index, values in enumerate(items, 1):
                session.add(DataRowRecord(table_id="table", item_index=index,
                    row_json=json.dumps({**header, **values, "__row_group": "synthetic-import"})))
        else:
            session.add(TemplateRecord(id="template"))
            session.flush()
            session.add(TemplateVersionRecord(template_id="template", version=1, name="合成模板",
                                              fields_json=json.dumps(schema)))
            session.add(TaskRecord(id="file", filename="合成文件.txt", content_type="text/plain", size_bytes=1,
                                   sha256="synthetic", storage_path="file.txt", status="completed"))
            session.flush()
            initialize_current(session, task_id="file", table_id="table", header=header, items=items, schema=schema)
            session.add(ExtractionRecord(task_id="file", document_kind="custom", model_name="synthetic",
                template_id="template", template_version=1,
                prompt_version="test", elapsed_seconds=0, result_json=json.dumps({"header": header, "items": items}),
                validation_json="[]"))
        session.commit()


@pytest.fixture
def engine(tmp_path):
    value = build_engine(f"sqlite:///{tmp_path / 'synthetic.db'}")
    Base.metadata.create_all(value)
    yield value
    value.dispose()


def plan():
    return Plan(title="修改客户", operations=[{
        "kind": "update_rows", "table_id": "table", "row_ids": [1], "changes": {"customer": "新客户"},
    }])


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("mode", ["read", "assist", "delegate"])
@pytest.mark.parametrize("scope", ["rows", "search", "group"])
def test_partial_grants_never_expand_shared_header(engine, independent, mode, scope):
    seed(engine, independent=independent)
    with Session(engine) as session:
        options = {"row_ids": [1]} if scope == "rows" else {"search": "合成商品0"}
        if scope == "group":
            session.add(DataViewRecord(id="group", table_id="table", name="单条分组",
                                      field_key="product", field_value_json=json.dumps("合成商品0")))
            session.commit()
            options = {"view_id": "group"}
        with pytest.raises(HTTPException) as raised:
            preview_plan(session, Context(table_id="table", mode=mode, **options), plan())
        assert raised.value.status_code == 403
        assert {row_values(row)["customer"] for row in session.scalars(select(DataRowRecord))} == {"原客户"}


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("scope", ["rows", "source", "table", "search"])
def test_complete_source_can_modify_shared_header(engine, independent, scope):
    from document_pipeline_api.api.assistant import FlushOnlySession
    seed(engine, independent=independent)
    options = {"row_ids": [1, 2, 3]} if scope == "rows" else {}
    if scope == "source":
        options = {"source_keys": [json.dumps({"id": "synthetic-import" if independent else "file",
                                               "kind": "group" if independent else "file"})]}
    if scope == "search":
        options = {"search": "合成商品"}
    with Session(engine) as session:
        context = Context(table_id="table", mode="delegate", **options)
        preview = preview_plan(session, context, plan())
        assert [row["row_id"] for row in preview["items"][0]["affected"]] == [1, 2, 3]
        apply_plan(FlushOnlySession(session), context, preview, None)
        session.commit()
        session.expire_all()
        assert {row_values(row)["customer"] for row in session.scalars(select(DataRowRecord))} == {"新客户"}


def test_approval_rechecks_narrowed_scope(engine):
    seed(engine)
    with Session(engine) as session:
        preview = preview_plan(session, Context(table_id="table"), plan())
        with pytest.raises(HTTPException) as raised:
            apply_plan(session, Context(table_id="table", row_ids=[1]), preview, None)
        assert raised.value.status_code == 403
        assert current_snapshot(session, "file")["header"] == {"customer": "原客户"}


def test_removed_legacy_history_is_not_a_model_resource():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Resource(kind="legacy_revisions", id="file")
    with pytest.raises(ValidationError):
        Resource(kind="file_revisions", id="file", legacy_id="review:1")


@pytest.mark.parametrize("size", [1000, 10000])
def test_model_result_default_is_bounded_and_paging_is_complete(engine, size, record_property):
    seed(engine, size)
    tracemalloc.start()
    started = time.perf_counter()
    with Session(engine) as session:
        first = read_task_result_page(session, "file")
    elapsed, peak = time.perf_counter() - started, tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    encoded = json.dumps(first, ensure_ascii=False).encode()
    record_property("default_read_seconds", elapsed)
    record_property("default_read_peak_bytes", peak)
    record_property("default_response_bytes", len(encoded))
    assert len(encoded) < 6000
    assert peak < 4_000_000
    assert first["total"] == size and first["next_page"] == 2
    assert len(first["result"]["items"]) == 3
    assert "original_result" not in first
    identities, values = [], []
    for page in range(1, size // 20 + 1):
        with Session(engine) as session:
            result = read_task_result_page(session, "file", page=page, page_size=20, expected_version=0)
        identities.extend(result["detail_ids"])
        values.extend(row["quantity"] for row in result["result"]["items"])
    assert len(identities) == len(set(identities)) == size
    assert values == list(range(size))
    assert result["has_more"] is False
    with Session(engine) as session:
        initial = read_task_result_page(session, "file", view="initial", page=size // 20, page_size=20)
    assert initial["view"] == "initial" and initial["total"] == size
    assert [row["quantity"] for row in initial["result"]["items"]] == list(range(size - 20, size))


@pytest.mark.parametrize("page,page_size,field_page", [(0, 3, 1), (1, 21, 1), (1, 0, 1), (1, 3, 0)])
def test_service_rejects_invalid_page_sizes(engine, page, page_size, field_page):
    with Session(engine) as session, pytest.raises(HTTPException) as raised:
        read_task_result_page(session, "file", page=page, page_size=page_size, field_page=field_page)
    assert raised.value.status_code == 422


def test_wide_long_values_are_explicitly_truncated_and_exactly_readable(engine):
    schema = [{"key": f"field{i}", "label": f"字段{i}", "section": "item", "value_type": "text"}
              for i in range(35)]
    long = "长字段🙂\n" * 1000
    seed(engine, schema=schema, header={}, items=[{f"field{i}": long for i in range(35)} for _ in range(3)])
    fields = {}
    with Session(engine) as session:
        for page in (1, 2, 3):
            result = read_task_result_page(session, "file", field_page=page, expected_version=0)
            fields.update(result["result"]["items"][0])
            assert len(json.dumps(result, ensure_ascii=False).encode()) < 35000
            assert result["truncated_paths"]
        assert set(fields) == {f"field{i}" for i in range(35)}
        chunks, offset = [], 0
        while offset is not None:
            chunk = read_task_result_value(session, "file", ["items", 0, "field34"], offset=offset,
                                           limit=751, expected_version=0)
            assert len(chunk["value"]) <= 751
            chunks.append(chunk["value"])
            offset = chunk["next_offset"]
        assert "".join(chunks) == long


def test_exact_scalar_read_preserves_types_and_rejects_paths(engine):
    seed(engine)
    with Session(engine) as session:
        value = read_task_result_value(session, "file", ["items", 2, "quantity"], limit=1, expected_version=0)
        assert value["value"] == 2
        for path in (["header"], ["items", -1, "product"], ["items", "0", "product"], ["private", "key"]):
            with pytest.raises(HTTPException):
                read_task_result_value(session, "file", path, expected_version=0)
        with pytest.raises(HTTPException) as raised:
            read_task_result_value(session, "file", ["header", "customer"])
        assert raised.value.status_code == 422


@pytest.mark.parametrize("options", [{"page": 2}, {"field_page": 2}])
def test_continuing_current_pages_requires_a_version_not_a_prompt_hint(engine, options):
    seed(engine)
    with Session(engine) as session:
        with pytest.raises(HTTPException) as raised:
            read_task_result_page(session, "file", **options)
        assert raised.value.status_code == 422 and "expected_version" in raised.value.detail
        # Immutable extraction evidence does not have a live revision to mix.
        assert read_task_result_page(session, "file", view="initial", **options)["view"] == "initial"


def test_current_pagination_and_value_reject_version_changes_initial_stays_evidence(engine):
    seed(engine)
    with Session(engine) as session:
        revise_current(session, "file", expected_version=0, header_changes={"customer": "新客户"}, deleted_ids=[2])
        session.commit()
    with Session(engine) as session:
        for reader, args in ((read_task_result_page, {}), (read_task_result_value, {"path": ["header", "customer"]})):
            with pytest.raises(HTTPException) as raised:
                reader(session, "file", expected_version=0, **args)
            assert raised.value.status_code == 409
        current = read_task_result_page(session, "file")
        initial = read_task_result_page(session, "file", view="initial")
        assert current["result"]["header"]["customer"] == "新客户" and current["total"] == 2
        assert initial["result"]["header"]["customer"] == "原客户" and initial["total"] == 3


@pytest.mark.parametrize("reader", ["full", "paged"])
def test_concurrent_edit_cannot_mix_header_ids_count_and_version(engine, reader, monkeypatch):
    from document_pipeline_api.services import current_data
    seed(engine)
    other = build_engine(str(engine.url))
    fired, snapshots = False, 0
    original = current_data.current_snapshot

    def counted(*args, **kwargs):
        nonlocal snapshots
        snapshots += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(current_data, "current_snapshot", counted)

    def commit_edit(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if fired or not statement.lstrip().upper().startswith("SELECT") or "extractions" not in statement:
            return
        fired = True
        with Session(other) as writer:
            revise_current(writer, "file", expected_version=0, header_changes={"customer": "新客户"},
                           deleted_ids=[2], new_items=[{"product": "新明细", "quantity": 99}])
            writer.commit()

    event.listen(engine, "after_cursor_execute", commit_edit)
    try:
        with Session(engine) as session:
            result = get_extraction(session, "file").model_dump(mode="json") if reader == "full" else read_task_result_page(session, "file")
        assert fired
        assert result["result"]["header"]["customer"] == "原客户"
        assert result["detail_ids"] == [1, 2, 3]
        assert result["review_version"] == 0
        assert len(result["result"]["items"]) == len(result["detail_ids"]) == 3
        # The writer records only affected deltas. The full reader builds one
        # snapshot; the paged reader does not load the complete current file.
        assert snapshots == (1 if reader == "full" else 0)
        with Session(engine) as session:
            fresh = read_task_result_page(session, "file")
        assert fresh["result"]["header"]["customer"] == "新客户"
        assert fresh["detail_ids"] == [1, 3, 4] and fresh["review_version"] == 1
    finally:
        event.remove(engine, "after_cursor_execute", commit_edit)
        other.dispose()


def test_mcp_contract_calls_bounded_current_and_explicit_evidence(tmp_path):
    from document_pipeline_api.mcp_server import create_mcp_server
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'mcp.db'}", storage_dir=tmp_path / "uploads")
    server = create_mcp_server(settings, result_read_enabled=True)
    engine = build_engine(settings.database_url)
    seed(engine, 1000)
    async def journey():
        current = await server.call_tool("get_task_result", {"task_id": "file"})
        initial = await server.call_tool("get_task_evidence", {"task_id": "file"})
        value = await server.call_tool("get_task_result_value", {"task_id": "file", "path": ["header", "customer"], "expected_version": 0})
        return current, initial, value
    try:
        current, initial, value = asyncio.run(journey())
        assert "original_result" not in str(current)
        assert len(str(current)) < 10000
        assert "initial" in str(initial) and "原客户" in str(value)
    finally:
        engine.dispose()


def test_mcp_file_reads_have_explicit_scope_and_revoke_together(tmp_path):
    from document_pipeline_api.mcp_server import create_mcp_server
    from document_pipeline_api.services.integration_config import write_integration_config
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'mcp.db'}", storage_dir=tmp_path / "uploads")
    write_integration_config(tmp_path, {"mcp_result_read": True})
    ungranted = create_mcp_server(settings, result_read_enabled=True, data_read_enabled=True, allowed_table_ids=())
    server = create_mcp_server(settings, result_read_enabled=True, data_read_enabled=True, allowed_table_ids=("table",))
    engine = build_engine(settings.database_url)
    seed(engine)
    calls = [("get_task_result", {"task_id": "file"}),
             ("get_task_evidence", {"task_id": "file"}),
             ("get_task_result_value", {"task_id": "file", "path": ["header", "customer"], "expected_version": 0})]
    async def permitted():
        for name, arguments in calls:
            await server.call_tool(name, arguments)
            with pytest.raises(Exception, match="授权范围"):
                await ungranted.call_tool(name, arguments)
        with pytest.raises(Exception, match="授权范围"):
            await ungranted.call_tool("get_data_table", {"table_id": "table"})
    async def revoked():
        for name, arguments in calls:
            with pytest.raises(Exception, match="权限已变更"):
                await server.call_tool(name, arguments)
    try:
        asyncio.run(permitted())
        write_integration_config(tmp_path, {"mcp_result_read": False})
        asyncio.run(revoked())
        write_integration_config(tmp_path, {"mcp_result_read": True})
        asyncio.run(revoked())
    finally:
        engine.dispose()
