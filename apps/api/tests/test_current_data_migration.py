import json

from alembic import command
import pytest
from sqlalchemy import MetaData, Table, inspect, event

from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import _alembic_config
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.services.data_baseline_audit import audit_database


def old_database(path):
    engine = build_engine(f'sqlite:///{path}')
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0039_original_archive')
    header = dict(document_type='发票', seller_name='甲公司', buyer_name='乙公司', document_number='001',
                  document_date='2026-09-20', amount_before_tax=30, tax_amount=0, total_amount=30)
    items = [dict(name='一', quantity=1, amount=10), dict(name='二', quantity=2, amount=20)]
    metadata = MetaData()
    tasks = Table('tasks', metadata, autoload_with=engine)
    tables = Table('data_tables', metadata, autoload_with=engine)
    extractions = Table('extractions', metadata, autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(tasks.insert().values(id='file', filename='测试.png', content_type='image/png', size_bytes=1,
            sha256='test', storage_path='file.png', template_mode='invoice', status='completed', attempt_count=0,
            page_count=1, candidate_templates_json='[]', model_config_version='legacy-unversioned', created_at=utc_now(), updated_at=utc_now()))
        connection.execute(tables.insert().values(id='table', name='发票', template_key='invoice',
            template_version='builtin-v1', document_kind='invoice', created_at=utc_now()))
        connection.execute(extractions.insert().values(task_id='file', document_kind='invoice', model_name='test',
            prompt_version='test', rule_engine_version='legacy-unversioned', elapsed_seconds=0,
            result_json=json.dumps({**header, 'items': items}), validation_json='[]', evidence_json='[]', created_at=utc_now()))
    rows = Table('data_rows', MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        for index, item in enumerate(items, 1):
            values = {**header, 'name': item['name'], 'quantity': item['quantity'], 'item_amount': item['amount']}
            connection.execute(rows.insert().values(id=index + 10, table_id='table', task_id='file', item_index=index,
                row_json=json.dumps(values), created_at=utc_now(), updated_at=utc_now(), row_version=1, review_pending=False))
    return engine, config


def upgrade(engine, config):
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')


def test_real_upgrade_preserves_ids_current_values_and_original_snapshot(tmp_path):
    path = tmp_path / 'upgrade.db'
    engine, config = old_database(path)
    try:
        with engine.connect() as connection:
            original = connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one()
        assert audit_database(path)['ready']
        upgrade(engine, config)
        assert audit_database(path)['ready']
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one() == original
            assert connection.exec_driver_sql('SELECT id FROM data_rows ORDER BY id').scalars().all() == [11, 12]
            rows = connection.exec_driver_sql('SELECT row_json FROM data_rows').scalars().all()
            assert all('seller_name' not in json.loads(row) for row in rows)
            assert json.loads(connection.exec_driver_sql('SELECT header_json FROM file_current_data').scalar_one())['seller_name'] == '甲公司'
            assert connection.exec_driver_sql('SELECT count(*) FROM file_revisions').scalar_one() == 1
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        upgrade(engine, config)
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT count(*) FROM file_revisions').scalar_one() == 1
    finally:
        engine.dispose()


def test_mid_migration_failure_rolls_back_schema_and_all_values(tmp_path, monkeypatch):
    path = tmp_path / 'rollback.db'
    engine, config = old_database(path)
    try:
        with engine.connect() as connection:
            before = connection.exec_driver_sql('SELECT * FROM data_rows ORDER BY id').all()
        def fail(connection, cursor, statement, parameters, context, executemany):
            if 'INSERT INTO file_current_data' in statement:
                raise RuntimeError('simulated migration failure after DDL')
        event.listen(engine, 'before_cursor_execute', fail)
        try:
            with pytest.raises(RuntimeError, match='after DDL'):
                upgrade(engine, config)
        finally:
            event.remove(engine, 'before_cursor_execute', fail)
        assert 'file_current_data' not in inspect(engine).get_table_names()
        assert 'is_header_only' not in {column['name'] for column in inspect(engine).get_columns('data_rows')}
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT * FROM data_rows ORDER BY id').all() == before
            assert connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == '0039_original_archive'
        assert audit_database(path)['ready']
        upgrade(engine, config)
        assert audit_database(path)['ready']
    finally:
        engine.dispose()
