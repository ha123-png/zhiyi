import hashlib
import json
import sys

import pytest
from sqlalchemy import inspect

from document_pipeline_api.launcher import main
from document_pipeline_api.db import build_engine
from document_pipeline_api.supervisor import SingleInstance
from test_current_data_migration import old_database


def invoke(monkeypatch, *arguments):
    monkeypatch.setattr(sys, 'argv', ['ZhiyiCLI', *map(str, arguments)])
    main()


def test_packaged_audit_is_readonly_then_explicit_offline_upgrade_preserves_ids(monkeypatch, tmp_path):
    path = tmp_path / 'document-pipeline.db'
    engine, _ = old_database(path)
    engine.dispose()
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    report = tmp_path / 'preflight.json'
    invoke(monkeypatch, 'audit-data', '--data-dir', tmp_path, '--report', report)
    assert json.loads(report.read_text(encoding='utf-8'))['ready']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    with pytest.raises(FileExistsError):
        invoke(monkeypatch, 'audit-data', '--data-dir', tmp_path, '--report', report)
    with SingleInstance(tmp_path / 'runtime/instance.lock'):
        with pytest.raises(RuntimeError, match='已经在运行'):
            invoke(monkeypatch, 'upgrade-data', '--data-dir', tmp_path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    invoke(monkeypatch, 'upgrade-data', '--data-dir', tmp_path)
    backups = list((tmp_path/'backups').glob('*.db'))
    assert len(backups) == 1
    engine = build_engine(f'sqlite:///{path}')
    try:
        assert 'file_current_data' in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT id FROM data_rows ORDER BY id').scalars().all() == [11, 12]
        invoke(monkeypatch, 'upgrade-data', '--data-dir', tmp_path)
        assert list((tmp_path/'backups').glob('*.db')) == backups
    finally:
        engine.dispose()


def test_upgrade_does_not_infer_target_or_create_missing_database(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match='明确指定'):
        invoke(monkeypatch, 'upgrade-data')
    with pytest.raises(SystemExit, match='未创建或修改'):
        invoke(monkeypatch, 'upgrade-data', '--data-dir', tmp_path/'missing')
    assert not (tmp_path/'missing').exists()
    with pytest.raises(SystemExit) as rejected:
        invoke(monkeypatch, 'start', '--migration-choices', tmp_path/'choices.json')
    assert rejected.value.code == 2


def test_conflicting_old_workspace_stops_without_a_product_choice_flow(monkeypatch, tmp_path):
    database = tmp_path / 'document-pipeline.db'
    engine, _ = old_database(database)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE data_rows SET row_json=json_set(row_json,'$.seller_name','另一客户') WHERE id=11")
            before = connection.exec_driver_sql('SELECT id,row_json FROM data_rows ORDER BY id').all()
            evidence = connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one()
        with pytest.raises(SystemExit, match='干净工作区'):
            invoke(monkeypatch, 'upgrade-data', '--data-dir', tmp_path)
        assert 'file_current_data' not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT id,row_json FROM data_rows ORDER BY id').all() == before
            assert connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one() == evidence
        assert list((tmp_path / 'backups').glob('*.db'))
    finally:
        engine.dispose()
