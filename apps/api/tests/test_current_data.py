import json

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import DataRowRecord, DataTableRecord, FileRevisionRecord, TaskRecord
from document_pipeline_api.services.file_revisions import revision_details
from document_pipeline_api.services.current_data import (
    create_edit_session, current_snapshot, initialize_current, recover_edit_sessions,
    revise_current, row_values, row_values_expression, seal_edit_session,
)


SCHEMA = [
    {"key": "customer", "label": "客户", "section": "header", "value_type": "text"},
    {"key": "date", "label": "日期", "section": "header", "value_type": "date"},
    {"key": "product", "label": "商品", "section": "item", "value_type": "text"},
    {"key": "quantity", "label": "数量", "section": "item", "value_type": "number"},
]


@pytest.fixture
def engine(tmp_path):
    value = build_engine(f"sqlite:///{tmp_path / 'current.db'}")
    Base.metadata.create_all(value)
    with Session(value) as session:
        session.add(DataTableRecord(id="table", name="测试", template_key="test", template_version="1", document_kind="custom"))
        for task_id in ("A", "B"):
            session.add(TaskRecord(id=task_id, filename=f"{task_id}.png", content_type="image/png",
                                   size_bytes=1, sha256=task_id, storage_path=f"{task_id}.png", status="completed"))
        session.flush()
        for task_id in ("A", "B"):
            initialize_current(session, task_id=task_id, table_id="table", header={"customer": "甲"},
                               items=[{"product": f"商品{index}", "quantity": index} for index in range(1, 4)],
                               schema=SCHEMA)
        session.commit()
    yield value
    value.dispose()


def revisions(session, task_id):
    return session.scalars(select(FileRevisionRecord).where(FileRevisionRecord.task_id == task_id)
                           .order_by(FileRevisionRecord.version)).all()


def revision_changes(session, record):
    result = revision_details(session, record.task_id, record.id, page_size=50)
    return {"header": result["header"], "items": {str((item["after"] or item.get("deleted_before") or item["before"])["id"]): item for item in result["items"]}}


def test_header_stored_once_and_projected_for_every_detail(engine):
    with Session(engine) as session:
        current = current_snapshot(session, "A")
        revise_current(session, "A", expected_version=0, header_changes={"customer": "乙"})
        session.commit()
        rows = session.scalars(select(DataRowRecord).where(DataRowRecord.task_id == "A")).all()
        assert all("customer" not in json.loads(row.row_json) for row in rows)
        assert {row_values(row)["customer"] for row in rows} == {"乙"}
        assert current_snapshot(session, "A")["header"] == {"customer": "乙"}
        assert len(current["items"]) == 3
        assert len(revisions(session, "A")) == 2
        changes = revision_changes(session, revisions(session, "A")[-1])
        assert changes["header"] == {"customer": {"before": "甲", "after": "乙"}}
        assert changes["items"] == {}
        assert session.scalar(select(func.count()).select_from(DataRowRecord).where(
            func.json_extract(row_values_expression(), "$.customer") == "乙")) == 3


def test_one_file_edit_has_one_revision_with_full_deleted_detail(engine):
    with Session(engine) as session:
        initial = current_snapshot(session, "A")
        first, second, third = initial["items"]
        revise_current(session, "A", expected_version=0,
                       header_changes={"customer": "乙", "date": "2026-09-16"},
                       detail_changes={first["id"]: {"quantity": 10}, second["id"]: {"quantity": 20}},
                       deleted_ids=[third["id"]])
        session.commit()
        changes = revision_changes(session, revisions(session, "A")[-1])
        assert len(revisions(session, "A")) == 2
        assert len(changes["header"]) == 2
        assert changes["items"][str(third["id"])] == {"before": third, "after": None}
        assert len(current_snapshot(session, "A")["items"]) == 2


def test_warehouse_session_groups_by_file_and_durably_saves_each_edit(engine):
    with Session(engine) as session:
        editing = create_edit_session(session, owner="process-a")
        session_id = editing.id
        session.commit()
    for task_id, times in (("A", 5), ("B", 3)):
        for index in range(times):
            with Session(engine) as session:
                revise_current(session, task_id, expected_version=index,
                               header_changes={"customer": f"客户{index}"}, edit_session_id=session_id)
                session.commit()
            with Session(engine) as check:
                assert current_snapshot(check, task_id)["header"]["customer"] == f"客户{index}"
    with Session(engine) as session:
        seal_edit_session(session, session_id)
        session.commit()
        for task_id, final in (("A", "客户4"), ("B", "客户2")):
            history = revisions(session, task_id)
            assert len(history) == 2
            assert history[-1].sealed_at is not None
            assert json.loads(history[-1].changes_json)["header"]["customer"] == {"before": "甲", "after": final}


def test_crash_recovery_seals_once_without_losing_values(engine):
    with Session(engine) as session:
        editing = create_edit_session(session, owner="crashed")
        revise_current(session, "A", expected_version=0, header_changes={"customer": "已落盘"}, edit_session_id=editing.id)
        session.commit()
    with Session(engine) as session:
        assert recover_edit_sessions(session, owner="new-process") == 1
        session.commit()
    with Session(engine) as session:
        assert recover_edit_sessions(session, owner="new-process") == 0
        assert len(revisions(session, "A")) == 2
        assert revisions(session, "A")[-1].sealed_at is not None
        assert current_snapshot(session, "A")["header"]["customer"] == "已落盘"


def test_delete_last_detail_clears_header_preserves_file_and_history(engine):
    with Session(engine) as session:
        before = current_snapshot(session, "A")
        revise_current(session, "A", expected_version=0, deleted_ids=[row["id"] for row in before["items"]])
        session.commit()
        assert current_snapshot(session, "A") == {"header": {}, "items": [], "has_data": False, "version": 1}
        assert session.get(TaskRecord, "A") is not None
        assert len(revisions(session, "A")) == 2
        assert len(revision_changes(session, revisions(session, "A")[-1])["items"]) == 3
        with pytest.raises(HTTPException, match="409"):
            initialize_current(session, task_id="A", table_id="table", header={"customer": "甲"}, items=[], schema=SCHEMA)


def test_manual_text_is_preserved_and_stale_edit_is_rejected_without_partial_changes(engine):
    with Session(engine) as session:
        first_id = current_snapshot(session, "A")["items"][0]["id"]
        revise_current(session, "A", expected_version=0, header_changes={"customer": "乙"},
                       detail_changes={first_id: {"quantity": "不是数字"}})
        saved = current_snapshot(session, "A")
        assert saved["header"]["customer"] == "乙"
        assert saved["items"][0]["values"]["quantity"] == "不是数字"
        with pytest.raises(HTTPException) as error:
            revise_current(session, "A", expected_version=0, header_changes={"customer": "丙"})
        assert error.value.status_code == 409
        session.commit()
        assert len(revisions(session, "A")) == 2


def test_noop_and_reverted_open_edit_do_not_create_versions(engine):
    with Session(engine) as session:
        revise_current(session, "A", expected_version=0, header_changes={"customer": "甲"})
        assert len(revisions(session, "A")) == 1
        editing = create_edit_session(session, owner="process")
        revise_current(session, "A", expected_version=0, header_changes={"customer": "乙"}, edit_session_id=editing.id)
        revise_current(session, "A", expected_version=1, header_changes={"customer": "甲"}, edit_session_id=editing.id)
        seal_edit_session(session, editing.id)
        session.commit()
        assert len(revisions(session, "A")) == 1


def test_transaction_rollback_reverts_values_and_revision_together(engine):
    with Session(engine) as session:
        revise_current(session, "A", expected_version=0, header_changes={"customer": "不应保存"})
        session.rollback()
    with Session(engine) as session:
        assert current_snapshot(session, "A")["header"]["customer"] == "甲"
        assert len(revisions(session, "A")) == 1


def test_delete_after_edit_keeps_round_start_and_complete_deleted_value(engine):
    with Session(engine) as session:
        first = current_snapshot(session, "A")["items"][0]
        editing = create_edit_session(session, owner="process")
        revise_current(session, "A", expected_version=0,
                       detail_changes={first["id"]: {"quantity": 12}}, edit_session_id=editing.id)
        session.commit()
        revise_current(session, "A", expected_version=1, deleted_ids=[first["id"]], edit_session_id=editing.id)
        session.commit()
        revise_current(session, "A", expected_version=2, header_changes={"customer": "乙"}, edit_session_id=editing.id)
        seal_edit_session(session, editing.id)
        session.commit()
        change = revision_changes(session, revisions(session, "A")[-1])["items"][str(first["id"])]
        assert change["before"] == first
        assert change["after"] is None
        assert change["deleted_before"] == {**first, "values": {"product": "商品1", "quantity": 12}}
        assert len(revisions(session, "A")) == 2


def test_new_detail_replaces_header_carrier_and_delete_keeps_last_content(engine):
    with Session(engine) as session:
        session.add(TaskRecord(id="C", filename="C.txt", content_type="text/plain", size_bytes=1,
                               sha256="C", storage_path="C.txt", status="completed"))
        session.flush()
        initialize_current(session, task_id="C", table_id="table", header={"customer": "甲"}, items=[], schema=SCHEMA)
        editing = create_edit_session(session, owner="process")
        revise_current(session, "C", expected_version=0, new_items=[{"product": "新增", "quantity": 2}], edit_session_id=editing.id)
        session.commit()
        rows = session.scalars(select(DataRowRecord).where(DataRowRecord.task_id == "C")).all()
        assert len(rows) == 1 and not rows[0].is_header_only
        revise_current(session, "C", expected_version=1, deleted_ids=[rows[0].id], edit_session_id=editing.id)
        seal_edit_session(session, editing.id)
        session.commit()
        assert current_snapshot(session, "C")["has_data"] is False
        change = revision_changes(session, revisions(session, "C")[-1])["items"][str(rows[0].id)]
        assert change["before"] is None and change["after"] is None
        assert change["deleted_before"]["values"] == {"product": "新增", "quantity": 2}


def test_revision_only_loads_affected_detail_objects(engine):
    from sqlalchemy import event
    loaded = []
    def record_load(session, instance):
        if isinstance(instance, DataRowRecord):
            loaded.append(instance.id)
    with Session(engine) as session:
        row_id = session.scalar(select(DataRowRecord.id).where(DataRowRecord.task_id == "A").limit(1))
        event.listen(session, "loaded_as_persistent", record_load)
        revise_current(session, "A", expected_version=0, detail_changes={row_id: {"quantity": 20}})
        session.commit()
        assert loaded == [row_id]
