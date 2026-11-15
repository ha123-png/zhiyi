"""A single MCP task response must come from one persisted state/version."""

import asyncio
import json

from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.mcp_server import create_mcp_server
from document_pipeline_api.models import ExtractionRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.schemas.extraction import DocumentExtraction, ReviewUpdate
from document_pipeline_api.services.data_tables import confirm_task, materialize_task_result
from document_pipeline_api.services.extraction import save_review
from document_pipeline_api.services.templates import ensure_builtin_templates


TASK_ID = "single-task-snapshot"


def _result(number):
    return DocumentExtraction(
        document_type="invoice", seller_name="Synthetic seller", buyer_name="Synthetic buyer",
        document_number=number, document_date="2026-11-02", amount_before_tax=100,
        tax_amount=6, total_amount=106,
        items=[{"name": "Synthetic item", "specification": "Synthetic", "unit": "item",
                "quantity": 1, "unit_price": 100, "amount": 100, "tax_amount": 6,
                "tax_rate": "6%"}],
    )


def _payload(raw):
    if isinstance(raw, tuple):
        raw = raw[1] or raw[0]
    if isinstance(raw, dict):
        return raw.get("result", raw)
    parsed = json.loads(raw[0].text)
    return parsed.get("result", parsed)


def test_mcp_get_task_does_not_mix_old_waiting_status_with_new_current_version(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'single-read.db'}",
                        storage_dir=tmp_path / "uploads", queue_enabled=False)
    server = create_mcp_server(settings, task_read_enabled=True)
    engine = build_engine(settings.database_url)
    with Session(engine) as session:
        ensure_builtin_templates(session)
        session.add(TaskRecord(
            id=TASK_ID, filename="synthetic.txt", content_type="text/plain", size_bytes=1,
            sha256="synthetic", storage_path="synthetic.txt", template_mode="invoice",
            status="waiting_for_template", pending_reason=None,
        ))
        session.flush()
        session.add(ExtractionRecord(
            task_id=TASK_ID, document_kind="invoice", model_name="synthetic-record",
            prompt_version="synthetic", elapsed_seconds=0,
            result_json=_result("ORIGINAL").model_dump_json(), validation_json="[]",
        ))
        session.flush()
        materialize_task_result(session, TASK_ID)
        session.commit()
        table_id = session.get(FileCurrentDataRecord, TASK_ID).table_id

    observed = {"interleaved": False, "persisted_states": [["waiting_for_template", 0]]}
    original_get = Session.get

    def interleave(reader, entity, identifier, **kwargs):
        record = original_get(reader, entity, identifier, **kwargs)
        if entity is TaskRecord and identifier == TASK_ID and not observed["interleaved"]:
            observed["interleaved"] = True
            observed["read_status_before_writer"] = record.status
            # Real product mutations on another connection: confirm the old
            # result, then edit the now-completed file to current version 1.
            with Session(engine) as writer:
                confirm_task(writer, TASK_ID, 0, target_table_id=table_id)
                observed["persisted_states"].append(["completed", 0])
                save_review(writer, TASK_ID, ReviewUpdate(expected_version=0, result=_result("EDITED")))
                current = writer.get(FileCurrentDataRecord, TASK_ID)
                observed["persisted_states"].append([writer.get(TaskRecord, TASK_ID).status, current.current_version])
        return record

    monkeypatch.setattr(Session, "get", interleave)
    try:
        response = _payload(asyncio.run(server.call_tool("get_task", {"task_id": TASK_ID})))
        observed["response"] = {key: response[key] for key in (
            "status", "pending_reason", "pending_action", "pending_review_version",
        )}
        with Session(engine) as session:
            assert session.get(TaskRecord, TASK_ID).status == "completed"
            assert session.get(FileCurrentDataRecord, TASK_ID).current_version == 1
            assert json.loads(session.get(ExtractionRecord, TASK_ID).result_json)["document_number"] == "ORIGINAL"
        print(json.dumps(observed, sort_keys=True))
        assert observed["interleaved"]
        assert response["status"] == "waiting_for_template"
        assert response["pending_action"] == "confirm_target_table"
        assert response["pending_review_version"] == 0
    finally:
        engine.dispose()
