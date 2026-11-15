import json
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.services.data_tables import create_table_from_import, parse_import_rows
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.services.export_scope import REVIEW_KEY, REVIEW_LABEL


class MissingOwner:
    model_name = "offline-review-fixture"

    def complete_text(self, prompt, result_type):
        return result_type.model_validate({"header": {"owner": None, "document": "项目说明"}, "items": []})


@pytest.mark.parametrize("suffix", ["xlsx", "csv", "json"])
@pytest.mark.parametrize("resolve_review", [True, False])
def test_pending_review_survives_export_import_merge_history_and_clears_on_review(tmp_path, suffix, resolve_review):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_dir=tmp_path / "uploads", queue_enabled=False)
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/templates", json={"name": "需要负责人", "fields": [
            {"key": "owner", "label": "负责人", "section": "header"},
            {"key": "document", "label": "文件名称", "section": "header"},
        ], "deterministic_rules": [{"kind": "required", "field": "header.owner", "severity": "error"}]})
        assert created.status_code == 201, created.text
        template = created.json()
        original = "负责人未填写。".encode()
        task = client.post("/api/v1/tasks", files={"file": ("待核对.txt", original, "text/plain")}, data={"template_id": template["id"]}).json()
        with client.app.state.session_factory() as session:
            result = process_task(session, settings, task["id"], client=MissingOwner())
        assert result is not None and result.validation_issues
        table = client.get("/api/v1/tables").json()[0]
        table_id = table["id"]
        row = client.get(f"/api/v1/tables/{table_id}").json()["rows"][0]
        assert row["review_pending"] is True
        exported = client.get(f"/api/v1/tables/{table_id}/export.{suffix}")
        assert exported.status_code == 200
        if suffix == "json":
            imported_rows = exported.json()
            assert imported_rows[0][REVIEW_KEY] is True
        elif suffix == "csv":
            imported_rows = parse_import_rows(exported.content, f"rows.{suffix}")
            assert imported_rows[0][REVIEW_KEY] == "待核对"
        else:
            workbook = load_workbook(BytesIO(exported.content))
            try:
                assert len(workbook.sheetnames) == 1
                assert list(workbook.active.values) == [("负责人", "文件名称"), (None, "项目说明")]
                assert all(cell.comment is None for cells in workbook.active for cell in cells)
            finally:
                workbook.close()
            imported_rows = parse_import_rows(exported.content, "rows.xlsx")
            assert REVIEW_KEY not in imported_rows[0]
        imported_pending = suffix != "xlsx"
        with client.app.state.session_factory() as session:
            imported = create_table_from_import(session, "重新导入", imported_rows)
            imported_id = imported.id
        imported_detail = client.get(f"/api/v1/tables/{imported_id}").json()
        assert bool(imported_detail["rows"][0]["review_pending"]) is imported_pending
        assert REVIEW_LABEL not in [col["label"] for col in imported_detail["columns"]]
        merged = client.post("/api/v1/tables/merge", json={"name": "合并", "table_ids": [table_id, imported_id]})
        if suffix != "json":
            # A flat spreadsheet cannot recover a single-row header's section.
            # Same-label header/item contracts must not be silently collapsed.
            assert merged.status_code == 409, merged.text
            with client.app.state.session_factory() as session:
                second = create_table_from_import(session, "再次独立导入", imported_rows)
                second_id = second.id
            merged = client.post("/api/v1/tables/merge", json={"name": "合并", "table_ids": [imported_id, second_id]})
        assert merged.status_code == 201, merged.text
        assert all(bool(row["review_pending"]) is imported_pending for row in client.get(f"/api/v1/tables/{merged.json()['id']}").json()["rows"])
        if resolve_review:
            updated = client.put(f"/api/v1/tasks/{task['id']}/review", json={"expected_version": 0, "result": {"header": {"owner": "合成负责人", "document": "项目说明"}, "items": []}})
            assert updated.status_code == 200, updated.text
            assert client.get(f"/api/v1/tables/{table_id}").json()["rows"][0]["review_pending"] is False
            # An independent imported copy must keep the status it actually carried.
            assert bool(client.get(f"/api/v1/tables/{imported_id}").json()["rows"][0]["review_pending"]) is imported_pending
            assert client.get(f"/api/v1/tasks/{task['id']}/file").content == original
            cleared = client.get(f"/api/v1/tables/{table_id}/export.json").json()
            assert REVIEW_KEY not in cleared[0]
        assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 200
        assert client.get(f"/api/v1/tables/{table_id}").json()["rows"] == []
        assert all(bool(row["review_pending"]) is imported_pending for row in client.get(f"/api/v1/tables/{merged.json()['id']}").json()["rows"])
        assert bool(json.loads(client.get(f"/api/v1/tables/{imported_id}/export.json").content)[0].get(REVIEW_KEY)) is imported_pending
