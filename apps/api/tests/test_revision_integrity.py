"""Bounded history reads, durable session recovery and managed-file boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tracemalloc

from alembic import command
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.migrations import _alembic_config, upgrade_database
from document_pipeline_api.models import DataTableRecord, FileRevisionDetailRecord, FileRevisionRecord, TaskRecord
from document_pipeline_api.services.current_data import (
    create_edit_session, current_snapshot, initialize_current, recover_edit_sessions, revise_current, seal_edit_session,
)
from document_pipeline_api.services.file_revisions import list_revisions, revision_details
from document_pipeline_api.storage_paths import resolve_original_reference

SCHEMA = [
    {"key": "customer", "label": "客户", "section": "header", "value_type": "text"},
    {"key": "product", "label": "商品", "section": "item", "value_type": "text"},
    {"key": "quantity", "label": "数量", "section": "item", "value_type": "number"},
]


def seed(engine, count):
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(DataTableRecord(id="table", name="测试", template_key="test", template_version="1", document_kind="custom"))
        session.add(TaskRecord(id="A", filename="证据.txt", content_type="text/plain", size_bytes=1,
                               sha256="synthetic", storage_path="A.txt", status="completed"))
        session.flush()
        initialize_current(session, task_id="A", table_id="table", header={"customer": "甲"}, schema=SCHEMA,
                           items=[{"product": f"商品{index}" + "长内容" * 50, "quantity": index} for index in range(count)])
        session.commit()


@pytest.mark.parametrize("count", [1000, 10000])
def test_large_revision_directory_and_three_details_do_not_load_full_history(tmp_path, count):
    engine = build_engine(f"sqlite:///{tmp_path / 'bounded.db'}")
    seed(engine, count)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, u, sql, *args: statements.append(sql))
    with Session(engine) as session:
        tracemalloc.start()
        directory = list_revisions(session, "A", page_size=3)
        revision = directory["revisions"][0]
        details = revision_details(session, "A", revision["id"], page_size=3)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert directory["total"] == 1 and revision["added_details"] == count
        assert details["total_details"] == count and len(details["items"]) == 3
        assert [item["after"]["item_index"] for item in details["items"]] == [1, 2, 3]
        assert peak < 2_000_000  # A 10k/long-value version is far larger than this.
        directory_sql = next(sql for sql in statements if 'file_revisions.id' in sql and 'LIMIT' in sql)
        assert "changes_json" not in directory_sql and "schema_json" not in directory_sql
        detail_sql = next(sql for sql in statements if "file_revision_details.change_json" in sql)
        assert "LIMIT" in detail_sql and "ORDER BY" in detail_sql
        # Last page is complete and ordered even though current rows can later disappear.
        last = revision_details(session, "A", revision["id"], page=(count + 2) // 3, page_size=3)
        assert last["items"][-1]["after"]["item_index"] == count
    engine.dispose()


def test_revision_counts_revert_and_delete_keep_full_last_content(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'counts.db'}")
    seed(engine, 2)
    with Session(engine) as session:
        row = current_snapshot(session, "A")["items"][0]
        editing = create_edit_session(session, owner="one-round")
        revise_current(session, "A", expected_version=0, header_changes={"customer": "乙"},
                       detail_changes={row["id"]: {"quantity": 7}}, edit_session_id=editing.id)
        session.commit()
        revision = list_revisions(session, "A")["revisions"][0]
        assert revision["modified_fields"] == 2
        revise_current(session, "A", expected_version=1, header_changes={"customer": "甲"},
                       deleted_ids=[row["id"]], edit_session_id=editing.id)
        seal_edit_session(session, editing.id)
        session.commit()
        details = revision_details(session, "A", revision["id"])
        assert details["modified_fields"] == 1 and details["deleted_details"] == 1
        assert details["header"] == {} and details["items"][0]["before"] == row
        assert details["items"][0]["deleted_before"]["values"] == {**row["values"], "quantity": 7}
        assert details["items"][0]["after"] is None
    engine.dispose()


@pytest.mark.parametrize("count", [1000, 10000])
def test_hundred_round_history_pages_stay_bounded(tmp_path, count, record_property):
    """Exercise twenty large versions and eighty small rounds in one file.

    The historical corpus is seeded in SQL to isolate read growth from the
    separately tested editor/session transaction behavior.
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'long-history.db'}")
    seed(engine, count)
    with Session(engine) as session:
        initial = session.scalar(select(FileRevisionRecord).where(FileRevisionRecord.version == 1))
        for revision_number in range(2, 102):
            large = revision_number <= 21
            session.add(FileRevisionRecord(
                id=f"round-{revision_number}", task_id="A", version=revision_number,
                operation="edit", editor="synthetic", schema_json=initial.schema_json,
                changes_json=json.dumps({"header": {} if large else {"customer": {
                    "before": f"客户{revision_number - 1}", "after": f"客户{revision_number}"}}, "items": {}}),
                modified_fields=count if large else 1, deleted_details=0, added_details=0,
                total_details=count if large else 0, sealed_at=initial.sealed_at,
            ))
            session.flush()
            if large:
                session.execute(text("""INSERT INTO file_revision_details
                    (revision_id,row_id,item_index,change_json)
                    SELECT :revision_id,row_id,item_index,
                      json_set(change_json,'$.before',json_extract(change_json,'$.after'),
                        '$.before.values.quantity',json_extract(change_json,'$.after.values.quantity')+:step-1,
                        '$.after.values.quantity',json_extract(change_json,'$.after.values.quantity')+:step)
                    FROM file_revision_details WHERE revision_id=:initial"""),
                    {"revision_id": f"round-{revision_number}", "step": revision_number - 1, "initial": initial.id})
        session.commit()
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, u, sql, *args: statements.append(sql))
    with Session(engine) as session:
        tracemalloc.start()
        started = time.perf_counter()
        directory = list_revisions(session, "A", page=5, page_size=20)
        detail = revision_details(session, "A", "round-21", page=2, page_size=3)
        elapsed, peak = time.perf_counter() - started, tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        record_property("hundred_round_page_seconds", elapsed)
        record_property("hundred_round_page_peak_bytes", peak)
        assert directory["total"] == 101 and len(directory["revisions"]) == 20
        assert detail["total_details"] == count and len(detail["items"]) == 3
        assert [item["after"]["item_index"] for item in detail["items"]] == [4, 5, 6]
        assert detail["items"][0]["before"]["values"]["quantity"] == 22
        assert detail["items"][0]["after"]["values"]["quantity"] == 23
        assert peak < 2_000_000
        detail_queries = [sql for sql in statements if "file_revision_details.change_json" in sql]
        assert len(detail_queries) == 1 and "LIMIT" in detail_queries[0]
    engine.dispose()


def test_process_exit_preserves_committed_current_and_open_revision(tmp_path):
    path = tmp_path / 'crash.db'
    engine = build_engine(f"sqlite:///{path}")
    seed(engine, 2)
    engine.dispose()
    code = '''
import os, sys
from sqlalchemy.orm import Session
from document_pipeline_api.db import build_engine
from document_pipeline_api.services.current_data import create_edit_session, revise_current
engine = build_engine(sys.argv[1])
with Session(engine) as session:
    editing = create_edit_session(session, owner="terminated-worker")
    revise_current(session, "A", expected_version=0, header_changes={"customer": "committed"}, edit_session_id=editing.id)
    session.commit()
    os._exit(72)
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / 'src')}
    result = subprocess.run([sys.executable, '-c', code, f"sqlite:///{path}"], env=env, timeout=30)
    assert result.returncode == 72
    engine = build_engine(f"sqlite:///{path}")
    with Session(engine) as session:
        assert current_snapshot(session, "A")["header"]["customer"] == 'committed'
        assert recover_edit_sessions(session, owner='restart') == 1
        session.commit()
        assert recover_edit_sessions(session, owner='restart') == 0
        revisions = list_revisions(session, "A")["revisions"]
        assert len(revisions) == 2 and all(item['sealed'] for item in revisions)
        assert revisions[0]['modified_fields'] == 1
    engine.dispose()


def test_valid_file_history_migration_is_lossless_and_idempotent(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    config = _alembic_config()
    changes = {'header': {'customer': {'before': '甲', 'after': '乙'}}, 'items': {
        '7': {'before': {'id': 7, 'item_index': 9, 'values': {'product': '原值', 'quantity': 1}}, 'after': None,
              'deleted_before': {'id': 7, 'item_index': 9, 'values': {'product': '原值', 'quantity': 3}}},
        '8': {'before': None, 'after': {'id': 8, 'item_index': 10, 'values': {'product': '新值', 'quantity': 4}}},
    }}
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0043_dynamic_groupings')
        connection.exec_driver_sql("INSERT INTO tasks (id,filename,content_type,size_bytes,sha256,storage_path,template_mode,status,attempt_count,created_at,updated_at) "
            "VALUES ('A','证据.txt','text/plain',1,'hash','A.txt','smart','completed',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.exec_driver_sql("INSERT INTO file_revisions (id,task_id,version,operation,editor,schema_json,changes_json,created_at) "
            "VALUES ('revision','A',2,'edit','user',?,?,CURRENT_TIMESTAMP)", (json.dumps(SCHEMA), json.dumps(changes)))
    upgrade_database(engine)
    upgrade_database(engine)
    with Session(engine) as session:
        revision = revision_details(session, 'A', 'revision')
        assert revision['header'] == changes['header']
        assert revision['items'] == list(changes['items'].values())
        assert revision['modified_fields'] == 2 and revision['added_details'] == revision['deleted_details'] == 1
        assert not revision['sealed']  # Open-session state remains recoverable.
        parent = session.get(FileRevisionRecord, 'revision')
        assert json.loads(parent.changes_json)['items'] == {}
        assert session.scalar(select(func.count()).select_from(FileRevisionDetailRecord)) == 2
        session.delete(parent)
        session.commit()
        assert session.scalar(select(func.count()).select_from(FileRevisionDetailRecord)) == 0
    engine.dispose()


@pytest.mark.parametrize('value', ['../A.txt', 'templates/../A.txt', 'templates/a/../../A.txt', 'wrong.txt'])
def test_invalid_relative_reference_never_reads_external(tmp_path, value):
    managed = tmp_path / 'managed'
    managed.mkdir()
    with pytest.raises(ValueError):
        resolve_original_reference(managed, value, 'A.txt')


def test_absolute_and_previous_external_reference_rejected_even_when_flat_copy_exists(tmp_path):
    managed, external = tmp_path / 'managed', tmp_path / 'external'
    managed.mkdir()
    external.mkdir()
    (managed / 'A.txt').write_text('internal')
    source = external / 'A.txt'
    source.write_text('external')
    for current, previous in ((str(source), None), ('A.txt', str(source))):
        with pytest.raises(ValueError):
            resolve_original_reference(managed, current, 'A.txt', previous)
    assert source.read_text() == 'external'
