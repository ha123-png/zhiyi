"""Catalog counts must describe the same authorized facts as record reads."""
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import DataRowRecord, DataTableRecord, DataViewRecord, TaskRecord
from document_pipeline_api.services.assistant_tools import Context, check_context, execute_tool
from document_pipeline_api.services.current_data import initialize_current


@pytest.fixture
def catalog_data(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    schema = [{"key": "customer", "label": "Customer", "section": "header", "value_type": "text"},
              {"key": "name", "label": "Name", "section": "item", "value_type": "text"}]
    with Session(engine) as session:
        for table_id in ("selected", "other"):
            session.add(DataTableRecord(id=table_id, name=table_id, template_key=table_id,
                                        template_version="1", document_kind="custom", columns_json=json.dumps(schema)))
        for task_id in ("file-a", "file-b"):
            session.add(TaskRecord(id=task_id, filename=f"{task_id}.txt", content_type="text/plain",
                                   size_bytes=1, sha256=task_id, storage_path=f"{task_id}.txt", status="completed"))
        session.flush()
        for task_id, customer in (("file-a", "Alpha"), ("file-b", "Beta")):
            initialize_current(session, task_id=task_id, table_id="selected", header={"customer": customer},
                               items=[{"name": "shared"}, {"name": "hidden"}], schema=schema)
        session.add_all([
            DataRowRecord(table_id="selected", item_index=index, row_json=json.dumps({"__row_group": "group", "name": f"group-{index}"}))
            for index in (1, 2)])
        session.add(DataRowRecord(table_id="other", item_index=1, row_json='{"name":"other"}'))
        session.add(DataViewRecord(id="alpha-view", table_id="selected", name="Alpha", field_key="customer", field_value_json='"Alpha"'))
        session.commit()
        first_id = session.scalar(select(DataRowRecord.id).where(DataRowRecord.task_id == "file-a").order_by(DataRowRecord.id))
    yield engine, first_id
    engine.dispose()


@pytest.mark.parametrize("selection,expected", [
    ({"row_ids": "first"}, 1),
    ({"row_ids": []}, 0),
    ({"search": "shared"}, 2),
    ({"view_id": "alpha-view"}, 2),
    ({"source_keys": ['{"id":"file-a","kind":"file"}']}, 2),
    ({"source_keys": ['{"id":"group","kind":"group"}']}, 2),
    ({"source_keys": []}, 0),
    ({"task_id": "file-a"}, 2),
    ({"task_id": "file-a", "row_ids": "first"}, 1),
    ({"view_id": "alpha-view", "search": "shared"}, 1),
    ({}, 6),
])
def test_catalog_count_matches_reads_and_analysis_for_scope(catalog_data, selection, expected):
    engine, first_id = catalog_data
    selection = {**selection}
    if selection.get("row_ids") == "first":
        selection["row_ids"] = [first_id]
    with Session(engine) as session:
        context = check_context(session, Context(table_id="selected", table_ids=["other"], mode="read", **selection))
        catalog = execute_tool(session, context, "catalog", {"kind": "tables"}, {})
        rows = execute_tool(session, context, "read_data_rows", {"table_id": "selected"}, {})
        analysis = execute_tool(session, context, "analyze_data_table", {"table_id": "selected"}, {})
        counts = {item["id"]: item["rows"] for item in catalog["items"]}
        assert counts["selected"] == rows["source"]["row_count"] == analysis["totals"]["count:*"] == expected
        assert counts["other"] == 1  # An explicitly added whole table retains its own full grant.


def test_catalog_task_identities_remain_limited_to_selected_rows(catalog_data):
    engine, first_id = catalog_data
    with Session(engine) as session:
        context = Context(table_id="selected", row_ids=[first_id], mode="read")
        catalog = execute_tool(session, context, "catalog", {"kind": "tasks"}, {})
        assert [item["id"] for item in catalog["items"]] == ["file-a"]
        workspace = execute_tool(session, Context(workspace=True, mode="read"), "catalog", {"kind": "tables"}, {})
        assert {item["id"]: item["rows"] for item in workspace["items"]} == {"selected": 6, "other": 1}
