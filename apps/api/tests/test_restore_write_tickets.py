"""A backup restore must invalidate drafts even when object IDs/versions repeat."""
from fastapi.testclient import TestClient

from document_pipeline_api.business_backup import create_business_backup, restore_business_backup
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.schemas.templates import TemplateBody
from document_pipeline_api.services import dataset_identity


def draft(template, description="updated"):
    return {**{key: template[key] for key in TemplateBody.model_fields}, "description": description,
            "expected_version": template["version"], "expected_updated_at": template["updated_at"]}


def test_repeated_restore_fences_existing_and_absent_bindings_and_template_drafts(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'document-pipeline.db'}",
                        storage_dir=tmp_path / "uploads", queue_enabled=False)
    external = tmp_path / "external"
    external.mkdir()
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/templates", json={"name": "Restorable rules", "fields": [
            {"key": "customer", "label": "Customer", "section": "header", "value_type": "text"}]})
        assert created.status_code == 201
        original = created.json()
        template_id = original["id"]
        first_version = client.get(f"/api/v1/templates/{template_id}/versions/1").json()
        updated = client.put(f"/api/v1/templates/{template_id}", json=draft(original, "Backup rules"))
        assert updated.status_code == 200
        saved = updated.json()
        missing_id = next(item["id"] for item in client.get("/api/v1/templates").json() if item["id"] != template_id)
        assert client.get(f"/api/v1/templates/{missing_id}/local-export").json()["revision"] == 0
        assert client.put(f"/api/v1/templates/{template_id}/local-export",
                          json={"expected_revision": 0, "enabled": False}).status_code == 200
    backup = create_business_backup(settings, tmp_path / "older.dpbak")
    binding_body = {"enabled": True, "mode": "copy", "parent_path": str(external)}
    with TestClient(create_app(settings)) as client:
        # The current installation has progressed far past the backup. The old
        # revision 2 draft used to collide with backup revision 1 plus one.
        for revision in range(1, 8):
            assert client.put(f"/api/v1/templates/{template_id}/local-export",
                              json={**binding_body, "expected_revision": revision}).status_code == 200
        assert client.put(f"/api/v1/templates/{template_id}", json=draft(saved, "Newer live rules")).status_code == 200
    # Two restores within the same apparent clock tick must still differ by a
    # microsecond. Millisecond timestamps or backup_revision + 1 cannot do this.
    monkeypatch.setattr(dataset_identity.time, "time_ns", lambda: 1_800_000_000_123_456_000)
    previous_restore = None
    previous_binding_revision = None
    for _ in range(2):
        restore_business_backup(settings, backup, tmp_path)
        with TestClient(create_app(settings)) as client:
            current = client.get(f"/api/v1/templates/{template_id}").json()
            binding = client.get(f"/api/v1/templates/{template_id}/local-export").json()
            assert current["version"] == 2
            assert binding["enabled"] is False
            assert 8 < binding["revision"] < 2**53
            assert client.put(f"/api/v1/templates/{template_id}", json=draft(saved, "Stale rules")).status_code == 409
            assert client.post(f"/api/v1/templates/{template_id}/versions/1/restore", json={
                "expected_version": saved["version"], "expected_updated_at": saved["updated_at"]}).status_code == 409
            missing_timestamp = draft(current)
            missing_timestamp.pop("expected_updated_at")
            assert client.put(f"/api/v1/templates/{template_id}", json=missing_timestamp).status_code == 409
            assert client.post(f"/api/v1/templates/{template_id}/versions/1/restore",
                               json={"expected_version": 2}).status_code == 409
            assert client.put(f"/api/v1/templates/{template_id}/local-export",
                              json={**binding_body, "expected_revision": 2}).status_code == 409
            assert client.put(f"/api/v1/templates/{missing_id}/local-export",
                              json={**binding_body, "expected_revision": 0}).status_code == 409
            if previous_restore is not None:
                assert current["updated_at"] != previous_restore["updated_at"]
                assert binding["revision"] > previous_binding_revision
                assert client.put(f"/api/v1/templates/{template_id}", json=draft(previous_restore)).status_code == 409
                assert client.put(f"/api/v1/templates/{template_id}/local-export",
                                  json={**binding_body, "expected_revision": previous_binding_revision}).status_code == 409
            previous_restore, previous_binding_revision = current, binding["revision"]
            # A refreshed modern UI can edit and switch historical rules, and
            # explicitly rebind export. Immutable rule snapshots stay intact.
            fresh = client.put(f"/api/v1/templates/{template_id}", json=draft(current, "Fresh rules"))
            assert fresh.status_code == 200, fresh.text
            fresh = fresh.json()
            restored = client.post(f"/api/v1/templates/{template_id}/versions/1/restore", json={
                "expected_version": fresh["version"], "expected_updated_at": fresh["updated_at"]})
            assert restored.status_code == 200, restored.text
            assert restored.json()["version"] == 1
            assert client.get(f"/api/v1/templates/{template_id}/versions/1").json()["description"] == first_version["description"]
            rebound = client.put(f"/api/v1/templates/{template_id}/local-export",
                                 json={**binding_body, "expected_revision": binding["revision"]})
            assert rebound.status_code == 200, rebound.text
            assert rebound.json()["enabled"] is True
