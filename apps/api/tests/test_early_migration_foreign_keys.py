"""Adding template snapshots must preserve populated early databases."""
import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import _alembic_config


@pytest.mark.parametrize('child', ['extraction', 'review', 'duplicate'])
def test_template_snapshot_migration_keeps_referenced_tasks_and_foreign_keys(tmp_path, child):
    engine = build_engine(f"sqlite:///{tmp_path / 'early.db'}")
    config = _alembic_config()
    try:
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '0004_template_versions')
            connection.exec_driver_sql("""INSERT INTO tasks
                (id,filename,content_type,size_bytes,sha256,storage_path,template_mode,status,created_at,updated_at)
                VALUES ('original','synthetic.txt','text/plain',1,'synthetic','synthetic.txt','smart','failed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
            if child == 'extraction':
                connection.exec_driver_sql("""INSERT INTO extractions
                    (task_id,document_kind,model_name,prompt_version,elapsed_seconds,result_json,validation_json,created_at)
                    VALUES ('original','custom','synthetic','synthetic',0,'{}','[]',CURRENT_TIMESTAMP)""")
            elif child == 'review':
                connection.exec_driver_sql("""INSERT INTO review_revisions
                    (task_id,version,result_json,validation_json,changes_json,editor,created_at)
                    VALUES ('original',1,'{}','[]','{}','synthetic',CURRENT_TIMESTAMP)""")
            else:
                connection.exec_driver_sql("""INSERT INTO tasks
                    (id,filename,content_type,size_bytes,sha256,storage_path,template_mode,status,duplicate_of_task_id,created_at,updated_at)
                    VALUES ('duplicate','synthetic.txt','text/plain',1,'synthetic','duplicate.txt','smart','failed','original',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            tables = ['tasks', 'extractions', 'review_revisions']
            original = {table: connection.exec_driver_sql(f'SELECT * FROM {table} ORDER BY 1').all() for table in tables}
            command.upgrade(config, '0005_task_template_snapshots')
            assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
            assert connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == '0005_task_template_snapshots'
            for table in tables:
                columns = list(original[table][0]._mapping) if original[table] else ['*']
                assert connection.exec_driver_sql(f'SELECT {",".join(columns)} FROM {table} ORDER BY 1').all() == original[table]
            for table in ['tasks', 'extractions']:
                assert any(fk['constrained_columns'] == ['template_id'] and fk['referred_table'] == 'templates'
                           for fk in inspect(connection).get_foreign_keys(table))
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.exec_driver_sql("UPDATE tasks SET template_id='missing-template' WHERE id='original'")
            command.downgrade(config, '0004_template_versions')
            for table in tables:
                assert connection.exec_driver_sql(f'SELECT * FROM {table} ORDER BY 1').all() == original[table]
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
    finally:
        engine.dispose()
