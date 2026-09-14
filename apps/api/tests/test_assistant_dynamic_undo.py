"""Changing a search/group value does not invalidate its authorized inverse."""

import json

import pytest
from fastapi import HTTPException

from test_assistant import client as ledger_fixture
from test_assistant_rework import plan_run, decide
from document_pipeline_api.models import DataRowRecord, DataViewRecord
from document_pipeline_api.services.assistant_tools import Context
from document_pipeline_api.services.assistant_operations import preview_undo

client = ledger_fixture


def filtered_edit(client, *, all_in_scope=True):
    with client.app.state.session_factory() as session:
        for identifier in (1, 2):
            row = session.get(DataRowRecord, identifier)
            row.row_json = json.dumps({**json.loads(row.row_json), "vendor": "原供应方"})
        session.commit()
    detail = plan_run(client, [{
        "kind": "update_rows", "table_id": "ledger", "all_in_scope": all_in_scope,
        "row_ids": [] if all_in_scope else [1, 2], "changes": {"vendor": "新供应方"},
    }], table_id="ledger", search="原供应方")
    tool = detail["tools"][0]
    assert tool["status"] == "pending", detail
    return tool["id"]


def vendors(client):
    with client.app.state.session_factory() as session:
        return [json.loads(session.get(DataRowRecord, i).row_json)["vendor"] for i in (1, 2, 3)]


@pytest.mark.parametrize("all_in_scope", [True, False])
def test_edit_that_exits_search_can_be_confirmed_and_undone(client, all_in_scope):
    identifier = filtered_edit(client, all_in_scope=all_in_scope)
    approved = decide(client, identifier)
    assert approved.status_code == 200, approved.text
    assert vendors(client) == ["新供应方", "新供应方", "B"]
    execution = approved.json()["result"]["execution"]
    assert execution["can_undo"]
    assert not any(key.startswith("scope:") for key in execution["undo_guards"])
    assert "row:1" in execution["undo_guards"] and "row:2" in execution["undo_guards"]
    undo = client.post(f"/api/v1/assistant/tools/{identifier}/undo")
    assert undo.status_code == 200, undo.text
    applied = decide(client, undo.json()["id"])
    assert applied.status_code == 200, applied.text
    assert vendors(client) == ["原供应方", "原供应方", "B"]


def test_changed_original_rows_still_prevent_undo(client):
    identifier = filtered_edit(client)
    assert decide(client, identifier).status_code == 200
    with client.app.state.session_factory() as session:
        row = session.get(DataRowRecord, 1)
        row.row_json = json.dumps({**json.loads(row.row_json), "vendor": "用户后来修改"})
        row.row_version += 1
        session.commit()
    undo = client.post(f"/api/v1/assistant/tools/{identifier}/undo")
    assert undo.status_code == 409
    assert vendors(client) == ["用户后来修改", "新供应方", "B"]


@pytest.mark.parametrize("context", [
    Context(table_id="ledger", table_ids=["ledger"], search="2026-02-01"),
    Context(table_id="ledger", table_ids=["ledger"], search="原供应方", row_ids=[3]),
    Context(table_id="ledger", table_ids=["ledger"], search="原供应方", mode="read"),
])
def test_model_undo_in_different_or_readonly_scope_cannot_restore_original_roots(client, context):
    identifier = filtered_edit(client)
    assert decide(client, identifier).status_code == 200
    with client.app.state.session_factory() as session:
        with pytest.raises(HTTPException) as captured:
            preview_undo(session, context, identifier)
        assert captured.value.status_code == 403
    assert vendors(client) == ["新供应方", "新供应方", "B"]


def test_scope_membership_guard_still_rejects_new_matching_rows_before_approval(client):
    identifier = filtered_edit(client)
    with client.app.state.session_factory() as session:
        session.add(DataRowRecord(table_id="ledger", item_index=9, row_json=json.dumps({
            "vendor": "原供应方", "amount": 20,
        })))
        session.commit()
    assert decide(client, identifier).status_code == 409
    assert vendors(client) == ["原供应方", "原供应方", "B"]


@pytest.mark.parametrize("change_definition", [False, True])
def test_group_value_can_be_restored_but_changed_group_definition_cannot_expand_undo(client, change_definition):
    with client.app.state.session_factory() as session:
        session.add(DataViewRecord(id="vendor-group", table_id="ledger", name="A分组",
                                  field_key="vendor", field_value_json=json.dumps("A")))
        session.commit()
    detail = plan_run(client, [{
        "kind": "update_rows", "table_id": "ledger", "all_in_scope": True,
        "changes": {"vendor": "新供应方"},
    }], table_id="ledger", view_id="vendor-group")
    identifier = detail["tools"][0]["id"]
    assert decide(client, identifier).status_code == 200
    assert vendors(client) == ["新供应方", "新供应方", "B"]
    undo = client.post(f"/api/v1/assistant/tools/{identifier}/undo")
    assert undo.status_code == 200, undo.text
    if change_definition:
        with client.app.state.session_factory() as session:
            session.get(DataViewRecord, "vendor-group").field_value_json = json.dumps("B")
            session.commit()
    result = decide(client, undo.json()["id"])
    assert result.status_code == (409 if change_definition else 200), result.text
    assert vendors(client) == (["新供应方", "新供应方", "B"] if change_definition else ["A", "A", "B"])
