from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TemplateVersionRecord


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_builtin_templates_are_seeded_and_read_only(client: TestClient) -> None:
    response = client.get("/api/v1/templates")

    assert response.status_code == 200
    templates = response.json()
    assert [(item["builtin_key"], item["name"]) for item in templates] == [
        ("invoice", "发票"),
        ("delivery", "送货单"),
    ]
    invoice = templates[0]
    assert invoice["is_system"] is True
    assert invoice["in_smart_pool"] is True  # 内置模板默认在智能匹配预选池内
    assert invoice["fields"][0]["key"] == "seller_name"
    labels = [field["label"] for field in invoice["fields"]]
    assert labels.count("税额") == 1
    assert labels.count("总税额") == 1
    assert labels[:4] == ["销售方", "购买方", "发票号码", "开票日期"]
    assert len(invoice["deterministic_rules"]) >= 1

    update = client.put(
        f"/api/v1/templates/{invoice['id']}",
        json={
            **_editable_body(invoice),
            "expected_version": 1,
        },
    )
    assert update.status_code == 409
    assert update.json()["detail"] == "内置模板不能直接修改，请先复制。"


def test_smart_pool_toggle_updates_membership(client: TestClient) -> None:
    """智能匹配预选池开关：移出池后列表可见 in_smart_pool=false。"""
    templates = client.get("/api/v1/templates").json()
    invoice_id = templates[0]["id"]

    changed = client.put(
        f"/api/v1/templates/{invoice_id}/smart-pool",
        json={"in_smart_pool": False},
    )
    assert changed.status_code == 200
    assert changed.json()["in_smart_pool"] is False

    after = client.get("/api/v1/templates").json()
    assert [item for item in after if item["id"] == invoice_id][0]["in_smart_pool"] is False

    restored = client.put(
        f"/api/v1/templates/{invoice_id}/smart-pool",
        json={"in_smart_pool": True},
    )
    assert restored.status_code == 200
    assert restored.json()["in_smart_pool"] is True


def test_copy_and_update_create_immutable_versions(
    client: TestClient,
) -> None:
    copied = client.post("/api/v1/templates/builtin-delivery/copy")

    assert copied.status_code == 201
    original_copy = copied.json()
    assert original_copy["is_system"] is False
    assert original_copy["source_template_id"] == "builtin-delivery"
    assert original_copy["version"] == 1
    assert original_copy["deterministic_rules"] == client.get(
        "/api/v1/templates/builtin-delivery"
    ).json()["deterministic_rules"]

    body = _editable_body(original_copy)
    body["name"] = "门店送货单"
    body["fields"].append(
        {
            "label": "经手人",
            "section": "header",
            "example": "张三",
            "instructions": "",
            "value_type": "text",
        }
    )
    updated = client.put(
        f"/api/v1/templates/{original_copy['id']}",
        json={**body, "expected_version": 1},
    )

    assert updated.status_code == 200
    saved = updated.json()
    assert saved["version"] == 2
    assert saved["name"] == "门店送货单"
    assert saved["fields"][-1]["key"].startswith("field_")

    stale = client.put(
        f"/api/v1/templates/{original_copy['id']}",
        json={**body, "expected_version": 1},
    )
    assert stale.status_code == 409

    with client.app.state.session_factory() as session:
        versions = session.scalars(
            select(TemplateVersionRecord)
            .where(TemplateVersionRecord.template_id == original_copy["id"])
            .order_by(TemplateVersionRecord.version)
        ).all()
    assert [version.version for version in versions] == [1, 2]
    assert versions[0].name == "送货单 副本"
    assert versions[1].name == "门店送货单"


def test_create_rejects_duplicate_field_keys(client: TestClient) -> None:
    response = client.post(
        "/api/v1/templates",
        json={
            "name": "重复字段",
            "description": "",
            "extra_instructions": "",
            "fields": [
                {"key": "same", "label": "字段一"},
                {"key": "same", "label": "字段二"},
            ],
            "validation_rules": [],
            "output_mapping": {},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "字段“字段二”重复。"


def test_deterministic_rules_are_versioned_and_cannot_reference_unknown_fields(
    client: TestClient,
) -> None:
    body = {
        "name": "数量金额检查",
        "description": "",
        "extra_instructions": "",
        "fields": [
            {"key": "qty", "label": "数量", "section": "item", "value_type": "number"},
            {"key": "price", "label": "单价", "section": "item", "value_type": "number"},
            {"key": "amount", "label": "金额", "section": "item", "value_type": "number"},
        ],
        "validation_rules": [],
        "deterministic_rules": [
            {
                "kind": "equation",
                "field": "items[].amount",
                "left": {
                    "op": "multiply",
                    "left": {"op": "field", "path": "items[].qty"},
                    "right": {"op": "field", "path": "items[].price"},
                },
                "right": {"op": "field", "path": "items[].amount"},
            }
        ],
        "output_mapping": {},
    }

    created = client.post("/api/v1/templates", json=body)
    assert created.status_code == 201
    assert created.json()["deterministic_rules"][0]["kind"] == "equation"
    copied = client.post(
        f"/api/v1/templates/{created.json()['id']}/copy"
    )
    assert copied.status_code == 201
    assert copied.json()["deterministic_rules"] == created.json()[
        "deterministic_rules"
    ]

    invalid = deepcopy(body)
    invalid["name"] = "非法引用"
    invalid["deterministic_rules"][0]["right"]["path"] = "items[].missing"
    rejected = client.post("/api/v1/templates", json=invalid)
    assert rejected.status_code == 422
    assert "不存在的字段" in rejected.json()["detail"]


def test_user_template_archive_is_recoverable_and_preserves_versions(
    client: TestClient,
) -> None:
    copied = client.post("/api/v1/templates/builtin-invoice/copy").json()

    archived = client.post(f"/api/v1/templates/{copied['id']}/archive")

    assert archived.status_code == 200
    assert archived.json()["is_active"] is False
    assert copied["id"] not in {
        template["id"] for template in client.get("/api/v1/templates").json()
    }
    all_templates = client.get(
        "/api/v1/templates?include_inactive=true"
    ).json()
    archived_read = next(item for item in all_templates if item["id"] == copied["id"])
    assert archived_read["version"] == copied["version"]
    assert archived_read["deterministic_rules"] == copied["deterministic_rules"]

    rejected_update = client.put(
        f"/api/v1/templates/{copied['id']}",
        json={**_editable_body(copied), "expected_version": copied["version"]},
    )
    assert rejected_update.status_code == 409
    assert "已停用" in rejected_update.json()["detail"]
    assert client.post(
        f"/api/v1/templates/{copied['id']}/copy"
    ).status_code == 409

    restored = client.post(f"/api/v1/templates/{copied['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    assert copied["id"] in {
        template["id"] for template in client.get("/api/v1/templates").json()
    }


def test_builtin_template_cannot_be_archived(client: TestClient) -> None:
    response = client.post("/api/v1/templates/builtin-invoice/archive")
    assert response.status_code == 409
    assert response.json()["detail"] == "内置模板不能停用。"


def _editable_body(template: dict) -> dict:
    return {
        key: template[key]
        for key in (
            "name",
            "description",
            "extra_instructions",
            "fields",
            "validation_rules",
            "deterministic_rules",
            "output_mapping",
        )
    }


def _user_template_body(name: str) -> dict:
    return {
        "name": name,
        "description": "",
        "extra_instructions": "",
        "fields": [
            {
                "key": "seller_name",
                "label": "销售方",
                "section": "header",
                "example": "",
                "instructions": "",
                "value_type": "text",
            }
        ],
        "validation_rules": [],
        "deterministic_rules": [],
        "output_mapping": {},
    }


def test_create_template_rejects_duplicate_active_name(client: TestClient) -> None:
    # 与内置模板同名（内置同样受保护）
    rejected = client.post(
        "/api/v1/templates",
        json=_user_template_body("发票"),
    )
    assert rejected.status_code == 422
    assert "已存在同名模板" in rejected.json()["detail"]

    created = client.post(
        "/api/v1/templates",
        json=_user_template_body("门店模板"),
    )
    assert created.status_code == 201

    duplicate = client.post(
        "/api/v1/templates",
        json=_user_template_body("门店模板"),
    )
    assert duplicate.status_code == 422
    assert "已存在同名模板" in duplicate.json()["detail"]


def test_rename_to_existing_name_is_rejected(client: TestClient) -> None:
    copied = client.post("/api/v1/templates/builtin-invoice/copy").json()
    body = _editable_body(copied)
    body["name"] = "发票"  # 与内置发票同名
    renamed = client.put(
        f"/api/v1/templates/{copied['id']}",
        json={**body, "expected_version": copied["version"]},
    )
    assert renamed.status_code == 422
    assert "已存在同名模板" in renamed.json()["detail"]

    # 改回自己的名字（排除自身后不冲突）
    body["name"] = copied["name"]
    ok = client.put(
        f"/api/v1/templates/{copied['id']}",
        json={**body, "expected_version": copied["version"]},
    )
    assert ok.status_code == 200


def test_copy_template_increments_suffix_on_collision(client: TestClient) -> None:
    first = client.post("/api/v1/templates/builtin-invoice/copy")
    assert first.status_code == 201
    assert first.json()["name"] == "发票 副本"

    second = client.post("/api/v1/templates/builtin-invoice/copy")
    assert second.status_code == 201
    assert second.json()["name"] == "发票 副本2"


def test_archived_template_name_can_be_reused(client: TestClient) -> None:
    created = client.post(
        "/api/v1/templates",
        json=_user_template_body("临时模板"),
    )
    assert created.status_code == 201
    archived = client.post(f"/api/v1/templates/{created.json()['id']}/archive")
    assert archived.status_code == 200

    reuse = client.post(
        "/api/v1/templates",
        json=_user_template_body("临时模板"),
    )
    assert reuse.status_code == 201


def test_delete_unreferenced_user_template(client: TestClient) -> None:
    copied = client.post("/api/v1/templates/builtin-invoice/copy").json()

    deleted = client.delete(f"/api/v1/templates/{copied['id']}")
    assert deleted.status_code == 204
    assert copied["id"] not in {
        template["id"]
        for template in client.get("/api/v1/templates?include_inactive=true").json()
    }
    with client.app.state.session_factory() as session:
        versions = session.scalars(
            select(TemplateVersionRecord).where(
                TemplateVersionRecord.template_id == copied["id"],
            )
        ).all()
        assert versions == []


def test_delete_template_detaches_copied_descendants(client: TestClient) -> None:
    created = client.post(
        "/api/v1/templates",
        json=_user_template_body("溯源源模板"),
    ).json()
    copied = client.post(f"/api/v1/templates/{created['id']}/copy").json()
    assert copied["source_template_id"] == created["id"]

    deleted = client.delete(f"/api/v1/templates/{created['id']}")
    assert deleted.status_code == 204
    detached = client.get(f"/api/v1/templates/{copied['id']}").json()
    assert detached["source_template_id"] is None


def test_delete_referenced_template_is_rejected(client: TestClient) -> None:
    from document_pipeline_api.models import TaskRecord

    created = client.post(
        "/api/v1/templates",
        json=_user_template_body("被引用模板"),
    ).json()
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="referencing-task",
                filename="a.png",
                content_type="image/png",
                size_bytes=1,
                sha256="digest",
                storage_path="a.png",
                template_mode="custom",
                template_id=created["id"],
                status="needs_review",
            )
        )
        session.commit()

    deleted = client.delete(f"/api/v1/templates/{created['id']}")
    assert deleted.status_code == 409
    assert "已被任务或数据表引用" in deleted.json()["detail"]
    # 停用仍然可用
    archived = client.post(f"/api/v1/templates/{created['id']}/archive")
    assert archived.status_code == 200


def test_delete_builtin_template_is_rejected(client: TestClient) -> None:
    deleted = client.delete("/api/v1/templates/builtin-invoice")
    assert deleted.status_code == 409
    assert "内置模板不能删除" in deleted.json()["detail"]
