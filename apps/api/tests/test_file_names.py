from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.services.file_names import model_file_name


class TextModel:
    model_name = "name-test"
    calls = 0

    def complete_text(self, prompt, result_type):
        self.calls += 1
        assert '"file_name_advice": {"rename": false, "name": null}' in prompt
        return result_type.model_validate({"header": {"title": "光合作用实验", "date": "2026-09-08"}, "items": [], "file_name_advice": {"rename": True, "name": "光合作用实验.txt"}})


def test_name_confirmation_exports_copy_without_extra_model_call(tmp_path: Path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    external = tmp_path / "external"
    external.mkdir()
    with TestClient(create_app(settings)) as client:
        assert client.put("/api/v1/system/settings", json={"office_convert": True, "image_convert": True}).status_code == 200
        template = client.post("/api/v1/templates", json={"name": "实验记录", "fields": [
            {"key": "title", "label": "标题", "section": "header"},
            {"key": "date", "label": "日期", "section": "header"},
        ], "behavior": {"suggest_filename": True}}).json()
        assert client.put(f"/api/v1/templates/{template['id']}/local-export", json={"expected_revision": 0, "enabled": True, "parent_path": str(external)}).status_code == 200
        raw = "标题：光合作用实验，日期：2026-09-08".encode()
        uploaded = client.post("/api/v1/tasks", data={"template_id": template["id"]}, files={"file": ("1001656.txt", raw, "text/plain")})
        assert uploaded.status_code == 201, uploaded.text
        task_id = uploaded.json()["id"]
        model = TextModel()
        with client.app.state.session_factory() as session:
            result = process_task(session, settings, task_id, client=model)
            task = session.get(TaskRecord, task_id)
            assert task.status == "needs_review"
            assert task.file_name.status == "pending"
            suggested = task.file_name.suggested_filename
            assert "光合作用实验" in suggested
            assert result.file_name.source_fields == []
            assert "file_name_advice" not in result.result.model_dump()
        assert list(external.iterdir()) == []
        response = client.put(f"/api/v1/tasks/{task_id}/review", json={"expected_version": 0, "result": result.result.model_dump(), "filename": suggested})
        assert response.status_code == 200, response.text
        assert response.json()["file_name"]["confirmed_filename"] == suggested
        assert (external / "实验记录" / suggested).read_bytes() == raw
        assert client.get(f"/api/v1/tasks/{task_id}/file").content == raw
        assert model.calls == 1
        task_read = client.get("/api/v1/tasks").json()[0]
        assert task_read["filename"] == "1001656.txt"
        assert task_read["file_export"]["status"] == "completed"
        assert client.get("/api/v1/tasks", params={"search": "光合作用"}).json()[0]["id"] == task_id
        assert client.get("/api/v1/tasks", params={"search": "1001656"}).json()[0]["id"] == task_id
        # Changing a confirmed name is metadata only; the already exported file stays put.
        changed = client.put(f"/api/v1/tasks/{task_id}/review", json={"expected_version": 1, "result": result.result.model_dump(), "filename": "新的展示名称.txt"})
        assert changed.status_code == 200
        assert len(changed.json()["file_name"]["decisions"]) == 2
        assert (external / "实验记录" / suggested).exists()
        assert not (external / "实验记录" / "新的展示名称.txt").exists()


@pytest.mark.parametrize("original", ["年度财务报告.pdf", "数学错题第一章.docx"])
def test_meaningful_names_are_not_replaced_by_a_first_item_title(original):
    result = model_file_name(original, {"rename": False, "name": None})
    assert result.suggested_filename == original
    assert result.source_fields == []
