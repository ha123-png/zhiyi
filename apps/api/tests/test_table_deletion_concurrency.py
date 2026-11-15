"""Table deletion serializes its complete current-row inventory with writers."""
import hashlib
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import (
    DataRowRecord, DataTableRecord, FileCurrentDataRecord, FileRevisionRecord, TaskRecord,
)
from document_pipeline_api.services.current_data import initialize_current
from document_pipeline_api.services.data_tables import delete_table


SCHEMA = [
    {"key": "customer", "label": "Customer", "section": "header", "value_type": "text"},
    {"key": "item", "label": "Item", "section": "item", "value_type": "text"},
]


def add_file(session, identifier):
    session.add(TaskRecord(id=identifier, filename=f"{identifier}.txt", content_type="text/plain",
                           size_bytes=9, sha256=hashlib.sha256(b"synthetic").hexdigest(),
                           storage_path=f"{identifier}.txt", status="completed"))
    session.flush()
    initialize_current(session, task_id=identifier, table_id="table",
                       header={"customer": identifier}, items=[{"item": "synthetic"}], schema=SCHEMA)


@pytest.mark.parametrize("initial_file", [False, True])
def test_table_deletion_serializes_before_enumerating_rows(tmp_path, initial_file):
    engine = build_engine(f"sqlite:///{tmp_path / 'table-delete.db'}")
    Base.metadata.create_all(engine)
    originals = []
    with Session(engine) as session:
        session.add(DataTableRecord(id="table", name="Fixture", template_key="fixture",
                                   template_version="1", document_kind="custom",
                                   columns_json=json.dumps(SCHEMA)))
        session.flush()
        if initial_file:
            add_file(session, "first")
            original = tmp_path / "first.txt"
            original.write_bytes(b"synthetic")
            originals.append(original)
        session.commit()
    observed = {}

    def concurrent_add(_connection, _cursor, statement, parameters, _context, _many):
        if observed or not statement.startswith("SELECT data_rows.id ") or parameters != ("table",):
            return
        observed["writer"] = "attempting"
        with Session(engine) as writer:
            writer.connection().exec_driver_sql("PRAGMA busy_timeout=0")
            try:
                add_file(writer, "concurrent")
                writer.commit()
                observed["writer"] = "committed"
            except OperationalError as error:
                assert getattr(error.orig, "sqlite_errorcode", 0) & 0xFF in {5, 6}
                observed["writer"] = "serialized"

    event.listen(engine, "after_cursor_execute", concurrent_add)
    try:
        with Session(engine) as deleting:
            delete_table(deleting, "table")
        assert observed["writer"] == "serialized", observed
        with Session(engine) as check:
            assert check.get(DataTableRecord, "table") is None
            assert check.scalars(select(DataRowRecord)).all() == []
            assert check.get(TaskRecord, "concurrent") is None
            if initial_file:
                current = check.get(FileCurrentDataRecord, "first")
                assert current.table_id is None
                assert current.has_data is False
                assert json.loads(current.header_json) == {}
                revisions = check.scalars(select(FileRevisionRecord).where(
                    FileRevisionRecord.task_id == "first").order_by(FileRevisionRecord.version)).all()
                assert [revision.operation for revision in revisions] == ["extraction", "delete"]
        assert all(original.read_bytes() == b"synthetic" for original in originals)
    finally:
        print(json.dumps({"initial_file": initial_file, **observed}, sort_keys=True))
        event.remove(engine, "after_cursor_execute", concurrent_add)
        engine.dispose()


def test_missing_table_deletion_releases_its_write_snapshot(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'missing-table.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as deleting:
            with pytest.raises(HTTPException) as missing:
                delete_table(deleting, "missing")
            assert missing.value.status_code == 404
            with Session(engine) as writer:
                writer.connection().exec_driver_sql("PRAGMA busy_timeout=0")
                writer.add(DataTableRecord(id="other", name="Other request", template_key="other",
                                           template_version="1", document_kind="custom"))
                writer.commit()
    finally:
        engine.dispose()
