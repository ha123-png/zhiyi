from datetime import timedelta

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.model_diagnostics import safe_diagnostic
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services.task_leases import acquire_task_lease, fail_leased_task


def test_diagnostic_retains_end_of_long_error_and_masks_credentials():
    value = safe_diagnostic("BEGIN api_key=synthetic-secret Bearer other-secret " + "x" * 12000 + " END port occupied")
    assert len(value) <= 8192
    assert value.startswith("BEGIN") and value.endswith("END port occupied")
    assert "synthetic-secret" not in value and "other-secret" not in value
    assert "中间部分已省略" in value
    assert "private-folder" not in safe_diagnostic('failure at C:\\Users\\private-folder\\key.txt')


def test_failure_diagnostic_survives_summary_limit_and_is_not_in_task_list(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'diagnostics.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as client:
        with client.app.state.session_factory() as session:
            session.add(TaskRecord(id="diagnostic-task", filename="synthetic.txt", content_type="text/plain",
                                   size_bytes=1, sha256="test", storage_path="synthetic.txt", status="queued", template_mode="smart"))
            session.commit()
            lease = acquire_task_lease(session, "diagnostic-task", lease_for=timedelta(minutes=1))
            assert fail_leased_task(session, "diagnostic-task", lease.token, code="model_error",
                                    message="服务拒绝请求：" + "context " * 120 + "原因在末尾 api_key=secret-for-test")
            saved = session.get(TaskRecord, "diagnostic-task")
            assert len(saved.failure_message) <= 512
            assert "展开诊断" in saved.failure_message
            assert "原因在末尾" in saved.failure_detail
        detail = client.get("/api/v1/tasks/diagnostic-task/diagnostics").json()
        assert "原因在末尾" in detail["detail"] and "secret-for-test" not in detail["detail"]
        assert "failure_detail" not in client.get("/api/v1/tasks").json()[0]
        assert client.get("/api/v1/tasks/missing/diagnostics").status_code == 404
