"""Windows supervisor must seal after terminating its children, before relaunch."""
import json
import shutil

from sqlalchemy import select
from sqlalchemy.orm import Session

from document_pipeline_api.db import build_engine
from document_pipeline_api.models import DataEditSessionRecord, FileRevisionRecord
from document_pipeline_api.services.current_data import create_edit_session, revise_current, current_snapshot
from document_pipeline_api.supervisor import _seal_stopped_edit_sessions
from test_current_data import engine as current_engine

engine = current_engine


def test_stopped_supervisor_seals_files_once_without_requiring_api_restart(engine, tmp_path):
    with Session(engine) as session:
        editing = create_edit_session(session, owner="stopped-api")
        for task_id, count in (("A", 5), ("B", 3)):
            for index in range(count):
                revise_current(session, task_id, expected_version=index,
                    header_changes={"customer": f"修改{index}"}, edit_session_id=editing.id)
        session.commit()
    engine.dispose()
    shutil.copyfile(tmp_path / 'current.db', tmp_path / 'document-pipeline.db')
    _seal_stopped_edit_sessions(tmp_path)
    _seal_stopped_edit_sessions(tmp_path)
    checked = build_engine(f"sqlite:///{tmp_path / 'document-pipeline.db'}")
    try:
        with Session(checked) as session:
            assert all(value.sealed_at for value in session.scalars(select(DataEditSessionRecord)))
            for task_id, final in (("A", "修改4"), ("B", "修改2")):
                versions = session.scalars(select(FileRevisionRecord).where(FileRevisionRecord.task_id == task_id)
                    .order_by(FileRevisionRecord.version)).all()
                assert len(versions) == 2 and versions[-1].sealed_at is not None
                assert json.loads(versions[-1].changes_json)['header']['customer']['after'] == final
                assert current_snapshot(session, task_id)['header']['customer'] == final
    finally:
        checked.dispose()


def test_stopped_supervisor_does_not_create_a_missing_database(tmp_path):
    _seal_stopped_edit_sessions(tmp_path)
    assert not (tmp_path / 'document-pipeline.db').exists()
