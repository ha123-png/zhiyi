import json

import pytest
from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import ExtractionRecord, TaskRecord, TemplateTableBindingRecord
from document_pipeline_api.services.data_tables import materialize_task_result


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'data.db'}",
                storage_dir=tmp_path / "uploads",
                queue_enabled=False,
            )
        )
    ) as value:
        yield value


def template(client):
    response = client.post(
        "/api/v1/templates",
        json={
            "name": "采购",
            "fields": [
                {"key": "customer", "label": "客户", "section": "header"},
                {"key": "amount", "label": "金额", "section": "item", "value_type": "number"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def edit(client, current, **changes):
    body = {
        key: current[key]
        for key in (
            "name",
            "description",
            "extra_instructions",
            "fields",
            "validation_rules",
            "deterministic_rules",
            "output_mapping",
            "behavior",
        )
    }
    return client.put(
        f"/api/v1/templates/{current['id']}",
        json={
            **body,
            **changes,
            "expected_version": current["version"],
        },
    )


def extract(client, current, task_id, amount=10):
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id=task_id,
                filename=f"{task_id}.txt",
                content_type="text/plain",
                size_bytes=1,
                sha256=task_id,
                storage_path=f"{task_id}.txt",
                status="completed",
                template_mode="custom",
                template_id=current["id"],
                template_version=current["version"],
            )
        )
        session.flush()
        session.add(
            ExtractionRecord(
                task_id=task_id,
                document_kind="custom",
                template_id=current["id"],
                template_version=current["version"],
                model_name="fixture",
                prompt_version="test",
                elapsed_seconds=0,
                result_json=json.dumps(
                    {"header": {"customer": "甲"}, "items": [{"amount": amount}]}
                ),
                validation_json="[]",
            )
        )
        session.flush()
        materialize_task_result(session, task_id)
        session.commit()
        return session.get(TemplateTableBindingRecord, current["id"]).table_id


def test_template_versions_share_destination_and_historical_labels_are_frozen(client):
    first = template(client)
    table_id = extract(client, first, "A")
    second = edit(
        client,
        first,
        extra_instructions="注意小数",
        fields=[
            {**first["fields"][1], "label": "采购金额"},
            {**first["fields"][0], "label": "采购客户"},
        ],
    )
    assert second.status_code == 200, second.text
    assert extract(client, second.json(), "B") == table_id
    # An older queued job also writes into the same table, without reverting labels.
    assert extract(client, first, "C") == table_id
    data = client.get(f"/api/v1/tables/{table_id}").json()
    assert [column["label"] for column in data["columns"]] == ["采购金额", "采购客户"]
    assert len(data["rows"]) == 3
    current = client.get("/api/v1/tasks/A/result").json()
    assert current["result"]["header"]["customer"] == "甲"
    history = client.get("/api/v1/tasks/A/revisions").json()
    revision = client.get("/api/v1/tasks/A/revisions/" + history["revisions"][0]["id"]).json()
    assert (
        next(column["label"] for column in revision["schema"] if column["key"] == "customer")
        == "客户"
    )


@pytest.mark.parametrize("value", [0, 12])
@pytest.mark.parametrize("change", [{"section": "header"}, {"value_type": "text"}])
def test_populated_identity_cannot_change_section_or_type(client, value, change):
    first = template(client)
    table_id = extract(client, first, "A", amount=value)
    response = edit(client, first, fields=[first["fields"][0], {**first["fields"][1], **change}])
    assert response.status_code == 409, response.text
    assert client.get(f"/api/v1/templates/{first['id']}").json()["version"] == 1
    assert client.get(f"/api/v1/tables/{table_id}").json()["rows"][0]["values"]["amount"] == value


def test_pending_file_protects_contract_before_first_extraction(client):
    first = template(client)
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="pending",
                filename="a.txt",
                content_type="text/plain",
                size_bytes=1,
                sha256="pending",
                storage_path="a.txt",
                template_mode="custom",
                template_id=first["id"],
                template_version=1,
                status="queued",
            )
        )
        session.commit()
    response = edit(
        client, first, fields=[first["fields"][0], {**first["fields"][1], "section": "header"}]
    )
    assert response.status_code == 409, response.text


def test_remove_future_field_keeps_old_values_delete_rule_keeps_facts(client):
    first = template(client)
    table_id = extract(client, first, "A")
    second = edit(client, first, fields=[first["fields"][0]])
    assert second.status_code == 200, second.text
    data = client.get(f"/api/v1/tables/{table_id}").json()
    assert data["rows"][0]["values"]["amount"] == 10
    assert any(column["key"] == "amount" for column in data["columns"])
    assert client.delete(f"/api/v1/templates/{first['id']}").status_code == 204
    assert client.get(f"/api/v1/tables/{table_id}").json()["rows"] == data["rows"]
    assert client.get("/api/v1/tasks/A/result").json()["result"]["items"][0]["amount"] == 10
    assert client.get(f"/api/v1/templates/{first['id']}/versions/1").status_code == 200


def test_delete_destination_clears_binding_and_new_file_does_not_resurrect_old(client):
    first = template(client)
    previous = extract(client, first, "A")
    assert client.delete(f"/api/v1/tables/{previous}").status_code == 204
    with client.app.state.session_factory() as session:
        assert session.get(TemplateTableBindingRecord, first["id"]) is None
    new = extract(client, first, "B")
    assert new != previous
    assert client.get("/api/v1/tasks/A/result").json()["has_current_data"] is False
    assert [row["task_id"] for row in client.get(f"/api/v1/tables/{new}").json()["rows"]] == ["B"]
