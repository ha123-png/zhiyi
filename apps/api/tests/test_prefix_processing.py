from document_pipeline_api.schemas.input_scope import SourceRange
from document_pipeline_api.services.input_scope import InputUnit, select_prefix
from document_pipeline_api.services.file_names import model_file_name


def choose(units, **overrides):
    return select_prefix(units, **({"limited": True, "text_limit": 128, "image_limit": 2, "row_limit": 2} | overrides))


def test_oversized_first_line_stops_at_character_boundary():
    result = choose([InputUnit(SourceRange(kind="line", start=1, end=1), text="A" * 10000),
                     InputUnit(SourceRange(kind="line", start=2, end=2), text="TAIL")])
    assert "TAIL" not in result.text
    assert result.units[0].text == "A" * 128
    assert result.scope.selected[0].character_end == 128
    assert result.scope.omitted[0].location.character_start == 129


def test_unlimited_does_not_use_context_or_page_limit():
    result = choose([InputUnit(SourceRange(kind="page", start=i, end=i)) for i in range(1, 61)], limited=False)
    assert len(result.units) == 60
    assert result.scope.coverage == "complete"


def test_prefix_does_not_fill_from_tail():
    result = choose([InputUnit(SourceRange(kind="page", start=i, end=i)) for i in range(1, 61)])
    assert [u.location.start for u in result.units] == [1, 2]
    assert result.scope.omitted[0].location.end == 60


def test_workbook_limit_is_shared_across_sheets_and_ignores_empty_rows():
    units = [InputUnit(SourceRange(kind="sheet_row", container=sheet, start=i, end=i), text=text)
             for sheet, i, text in [("A", 1, "one"), ("A", 2, ""), ("B", 1, "two"), ("B", 2, "three")]]
    result = choose(units)
    assert [u.text for u in result.units] == ["one", "two"]
    assert result.scope.omitted[0].location.container == "B"


def test_docx_image_boundary_stops_later_text():
    result = choose([InputUnit(SourceRange(kind="paragraph", start=1, end=1), text="HEAD"),
                     InputUnit(SourceRange(kind="image", start=1, end=1)),
                     InputUnit(SourceRange(kind="image", start=2, end=2)),
                     InputUnit(SourceRange(kind="paragraph", start=2, end=2), text="TAIL")], image_limit=1)
    assert "TAIL" not in result.text


def test_bad_name_advice_never_fails_extraction_or_changes_extension():
    assert model_file_name("IMG_123.txt", {"rename": True, "name": "../escape.txt"}).suggested_filename == "IMG_123.txt"
    assert model_file_name("课程笔记.txt", {"rename": False}).suggested_filename == "课程笔记.txt"
    assert model_file_name("IMG_123.txt", {"rename": True, "name": "数学笔记"}).suggested_filename == "数学笔记.txt"


def test_user_upload_limit_replaces_old_request_cap_and_is_snapshotted(tmp_path):
    from fastapi.testclient import TestClient
    from document_pipeline_api.config import Settings
    from document_pipeline_api.main import create_app
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'limits.db'}", storage_dir=tmp_path / "uploads",
                        max_request_bytes=256, max_upload_bytes=1024)
    with TestClient(create_app(settings)) as client:
        update = client.put("/api/v1/system/settings", json={"image_convert": True, "office_convert": True,
            "upload_limit_mb": 1, "allow_limited_input": True, "input_text_limit": 128})
        assert update.status_code == 200
        raw = "A" * 2000
        task = client.post("/api/v1/tasks", files={"file": ("long.txt", raw, "text/plain")})
        assert task.status_code == 201
        assert task.json()["planned_scope"]["text_characters"] == 128
        assert client.get(f"/api/v1/tasks/{task.json()['id']}/file").text == raw
        rejected = client.post("/api/v1/tasks", files={"file": ("too-big.txt", b"A" * (1024 * 1024 + 1), "text/plain")})
        assert rejected.status_code == 413
        assert "上传上限" in rejected.json()["detail"]


def test_retry_changes_input_only_when_explicitly_requested(tmp_path):
    from fastapi.testclient import TestClient
    from document_pipeline_api.config import Settings
    from document_pipeline_api.main import create_app
    from document_pipeline_api.models import TaskRecord
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'retry.db'}", storage_dir=tmp_path / "uploads")
    with TestClient(create_app(settings)) as client:
        task = client.post("/api/v1/tasks", files={"file": ("note.txt", "ABC", "text/plain")}).json()
        assert client.put("/api/v1/system/settings", json={"allow_limited_input": True, "input_text_limit": 1}).status_code == 200
        def fail():
            with client.app.state.session_factory() as session:
                record = session.get(TaskRecord, task["id"])
                record.status = "failed"
                session.commit()
        fail()
        same = client.post(f"/api/v1/tasks/{task['id']}/retry")
        assert same.status_code == 200
        assert same.json()["planned_scope"]["coverage"] == "complete"
        fail()
        changed = client.post(f"/api/v1/tasks/{task['id']}/retry?use_current_settings=true")
        assert changed.status_code == 200
        assert changed.json()["planned_scope"]["text_characters"] == 1
        assert changed.json()["planned_scope"]["coverage"] == "partial"
