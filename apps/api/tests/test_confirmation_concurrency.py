import json

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import (
    DataRowRecord, ExtractionRecord, FileCurrentDataRecord, FileRevisionRecord, TaskRecord,
)
from document_pipeline_api.schemas.extraction import DocumentExtraction, ReviewUpdate
from document_pipeline_api.services.data_tables import confirm_task, materialize_task_result
from document_pipeline_api.services.extraction import save_review


TASK_ID = "confirmation-race"


def _result():
    return DocumentExtraction(
        document_type="invoice", seller_name="Synthetic seller", buyer_name="Synthetic buyer",
        document_number="SYNTHETIC-1", document_date="2026-10-31",
        amount_before_tax=100, tax_amount=6, total_amount=106,
        items=[{"name": "Synthetic item", "specification": "Synthetic", "unit": "item",
                "quantity": 1, "unit_price": 100,
                "amount": 100, "tax_amount": 6, "tax_rate": "6%"}],
    )


@pytest.fixture
def engine(tmp_path):
    value = build_engine(f"sqlite:///{tmp_path / 'confirmation.db'}")
    Base.metadata.create_all(value)
    with Session(value) as session:
        session.add(TaskRecord(
            id=TASK_ID, filename="synthetic.png", content_type="image/png", size_bytes=1,
            sha256="synthetic", storage_path="synthetic.png", template_mode="invoice",
            status="needs_review",
        ))
        session.flush()
        session.add(ExtractionRecord(
            task_id=TASK_ID, document_kind="invoice", model_name="synthetic",
            prompt_version="synthetic", elapsed_seconds=0,
            result_json=_result().model_dump_json(), validation_json="[]",
        ))
        session.flush()
        materialize_task_result(session, TASK_ID)
        session.commit()
    yield value
    value.dispose()


def _edit_with_blocking_issue(engine, *, immediate_busy_error=False):
    with Session(engine) as writer:
        if immediate_busy_error:
            # The other session runs synchronously at the exact interleaving.
            # Report a held write lock immediately instead of waiting on us.
            writer.connection().exec_driver_sql("PRAGMA busy_timeout=0")
        save_review(writer, TASK_ID, ReviewUpdate(
            expected_version=0,
            result=_result().model_copy(update={"total_amount": 999}),
        ))


def _state(engine):
    with Session(engine) as check:
        current = check.get(FileCurrentDataRecord, TASK_ID)
        task = check.get(TaskRecord, TASK_ID)
        return {
            "version": current.current_version,
            "total_amount": json.loads(current.header_json)["total_amount"],
            "blocking_errors": sum(issue.get("severity") == "error" and not issue.get("ignored")
                                   for issue in json.loads(current.validation_json)),
            "task_status": task.status,
            "review_pending": check.scalars(select(DataRowRecord.review_pending).where(
                DataRowRecord.task_id == TASK_ID)).all(),
            "revisions": check.scalar(select(func.count()).select_from(FileRevisionRecord).where(
                FileRevisionRecord.task_id == TASK_ID)),
        }


def test_confirmation_never_approves_a_newer_edit_between_its_read_and_write(engine, monkeypatch):
    observed = {"writer": None, "confirm_error": None, "confirm_version": None}
    with Session(engine) as confirming:
        original_get = confirming.get

        def interleave(entity, identity, **kwargs):
            record = original_get(entity, identity, **kwargs)
            if entity is FileCurrentDataRecord and observed["writer"] is None:
                observed["old_current_version"] = record.current_version
                observed["writer"] = "attempting"
                try:
                    _edit_with_blocking_issue(engine, immediate_busy_error=True)
                    observed["writer"] = "committed"
                    observed["writer_state"] = _state(engine)
                except OperationalError as error:
                    assert getattr(error.orig, "sqlite_errorcode", 0) & 0xFF in {5, 6}
                    observed["writer"] = "serialized"
            return record

        monkeypatch.setattr(confirming, "get", interleave)
        try:
            observed["confirm_version"] = confirm_task(confirming, TASK_ID, 0).review_version
        except HTTPException as error:
            observed["confirm_error"] = error.status_code
    observed["after_confirm"] = _state(engine)
    print(json.dumps(observed, sort_keys=True))
    assert observed["old_current_version"] == 0
    if observed["writer"] == "committed":
        # A conditional version fence is also valid: it must reject the stale
        # confirmation without clearing the new review issue or pending state.
        assert observed["confirm_error"] == 409, observed
        assert observed["after_confirm"] == observed["writer_state"]
    else:
        assert observed["writer"] == "serialized"
        assert observed["confirm_error"] is None
        assert observed["confirm_version"] == 0
        assert observed["after_confirm"]["total_amount"] == 106
        assert observed["after_confirm"]["task_status"] == "completed"
        assert observed["after_confirm"]["review_pending"] == [False]
        # Once that confirmation commits, the later edit still executes and
        # keeps its new validation failure. An old retry must not approve it.
        _edit_with_blocking_issue(engine)

    after_edit = _state(engine)
    assert after_edit["version"] == 1
    assert after_edit["total_amount"] == 999
    assert after_edit["blocking_errors"] > 0
    # A revision of an already completed file records the issue in the new version.
    expected_pending = observed["writer"] == "committed"
    assert after_edit["task_status"] == ("needs_review" if expected_pending else "completed")
    assert after_edit["review_pending"] == [expected_pending]
    with Session(engine) as retry:
        with pytest.raises(HTTPException) as error:
            confirm_task(retry, TASK_ID, 0)
        assert error.value.status_code == 409
    assert _state(engine) == after_edit


def test_confirmation_checks_version_and_validation_after_a_committed_edit(engine):
    _edit_with_blocking_issue(engine)
    before = _state(engine)
    assert before["version"] == 1
    assert before["blocking_errors"] > 0
    with Session(engine) as stale:
        with pytest.raises(HTTPException) as error:
            confirm_task(stale, TASK_ID, 0)
        assert error.value.status_code == 409
    with Session(engine) as current:
        with pytest.raises(HTTPException) as error:
            confirm_task(current, TASK_ID, 1)
        assert error.value.status_code == 422
    assert _state(engine) == before


def test_confirmation_response_describes_the_version_it_committed(engine, monkeypatch):
    with Session(engine) as confirming:
        original_commit = confirming.commit

        def edit_after_commit():
            original_commit()
            _edit_with_blocking_issue(engine)

        monkeypatch.setattr(confirming, "commit", edit_after_commit)
        result = confirm_task(confirming, TASK_ID, 0)

    after = _state(engine)
    assert after["version"] == 1
    assert after["blocking_errors"] > 0
    assert after["task_status"] == "completed"
    assert after["review_pending"] == [False]
    # The later edit is real, but was not the version this request approved.
    assert result.review_version == 0
