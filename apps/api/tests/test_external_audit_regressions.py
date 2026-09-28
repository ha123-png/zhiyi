"""Reproduce the external v0.4 audit against real service boundaries."""
from io import BytesIO
from contextlib import closing
import json
import os
from types import SimpleNamespace
import sqlite3
import subprocess
import sys
from zipfile import ZipFile

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, update

from document_pipeline_api.models import DataRowRecord, DataViewRecord
from document_pipeline_api.model_providers import OpenAICompatibleProvider, OllamaProvider
from document_pipeline_api.services.assistant_analysis import AnalysisRequest, analyze
from document_pipeline_api.services.assistant_tools import Context, scoped_analysis, ensure_row_scope
from document_pipeline_api.services.data_views import _rows_for_view
from document_pipeline_api.services.integration_queries import export_data_table_file
from document_pipeline_api.services.data_tables import parse_import_rows, ImportFileError
from document_pipeline_api.services.atomic_output import new_text_file
from document_pipeline_api.business_backup import recover_interrupted_restore, restore_journal_path
from document_pipeline_api.installation_layout import validate_installation_layout
from test_assistant import client as assistant_client

client = assistant_client


@pytest.mark.parametrize("selected", [False, 0, 0.0, "0", True, 1, 1.0, "1"])
def test_view_scope_uses_exact_warehouse_predicate(client, selected):
    with client.app.state.session_factory() as session:
        session.execute(delete(DataRowRecord))
        records = []
        for index, value in enumerate([False, 0, 0.0, "0", True, 1, 1.0, "1"]):
            row = DataRowRecord(table_id="ledger", item_index=index, row_json=json.dumps({"vendor": value}))
            session.add(row)
            records.append(row)
        view = DataViewRecord(id="view", table_id="ledger", name="selected", field_key="vendor", field_value_json=json.dumps(selected))
        session.add(view)
        session.commit()
        native_ids = {row.id for row in _rows_for_view(session, view)}
        assert len(native_ids) == 1
        context = Context(table_id="ledger", view_id="view")
        query = scoped_analysis(session, context, AnalysisRequest(table_id="ledger", records=True))
        assert "_scope_view_id" not in query.model_dump()
        assert {row["row_id"] for row in analyze(session, query)["rows"]} == native_ids
        for row in records:
            if row.id in native_ids:
                ensure_row_scope(session, context, "ledger", row.id)
            else:
                with pytest.raises(HTTPException) as error:
                    ensure_row_scope(session, context, "ledger", row.id)
                assert error.value.status_code == 403
        from document_pipeline_api.services.assistant_refresh import refreshed_result
        saved = {**analyze(session, query), "analysis_id": "saved"}
        refreshed = refreshed_result(session, SimpleNamespace(status="completed", result_json=json.dumps(saved), id="old"), context)
        assert {row["row_id"] for row in refreshed["rows"]} == native_ids
        from document_pipeline_api.services.assistant_operations import Plan, preview_plan, apply_plan
        plan = preview_plan(session, context, Plan(title="修改当前分组", operations=[{
            "kind": "update_rows", "table_id": "ledger", "all_in_scope": True, "changes": {"amount": 5}}]))
        assert {row["row_id"] for row in plan["items"][0]["affected"]} == native_ids
        apply_plan(session, context, plan, client.app.state.settings)
        session.commit()
        for row in records:
            session.refresh(row)
            assert (json.loads(row.row_json).get("amount") == 5) == (row.id in native_ids)


def test_removed_row_identity_is_not_reused(client):
    with client.app.state.session_factory() as session:
        old = DataRowRecord(table_id="ledger", item_index=9, row_json='{"amount":1}')
        session.add(old)
        session.commit()
        old_id, old_version = old.id, old.row_version
        session.delete(old)
        session.commit()
        new = DataRowRecord(table_id="ledger", item_index=10, row_json='{"amount":2}')
        session.add(new)
        session.commit()
        assert new.id > old_id
    stale = client.patch(f"/api/v1/tables/ledger/rows/{old_id}", json={"expected_version": old_version, "changes": {"amount": 999}})
    assert stale.status_code == 404


class Header(BaseModel):
    value: str


class BusinessResult(BaseModel):
    header: Header
    items: list[dict[str, str]]
    quantity: float


@pytest.mark.parametrize("provider_type", [OpenAICompatibleProvider, OllamaProvider])
@pytest.mark.parametrize("wrapped", [False, True])
def test_provider_preserves_business_value_key(provider_type, wrapped):
    content = json.dumps({"header": {"value": "业务值"}, "items": [{"value": "明细值"}],
                          "quantity": {"value": 7} if wrapped else 7})
    def respond(_):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "message": {"content": content}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        provider = provider_type("http://synthetic.test", "test", client=transport)
        result = provider.complete_text("读取", BusinessResult)
    assert result.header.value == "业务值"
    assert result.items == [{"value": "明细值"}]
    assert result.quantity == 7


def test_export_publish_cannot_overwrite_racing_writer(client, tmp_path, monkeypatch):
    import document_pipeline_api.services.integration_queries as service
    target = tmp_path / "result.json"
    real = service._iter_table_values
    def racing_values(session, table_id):
        target.write_text("belongs to another writer", encoding="utf-8")
        yield from real(session, table_id)
    monkeypatch.setattr(service, "_iter_table_values", racing_values)
    with client.app.state.session_factory() as session, pytest.raises(FileExistsError):
        session.execute(update(DataRowRecord).values(input_scope_json=None))
        export_data_table_file(session, "ledger", target, "json")
    assert target.read_text(encoding="utf-8") == "belongs to another writer"
    assert not list(tmp_path.glob(".zhiyi-export-*"))


def test_failed_export_never_publishes_partial_file(tmp_path):
    path = tmp_path / "result.csv"
    with pytest.raises(RuntimeError):
        with new_text_file(path) as stream:
            stream.write("incomplete")
            raise RuntimeError("interrupted writer")
    assert not path.exists()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("coordinate", ["A2:A1048576", "A2:ZZ1000", "A2:A300000"])
def test_xlsx_budget_rejects_small_xml_before_workbook_allocation(coordinate, monkeypatch):
    import document_pipeline_api.services.data_tables as service
    def forbidden(*args, **kwargs):
        pytest.fail("Workbook allocated before structural budget check")
    monkeypatch.setattr(service, "load_workbook", forbidden)
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><mergeCells><mergeCell ref="' + coordinate + '"/></mergeCells></worksheet>')
    with pytest.raises(ImportFileError):
        parse_import_rows(buffer.getvalue(), "small.xlsx")


def _database(path, marker):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))


@pytest.mark.parametrize("kill_after", [1, 2, 3, 4, 5])
def test_restore_hard_exit_recovers_complete_old_set(tmp_path, kill_after):
    root, stage = tmp_path / "data", tmp_path / "stage"
    root.mkdir()
    stage.mkdir()
    for folder, marker in ((root, "old"), (stage, "new")):
        _database(folder / "document-pipeline.db", marker)
        (folder / "uploads").mkdir()
        (folder / "uploads" / "evidence.txt").write_text(marker)
    (root / "queue.db").write_bytes(b"old queue")
    script = '''
import os, sys
from pathlib import Path
import document_pipeline_api.business_backup as backup
root, stage, stop = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
move = backup._move_path
count = 0
def interrupted(source, destination):
    global count
    move(source, destination)
    count += 1
    if count == stop: os._exit(73)
backup._move_path = interrupted
backup._install_staged_restore(root, root / 'document-pipeline.db', root / 'uploads', stage / 'document-pipeline.db', stage / 'uploads')
'''
    result = subprocess.run([sys.executable, "-c", script, str(root), str(stage), str(kill_after)], capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    assert restore_journal_path(root).exists()
    recover_interrupted_restore(root)
    recover_interrupted_restore(root)  # repeated startup must be idempotent
    assert not restore_journal_path(root).exists()
    with sqlite3.connect(root / "document-pipeline.db") as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("old",)
    assert (root / "uploads" / "evidence.txt").read_text() == "old"
    assert (root / "queue.db").read_bytes() == b"old queue"


@pytest.mark.parametrize("layout", ["equal", "parent", "child", "mixed"])
def test_installation_cannot_claim_business_or_unrelated_directory(tmp_path, layout):
    data = tmp_path / "data"
    install = {"equal": data, "parent": tmp_path, "child": data / "program", "mixed": tmp_path / "other"}[layout]
    if layout == "mixed":
        install.mkdir()
        (install / "user.txt").write_text("keep")
    with pytest.raises(ValueError):
        validate_installation_layout(install, data)


def test_clear_revokes_native_ticket_without_deleting_external_original(tmp_path):
    from test_clear_data import setup_client, clear
    from document_pipeline_api.services.native_files import issue_import_ticket, consume_import_ticket
    from document_pipeline_api.services.file_copies import CopyExportError
    root = tmp_path / "data"
    source = tmp_path / "original.txt"
    source.write_bytes(b"evidence")
    client, _ = setup_client(root)
    with client:
        ticket = issue_import_ticket(root, source)
        assert clear(client).status_code == 200
        assert not (root / "native-imports").exists()
        with pytest.raises(CopyExportError):
            with consume_import_ticket(root, ticket["token"]):
                pytest.fail("old ticket was accepted")
    assert source.read_bytes() == b"evidence"


def test_restored_backup_rejects_pre_restore_row_version(client, tmp_path):
    from document_pipeline_api.business_backup import create_business_backup, restore_business_backup
    from document_pipeline_api.config import Settings
    from document_pipeline_api.main import create_app
    from fastapi.testclient import TestClient
    with client.app.state.session_factory() as session:
        session.execute(update(DataRowRecord).values(input_scope_json=None))
        session.commit()
    archive = create_business_backup(client.app.state.settings, tmp_path / "before.dpbak")
    source = client.get('/api/v1/tables/ledger').json()['rows'][0]
    target = tmp_path / "target"
    target.mkdir()
    settings = Settings(database_url=f"sqlite:///{target / 'document-pipeline.db'}", storage_dir=target / "uploads", queue_enabled=False)
    restore_business_backup(settings, archive, target)
    with TestClient(create_app(settings)) as restored:
        response = restored.patch(f'/api/v1/tables/ledger/rows/{source["id"]}', json={
            "expected_version": source["version"], "changes": {"amount": 999}})
        assert response.status_code == 409, response.text
        current = restored.get('/api/v1/tables/ledger').json()['rows'][0]
        assert current['values']['amount'] == source['values']['amount']
        assert current['version'] != source['version']


def test_installation_rejects_reparse_point_without_following_it(tmp_path):
    install, outside = tmp_path / "program", tmp_path / "outside"
    install.mkdir()
    outside.mkdir()
    (install / "Zhiyi.exe").write_bytes(b"app")
    (install / "ZhiyiCLI.exe").write_bytes(b"cli")
    (outside / "user.txt").write_bytes(b"preserve")
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(install / "linked"))
    else:
        (install / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="链接"):
        validate_installation_layout(install, tmp_path / "data")
    assert (outside / "user.txt").read_bytes() == b"preserve"


def test_data_generation_revokes_existing_mcp_connection(tmp_path):
    import asyncio
    from document_pipeline_api.mcp_server import create_mcp_server
    from document_pipeline_api.services.dataset_identity import advance_dataset
    from test_mcp_server import _settings
    server = create_mcp_server(_settings(tmp_path), data_read_enabled=True)
    asyncio.run(server.call_tool("list_data_tables", {}))
    advance_dataset(tmp_path)
    with pytest.raises(Exception, match="重新连接 MCP"):
        asyncio.run(server.call_tool("list_data_tables", {}))
