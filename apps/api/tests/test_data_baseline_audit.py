import json
import sqlite3

import pytest

from document_pipeline_api.services.data_baseline_audit import audit_database


def legacy_database(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE alembic_version (version_num TEXT);
        INSERT INTO alembic_version VALUES ('0039_original_archive');
        CREATE TABLE tasks (id TEXT PRIMARY KEY, filename TEXT);
        CREATE TABLE extractions (task_id TEXT PRIMARY KEY, document_kind TEXT, result_json TEXT);
        CREATE TABLE review_revisions (task_id TEXT, version INTEGER, result_json TEXT);
        CREATE TABLE data_tables (id TEXT PRIMARY KEY);
        CREATE TABLE data_rows (id INTEGER PRIMARY KEY, table_id TEXT, task_id TEXT, item_index INTEGER, row_json TEXT);
        CREATE TABLE confirmed_documents (task_id TEXT, result_json TEXT);
        INSERT INTO tasks VALUES ('file-a', 'invoice.png');
        INSERT INTO data_tables VALUES ('table-a');
    """)
    return connection


def insert_data(connection, *, header=None, items=None, rows=None):
    header = {"customer": "甲", "total": 0, "paid": False} if header is None else header
    items = [{"product": "一", "amount": 10}, {"product": "二", "amount": 20}] if items is None else items
    result = json.dumps({"header": header, "items": items})
    connection.execute("INSERT INTO extractions VALUES ('file-a', 'custom', ?)", (result,))
    if rows is None:
        rows = [{**header, **item} for item in items or [{}]]
    for index, values in enumerate(rows, 1):
        connection.execute("INSERT INTO data_rows VALUES (?, 'table-a', 'file-a', ?, ?)",
                           (index, index, json.dumps({"source_filename": "invoice.png", "item_index": index, **values})))
    connection.commit()


def codes(report):
    return {entry["code"] for entry in report["issues"]}


def test_exact_data_can_migrate_without_modifying_database(tmp_path):
    path = tmp_path / "old.db"
    with legacy_database(path) as connection:
        insert_data(connection)
    before = path.read_bytes()
    report = audit_database(path)
    assert report["ready"]
    assert report["counts"]["matching_files"] == 1
    assert path.read_bytes() == before


def test_disagreement_never_silently_selects_a_side(tmp_path):
    path = tmp_path / "old.db"
    with legacy_database(path) as connection:
        insert_data(connection)
        connection.execute("UPDATE data_rows SET row_json=? WHERE id=1", (json.dumps({"customer": "乙", "total": 0, "paid": False, "product": "一", "amount": 10}),))
    report = audit_database(path)
    assert not report["ready"]
    assert {"different_current_data", "inconsistent_header"} <= codes(report)
    assert "乙" not in json.dumps(report, ensure_ascii=False)


def test_missing_rows_does_not_restore_old_snapshot(tmp_path):
    path = tmp_path / "old.db"
    with legacy_database(path) as connection:
        insert_data(connection, rows=[])
    report = audit_database(path)
    assert "missing_current_rows" in codes(report)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM data_rows").fetchone()[0] == 0


def test_header_item_collision_is_reported_even_if_flat_values_match(tmp_path):
    path = tmp_path / "old.db"
    with legacy_database(path) as connection:
        insert_data(connection, header={"amount": 1000}, items=[{"amount": 10}])
    assert "header_item_key_collision" in codes(audit_database(path))


def test_zero_and_false_are_not_treated_as_empty(tmp_path):
    path = tmp_path / "old.db"
    with legacy_database(path) as connection:
        insert_data(connection, header={"total": 0, "paid": False}, items=[], rows=[{}])
    assert "different_current_data" in codes(audit_database(path))


def test_false_and_zero_are_not_the_same_legacy_fact(tmp_path):
    path = tmp_path / 'bool.db'
    with legacy_database(path) as connection:
        insert_data(connection, header={'paid': False}, items=[], rows=[{'paid': 0}])
    assert 'different_current_data' in codes(audit_database(path))


def test_wal_commits_are_included_and_audit_cannot_write(tmp_path):
    path = tmp_path / "wal.db"
    with legacy_database(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        insert_data(connection)
        assert audit_database(path)["counts"]["data_rows"] == 2
        connection.execute("DELETE FROM data_rows WHERE id=2")
        connection.commit()
        assert "different_current_data" in codes(audit_database(path))


def test_missing_path_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        audit_database(path)
    assert not path.exists()


def test_partial_migration_is_not_mistaken_for_completed_migration(tmp_path):
    path = tmp_path / 'partial.db'
    with legacy_database(path) as connection:
        insert_data(connection)
        connection.execute('CREATE TABLE file_current_data (task_id TEXT PRIMARY KEY)')
    report = audit_database(path)
    assert not report['ready']
    assert 'incomplete_current_schema' in codes(report)


def test_current_schema_audit_checks_missing_parent_and_does_not_change_data(tmp_path):
    from document_pipeline_api.config import Settings
    from document_pipeline_api.main import create_app
    from fastapi.testclient import TestClient
    path = tmp_path / 'current.db'
    settings = Settings(database_url=f'sqlite:///{path}', storage_dir=tmp_path / 'uploads', queue_enabled=False)
    with TestClient(create_app(settings)):
        assert audit_database(path)['ready']
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO data_rows (table_id, task_id, item_index, row_json, row_version, review_pending, created_at, updated_at, is_header_only) VALUES ('missing-table', 'missing-file', 1, '{}', 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)")
    report = audit_database(path)
    assert not report['ready']
    assert 'orphan_current_row' in codes(report)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT count(*) FROM data_rows').fetchone()[0] == 1
