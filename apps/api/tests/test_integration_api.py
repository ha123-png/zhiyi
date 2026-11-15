import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import ExtractionRecord, TaskRecord
from document_pipeline_api.schemas.extraction import DocumentExtraction
from document_pipeline_api.services.integration_config import integration_config_path, write_integration_config
from image_test_data import PNG_BYTES


READ_TOKEN = "read-" + ("a" * 40)
WRITE_TOKEN = "write-" + ("b" * 40)


def _settings(tmp_path: Path, *, enabled: bool = True) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'integration.db'}",
        storage_dir=tmp_path / "uploads",
        integration_read_token=READ_TOKEN if enabled else "",
        integration_write_token=WRITE_TOKEN if enabled else "",
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_confirmed_task(client: TestClient) -> str:
    result = DocumentExtraction(
        document_type="发票",
        seller_name="甲公司",
        buyer_name="乙公司",
        document_number="API-1",
        document_date="2026-07-30",
        amount_before_tax=100,
        tax_amount=6,
        total_amount=106,
        items=[
                {
                    "name": "服务",
                    "specification": None,
                    "unit": "项",
                    "quantity": 1,
                    "unit_price": 100,
                    "amount": 100,
                    "tax_rate": "6%",
                    "tax_amount": 6,
                }
        ],
    )
    with client.app.state.session_factory() as session:
        session.add(
            TaskRecord(
                id="integration-task",
                filename="integration.png",
                content_type="image/png",
                size_bytes=1,
                sha256="integration-digest",
                storage_path="integration.png",
                template_mode="invoice",
                status="needs_review",
            )
        )
        session.flush()
        session.add(
            ExtractionRecord(
                task_id="integration-task",
                document_kind="invoice",
                model_name="test",
                prompt_version="test",
                elapsed_seconds=1,
                result_json=result.model_dump_json(),
                validation_json="[]",
            )
        )
        from document_pipeline_api.services.data_tables import materialize_task_result
        session.flush()
        materialize_task_result(session, "integration-task")
        session.commit()
    response = client.post(
        "/api/v1/tasks/integration-task/confirm",
        json={"expected_review_version": 0},
    )
    assert response.status_code == 200, response.text
    return response.json()["table_id"]


def test_integration_api_is_closed_when_tokens_are_not_configured(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path, enabled=False))) as client:
        response = client.get("/api/integration/v1/capabilities")

    assert response.status_code == 503


def test_integration_token_changes_take_effect_without_api_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=False)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/integration/v1/capabilities").status_code == 503
        write_integration_config(tmp_path, {"read_token": READ_TOKEN})
        enabled = client.get(
            "/api/integration/v1/capabilities",
            headers=_headers(READ_TOKEN),
        )
        assert enabled.status_code == 200
        write_integration_config(tmp_path, {"read_token": None})
        assert client.get(
            "/api/integration/v1/capabilities",
            headers=_headers(READ_TOKEN),
        ).status_code == 503


def test_weak_or_equal_integration_tokens_fail_at_startup(tmp_path: Path) -> None:
    weak = _settings(tmp_path)
    weak = Settings(
        **{
            **weak.__dict__,
            "integration_read_token": "too-short",
        }
    )
    equal = _settings(tmp_path)
    equal = Settings(
        **{
            **equal.__dict__,
            "integration_read_token": WRITE_TOKEN,
        }
    )

    with pytest.raises(RuntimeError, match="至少需要 32"):
        with TestClient(create_app(weak)):
            pass
    with pytest.raises(RuntimeError, match="不能相同"):
        with TestClient(create_app(equal)):
            pass


def test_invalid_or_missing_token_cannot_read_integration_data(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        missing = client.get("/api/integration/v1/tasks")
        invalid = client.get(
            "/api/integration/v1/tasks",
            headers=_headers("not-a-real-token"),
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert invalid.headers["www-authenticate"] == "Bearer"


def test_read_token_cannot_use_write_endpoints(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        capabilities = client.get(
            "/api/integration/v1/capabilities",
            headers=_headers(READ_TOKEN),
        )
        upload = client.post(
            "/api/integration/v1/tasks",
            headers=_headers(READ_TOKEN),
            files={"file": ("invoice.png", PNG_BYTES, "image/png")},
            data={"template_mode": "invoice"},
        )

    assert capabilities.status_code == 200
    assert capabilities.json()["scope"] == "read"
    assert capabilities.json()["write"] == []
    assert upload.status_code == 403


def test_generated_http_keys_can_read_originals_and_revisions_without_file_permission_settings(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, enabled=False))) as client:
        base = "/api/integration/v1"
        keys = "/api/v1/integration/settings/keys"
        read = _headers(client.post(keys, json={"kind": "read"}).json()["token"])
        write = _headers(client.post(keys, json={"kind": "write"}).json()["token"])
        table_id = _seed_confirmed_task(client)
        original = client.post(
            f"{base}/tasks", headers=write,
            files={"file": ("standalone.png", PNG_BYTES, "image/png")},
        ).json()
        file_url = f"{base}/tasks/{original['id']}/file"
        directory_url = f"{base}/tasks/integration-task/revisions"
        assert client.get(file_url).status_code == 401
        for headers in (read, write):
            capabilities = client.get(f"{base}/capabilities", headers=headers).json()
            assert {"file_revisions", "original_file"} <= set(capabilities["read"])
            download = client.get(file_url, headers=headers)
            assert download.status_code == 200 and download.content == PNG_BYTES
            assert download.headers["cache-control"] == "no-store"
            directory = client.get(directory_url, headers=headers)
            assert directory.status_code == 200
            revision = directory.json()["revisions"][0]["id"]
            assert client.get(f"{directory_url}/{revision}", headers=headers).status_code == 200
            assert client.get(f"{base}/tables/{table_id}/revisions", headers=headers).status_code == 200
        assert client.post(f"{base}/tasks", headers=read,
                           files={"file": ("second.png", PNG_BYTES, "image/png")}).status_code == 403


def test_existing_http_key_keeps_old_file_grants_until_reissued(tmp_path: Path) -> None:
    path = integration_config_path(tmp_path)
    path.parent.mkdir()
    legacy = {"read_token": READ_TOKEN, "write_token": WRITE_TOKEN,
              "api_read_revision_read": True, "api_read_table_ids": ["legacy-table"]}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    with TestClient(create_app(_settings(tmp_path, enabled=False))) as client:
        settings = "/api/v1/integration/settings"
        base = "/api/integration/v1"
        assert client.get(settings).json()["legacy_http_keys"] == ["read", "write"]
        old = _headers(READ_TOKEN)
        assert {"file_revisions", "original_file"} & set(client.get(f"{base}/capabilities", headers=old).json()["read"]) == {"file_revisions"}
        assert client.get(f"{base}/tasks/other/file", headers=old).status_code == 403
        assert client.get(f"{base}/tables/other/revisions", headers=old).status_code == 404
        assert client.post(f"{settings}/permissions", json={"task_read": True}).status_code == 200
        assert json.loads(path.read_text(encoding="utf-8"))["api_read_table_ids"] == ["legacy-table"]
        replacement = _headers(client.post(f"{settings}/keys", json={"kind": "read"}).json()["token"])
        assert client.get(f"{base}/capabilities", headers=old).status_code == 401
        assert {"file_revisions", "original_file"} <= set(client.get(f"{base}/capabilities", headers=replacement).json()["read"])
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "api_read_table_ids" not in data
        assert data["write_token"] == WRITE_TOKEN and data["mcp_task_read"]
        assert client.get(settings).json()["legacy_http_keys"] == ["write"]


def test_write_token_edits_same_versioned_fact_and_records_source(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        table_id = _seed_confirmed_task(client)
        table = client.get(
            f"/api/integration/v1/tables/{table_id}",
            headers=_headers(WRITE_TOKEN),
        ).json()
        row = table["rows"][0]

        edited = client.patch(
            f"/api/integration/v1/tables/{table_id}/rows/{row['id']}",
            headers=_headers(WRITE_TOKEN),
            json={
                "expected_version": row["version"],
                "changes": {"name": "ERP 修正"},
                "editor": "forged-client-name",
            },
        )
        stale = client.patch(
            f"/api/integration/v1/tables/{table_id}/rows/{row['id']}",
            headers=_headers(WRITE_TOKEN),
            json={
                "expected_version": row["version"],
                "changes": {"name": "旧值覆盖"},
            },
        )
        revisions = client.get(
            f"/api/v1/tasks/{row['task_id']}/revisions"
        ).json()["revisions"]

    assert edited.status_code == 200
    assert edited.json()["values"]["name"] == "ERP 修正"
    assert stale.status_code == 409
    assert revisions[0]["operation"] == "edit"
    assert revisions[0]["editor"] == "integration-api"


def test_write_token_can_upload_into_normal_task_pipeline(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post(
            "/api/integration/v1/tasks",
            headers=_headers(WRITE_TOKEN),
            files={"file": ("invoice.png", PNG_BYTES, "image/png")},
            data={"template_mode": "invoice"},
        )
        tasks = client.get(
            "/api/integration/v1/tasks",
            headers=_headers(READ_TOKEN),
        ).json()

    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert len(tasks) == 1
    assert tasks[0]["id"] == response.json()["id"]
