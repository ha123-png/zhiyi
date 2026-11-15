"""Numeric proposals must preserve the reviewed value through approval and undo."""

import json
from decimal import localcontext

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from test_assistant import client as ledger_fixture
from test_assistant_rework import decide, plan_run
from document_pipeline_api.models import (
    AssistantToolCall, DataRowRecord, DataRowRevisionRecord, DataTableRecord,
    FileRevisionRecord, TaskRecord,
)
from document_pipeline_api.services.assistant_operations import increment_value
from document_pipeline_api.services.current_data import current_snapshot, initialize_current


client = ledger_fixture
LARGE = 123456789012345678901234567890
SAFE_INTEGER = 2**53 - 1


@pytest.mark.parametrize("value, delta, expected", [
    (LARGE, 0, LARGE),
    (LARGE, 1, LARGE + 1),
    (-LARGE, -1, -LARGE - 1),
    (10**30 - 1, 1, 10**30),
    (LARGE, -LARGE + 1, 1),
    (SAFE_INTEGER + 2, 1, SAFE_INTEGER + 3),
    (0.1, 0.2, 0.3),
    (-1000.5, -0.1, -1000.6),
    (1e-300, 1e-300, 2e-300),
])
def test_increment_preserves_exact_decimal_sum(value, delta, expected):
    # A caller's Decimal precision must not change a previously reviewed plan.
    with localcontext() as context:
        context.prec = 6
        result = increment_value(value, delta, "金额")
        assert result == expected
        assert type(result) is type(expected)
        assert context.prec == 6


@pytest.mark.parametrize("value, delta", [
    ("0.12345678901234567890123456789", 0),
    (LARGE, 0.1),
    (0.1, 1e-17),
    (1, 1e-300),
    ("1e-325", 0),
])
def test_increment_rejects_unrepresentable_decimal_result(value, delta):
    with pytest.raises(HTTPException) as raised:
        increment_value(value, delta, "金额")
    assert raised.value.status_code == 422
    assert "精度" in raised.value.detail


@pytest.mark.parametrize("value", ["1e1000000", "1e-1000000", 10**400])
def test_increment_rejects_values_outside_bounded_storage_contract(value):
    with pytest.raises(HTTPException) as raised:
        increment_value(value, 0, "金额")
    assert raised.value.status_code == 422


def seed_amount(client, value):
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 1)
        row.row_json = json.dumps({**json.loads(row.row_json), "amount": value},
                                  ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        session.commit()


def persisted_amount(client):
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 1)
        return json.loads(row.row_json)["amount"], row.row_version


def wire_value(value):
    return str(value) if type(value) is int and abs(value) > SAFE_INTEGER else value


def amount_change(plan):
    affected = plan["items"][0]["affected"]
    assert len(affected) == 1 and affected[0]["row_id"] == 1
    return affected[0]


@pytest.mark.parametrize("original, delta, expected", [
    (LARGE, 0, LARGE),
    (LARGE, 1, LARGE + 1),
    (-LARGE, -1, -LARGE - 1),
    (10**30 - 1, 1, 10**30),
    (LARGE, -LARGE + 1, 1),
    (1000.5, 0.1, 1000.6),
    (-1000.5, -0.1, -1000.6),
])
def test_exact_preview_approval_history_and_undo(client, original, delta, expected):
    seed_amount(client, original)
    detail = plan_run(client, [{
        "kind": "increment_rows", "table_id": "ledger", "row_ids": [1],
        "changes": {"amount": delta},
    }])
    tool = detail["tools"][0]
    assert tool["status"] == "pending", tool
    preview = amount_change(tool["result"])
    assert preview["before"] == {"amount": wire_value(original)}
    assert preview["after"] == {"amount": wire_value(expected)}
    assert persisted_amount(client) == (original, 1)
    with client.app.state.session_factory() as session:
        saved = json.loads(session.get(AssistantToolCall, tool["id"]).result_json)
        assert amount_change(saved)["after"]["amount"] == expected
        assert type(amount_change(saved)["after"]["amount"]) is type(expected)

    approved = decide(client, tool["id"])
    assert approved.status_code == 200, approved.text
    assert amount_change(approved.json()["result"]) == preview
    assert decide(client, tool["id"]).json() == approved.json()
    changed = expected != original
    assert persisted_amount(client) == (expected, 2 if changed else 1)
    history = client.get("/api/v1/tables/ledger/rows/1/revisions")
    assert history.status_code == 200, history.text
    assert len(history.json()) == int(changed)
    if changed:
        revision = history.json()[0]
        assert revision["before"]["amount"] == wire_value(original)
        assert revision["after"]["amount"] == wire_value(expected)
        with client.app.state.session_factory() as session:
            stored = session.scalar(select(DataRowRevisionRecord).where(
                DataRowRevisionRecord.row_id == 1))
            assert json.loads(stored.before_json)["amount"] == original
            assert json.loads(stored.after_json)["amount"] == expected

    undo = client.post(f"/api/v1/assistant/tools/{tool['id']}/undo")
    assert undo.status_code == 200, undo.text
    refreshed = client.get(f"/api/v1/assistant/threads/{detail['id']}").json()
    undo_tool = next(item for item in refreshed["tools"] if item["id"] == undo.json()["id"])
    undo_change = amount_change(undo_tool["result"])
    assert undo_change["before"] == {"amount": wire_value(expected)}
    assert undo_change["after"] == {"amount": wire_value(original)}
    undone = decide(client, undo.json()["id"])
    assert undone.status_code == 200, undone.text
    assert persisted_amount(client) == (original, 3 if changed else 1)
    history = client.get("/api/v1/tables/ledger/rows/1/revisions").json()
    assert len(history) == 2 * int(changed)
    if changed:
        assert history[-1]["before"]["amount"] == wire_value(expected)
        assert history[-1]["after"]["amount"] == wire_value(original)


@pytest.mark.parametrize("original, delta", [
    (LARGE, 0.1),
    (0.1, 1e-17),
    ("0.12345678901234567890123456789", 0),
])
def test_inexact_preview_cannot_create_a_confirmable_write(client, original, delta):
    seed_amount(client, original)
    detail = plan_run(client, [{
        "kind": "increment_rows", "table_id": "ledger", "row_ids": [1],
        "changes": {"amount": delta},
    }])
    tool = detail["tools"][0]
    assert tool["status"] == "failed", tool
    assert "精度" in json.dumps(tool["result"], ensure_ascii=False)
    assert decide(client, tool["id"]).json()["status"] == "failed"
    assert persisted_amount(client) == (original, 1)
    assert client.get("/api/v1/tables/ledger/rows/1/revisions").json() == []


def test_file_shared_header_and_details_keep_exact_values_in_revision_and_undo(client):
    schema = [
        {"key": "total", "label": "总额", "section": "header", "value_type": "number"},
        {"key": "amount", "label": "金额", "section": "item", "value_type": "number"},
    ]
    with client.app.state.session_factory() as session:
        session.add(DataTableRecord(id="precision-file-table", name="合成文件账本",
            template_key="manual:precision-file", template_version="1", document_kind="custom",
            columns_json=json.dumps(schema)))
        session.add(TaskRecord(id="precision-file", filename="合成精度.txt", content_type="text/plain",
            size_bytes=1, sha256="synthetic-precision-file", storage_path="synthetic-precision.txt",
            status="completed"))
        session.flush()
        initialize_current(session, task_id="precision-file", table_id="precision-file-table",
            header={"total": LARGE}, items=[{"amount": -LARGE}, {"amount": 10.25}], schema=schema)
        session.commit()
        original = current_snapshot(session, "precision-file")
        row_ids = [item["id"] for item in original["items"]]

    detail = plan_run(client, [{
        "kind": "increment_rows", "table_id": "precision-file-table", "row_ids": row_ids,
        "changes": {"total": 1, "amount": 1},
    }], table_ids=["precision-file-table"])
    tool = detail["tools"][0]
    assert tool["status"] == "pending", tool
    affected = tool["result"]["items"][0]["affected"]
    assert [row["after"]["total"] for row in affected] == [str(LARGE + 1)] * 2
    assert [row["after"]["amount"] for row in affected] == [str(-LARGE + 1), 11.25]
    with client.app.state.session_factory() as session:
        assert current_snapshot(session, "precision-file") == original

    approved = decide(client, tool["id"])
    assert approved.status_code == 200, approved.text
    with client.app.state.session_factory() as session:
        current = current_snapshot(session, "precision-file")
        assert current["header"] == {"total": LARGE + 1}
        assert [item["values"]["amount"] for item in current["items"]] == [-LARGE + 1, 11.25]
        revisions = list(session.scalars(select(FileRevisionRecord).where(
            FileRevisionRecord.task_id == "precision-file").order_by(FileRevisionRecord.version)))
        assert len(revisions) == 2
        assert json.loads(revisions[-1].changes_json)["header"]["total"] == {
            "before": LARGE, "after": LARGE + 1,
        }
        revision_id = revisions[-1].id
    revision = client.get(f"/api/v1/tasks/precision-file/revisions/{revision_id}").json()
    assert revision["header"]["total"] == {"before": str(LARGE), "after": str(LARGE + 1)}
    assert revision["total_details"] == 2

    undo = client.post(f"/api/v1/assistant/tools/{tool['id']}/undo")
    assert undo.status_code == 200, undo.text
    undone = decide(client, undo.json()["id"])
    assert undone.status_code == 200, undone.text
    with client.app.state.session_factory() as session:
        restored = current_snapshot(session, "precision-file")
        assert restored["header"] == original["header"]
        assert restored["items"] == original["items"]
        revision = session.scalar(select(FileRevisionRecord).where(
            FileRevisionRecord.task_id == "precision-file").order_by(FileRevisionRecord.version.desc()))
        assert json.loads(revision.changes_json)["header"]["total"] == {
            "before": LARGE + 1, "after": LARGE,
        }
