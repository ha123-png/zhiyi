from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import ExtractionRecord, TaskRecord
from document_pipeline_api.schemas.extraction import DocumentExtraction
from document_pipeline_api.services.integration_config import write_integration_config
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
        session.commit()
    response = client.post(
        "/api/v1/tasks/integration-task/confirm",
        json={"expected_review_version": 0},
    )
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
            f"/api/v1/tables/{table_id}/rows/{row['id']}/revisions"
        ).json()

    assert edited.status_code == 200
    assert edited.json()["values"]["name"] == "ERP 修正"
    assert stale.status_code == 409
    assert revisions[-1]["operation"] == "table_edit"
    assert revisions[-1]["editor"] == "integration-api"


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
