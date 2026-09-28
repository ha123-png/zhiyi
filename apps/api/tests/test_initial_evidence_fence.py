"""An existing extraction cannot be rerun through routing or stale worker jobs."""
import hashlib
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import (
    DataTableRecord, ExtractionRecord, FileCurrentDataRecord, FileRevisionRecord, TaskRecord,
)
from document_pipeline_api.services.current_data import current_snapshot
from document_pipeline_api.services.extraction import process_task
from image_test_data import PNG_BYTES
from template_model_test_data import invoice_response


class OfflineInvoice:
    model_name = "offline-evidence-test"

    def __init__(self):
        self.calls = 0

    def extract_image(self, _path, _prompt, result_type):
        self.calls += 1
        return result_type.model_validate(invoice_response(document_number="INITIAL-A"))


def fingerprint(session, task_id):
    extraction = session.get(ExtractionRecord, task_id)
    initial = {column.name: str(getattr(extraction, column.name)) for column in ExtractionRecord.__table__.columns}
    history = [{column.name: str(getattr(record, column.name)) for column in FileRevisionRecord.__table__.columns}
               for record in session.scalars(select(FileRevisionRecord).where(FileRevisionRecord.task_id == task_id)
                                              .order_by(FileRevisionRecord.version))]
    return hashlib.sha256(json.dumps({"initial": initial, "current": current_snapshot(session, task_id),
                                     "history": history}, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def routed_file(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'document-pipeline.db'}",
                        storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as client:
        with client.app.state.session_factory() as session:
            session.add(DataTableRecord(id="wrong-destination", name="Wrong destination", template_key="delivery",
                                        template_version="builtin-v1", document_kind="delivery"))
            session.commit()
        response = client.post("/api/v1/tasks", data={"template_mode": "invoice"},
                               files={"file": ("evidence.png", PNG_BYTES, "image/png")})
        assert response.status_code == 201
        task_id = response.json()["id"]
        with client.app.state.session_factory() as session:
            task = session.get(TaskRecord, task_id)
            task.target_table_id = "wrong-destination"
            session.commit()
            process_task(session, settings, task_id, client=OfflineInvoice())
            session.rollback()
            task = session.get(TaskRecord, task_id)
            assert task.status == "waiting_for_template"
            initial = fingerprint(session, task_id)
            template_id = task.template_id
            table_id = session.get(FileCurrentDataRecord, task_id).table_id
        yield client, settings, task_id, template_id, table_id, initial


@pytest.mark.parametrize("different_template", [False, True])
def test_template_selection_cannot_requeue_existing_initial_evidence(routed_file, different_template):
    client, _, task_id, template_id, table_id, initial = routed_file
    if different_template:
        template_id = next(template["id"] for template in client.get("/api/v1/templates").json()
                           if template["id"] != template_id)
    response = client.post(f"/api/v1/tasks/{task_id}/template", json={"template_id": template_id})
    assert response.status_code == 409, response.text
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, task_id).status == "waiting_for_template"
        assert fingerprint(session, task_id) == initial
    # The legitimate routing recovery still confirms the already saved facts.
    confirmed = client.post(f"/api/v1/tasks/{task_id}/template", json={"target_table_id": table_id})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"
    with client.app.state.session_factory() as session:
        assert fingerprint(session, task_id) == initial


def test_worker_rejects_legacy_requeued_result_without_model_call_or_evidence_change(routed_file):
    client, settings, task_id, _, _, initial = routed_file
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        task.status = "queued"  # Simulate an old queue state created before the fence.
        session.commit()
        model = OfflineInvoice()
        assert process_task(session, settings, task_id, client=model) is None
        assert model.calls == 0
        task = session.get(TaskRecord, task_id)
        assert task.status == "failed"
        assert task.failure_code == "existing_extraction"
        assert task.lease_token is None
        assert fingerprint(session, task_id) == initial
