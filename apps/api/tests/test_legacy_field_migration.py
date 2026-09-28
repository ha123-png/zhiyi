import json

import pytest
from sqlalchemy import MetaData, Table, inspect
from sqlalchemy.orm import Session

from test_current_data_migration import old_database, upgrade
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.data_tables import DataRowUpdate
from document_pipeline_api.services.data_baseline_audit import audit_database
from document_pipeline_api.services.data_rows import update_data_row
from document_pipeline_api.services.legacy_fields import normalize_fields, normalize_paths
from document_pipeline_api.services.legacy_migration_choices import file_fingerprint
from document_pipeline_api.services.templates import get_template, get_template_version


def copied_invoice(path):
    engine, config = old_database(path)
    fields = [{'key': key, 'label': label, 'section': section, 'value_type': kind} for key, label, section, kind in (
        ('seller_name', '客户', 'header', 'text'), ('document_number', '单号', 'header', 'text'),
        ('tax_amount', '总税额', 'header', 'number'), ('name', '商品', 'item', 'text'),
        ('item_amount', '金额', 'item', 'number'), ('tax_amount', '明细税额', 'item', 'number'))]
    rules = [{'kind': 'equation', 'field': 'header.tax_amount',
              'left': {'op': 'sum', 'path': 'items[].tax_amount'},
              'right': {'op': 'field', 'path': 'header.tax_amount'}, 'tolerance': .01, 'severity': 'error'}]
    header = {'seller_name': '甲', 'document_number': '001', 'tax_amount': 10}
    items = [{'name': '一', 'item_amount': 10, 'tax_amount': 4}, {'name': '二', 'item_amount': 20, 'tax_amount': 6}]
    metadata = MetaData()
    templates = Table('templates', metadata, autoload_with=engine)
    versions = Table('template_versions', metadata, autoload_with=engine)
    original = json.dumps({'header': header, 'items': items}, ensure_ascii=False)
    with engine.begin() as connection:
        connection.execute(templates.insert().values(id='copied', is_system=False, is_active=True, in_smart_pool=True,
            current_version=1, created_at=utc_now(), updated_at=utc_now()))
        connection.execute(versions.insert().values(template_id='copied', version=1, name='旧复制发票', description='',
            extra_instructions='', fields_json=json.dumps(fields), validation_rules_json='[]',
            deterministic_rules_json=json.dumps(rules), output_mapping_json='{}', behavior_json='{}', created_at=utc_now()))
        connection.exec_driver_sql("UPDATE data_tables SET template_key='copied', template_version='1', document_kind='custom'")
        connection.exec_driver_sql("UPDATE tasks SET template_id='copied', template_version=1, template_mode='custom'")
        connection.exec_driver_sql("UPDATE extractions SET document_kind='custom', template_id='copied', template_version=1, result_json=?", (original,))
        for index, item in enumerate(items, 11):
            connection.exec_driver_sql('UPDATE data_rows SET row_json=?,row_version=7 WHERE id=?', (json.dumps({**header, **item}), index))
    return engine, config, original, fields


def choice_for(engine):
    with engine.connect() as connection:
        fingerprint = file_fingerprint(connection.connection.driver_connection, 'file')
    return {'format_version': 1, 'files': [{'task_id': 'file', 'fingerprint': fingerprint, 'has_data': True,
        'header': {'seller_name': '甲', 'document_number': 'warehouse', 'tax_amount': 10},
        'items': [{'id': 11, 'item_index': 1, 'values': {'name': '一', 'item_amount': 10, 'item_tax_amount': 4}},
                  {'id': 12, 'item_index': 2, 'values': {'name': '二', 'item_amount': 20, 'item_tax_amount': 6}}]}]}


def change_warehouse(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE data_rows SET row_json=json_set(row_json,'$.document_number','warehouse')")


def test_colliding_keys_migrate_as_two_facts_and_pinned_rules_still_work(tmp_path):
    path = tmp_path / 'collision.db'
    engine, config, original, fields = copied_invoice(path)
    try:
        report = audit_database(path)
        assert report['ready']
        assert report['issues'][0]['code'] == 'header_item_key_collision'
        upgrade(engine, config)
        assert audit_database(path)['ready']
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one() == original
            assert json.loads(connection.exec_driver_sql("SELECT fields_json FROM template_versions WHERE template_id='copied' AND version=1").scalar_one()) == fields
            assert json.loads(connection.exec_driver_sql('SELECT header_json FROM file_current_data').scalar_one())['tax_amount'] == 10
            assert [json.loads(value)['item_tax_amount'] for value in connection.exec_driver_sql('SELECT row_json FROM data_rows ORDER BY id').scalars()] == [4, 6]
            assert connection.exec_driver_sql('SELECT current_version FROM file_current_data').scalar_one() == 8
        with Session(engine) as session:
            template = get_template(session, 'copied')
            old_template = get_template_version(session, 'copied', 1)
            assert template.version == 2
            assert [field.key for field in old_template.fields] == [field.key for field in template.fields]
            update_data_row(session, 'table', 11, DataRowUpdate(expected_version=8, changes={'item_tax_amount': 5}))
        with engine.connect() as connection:
            issues = json.loads(connection.exec_driver_sql('SELECT validation_json FROM file_current_data').scalar_one())
            assert any(issue['field'] == 'header.tax_amount' for issue in issues)
            assert json.loads(connection.exec_driver_sql('SELECT header_json FROM file_current_data').scalar_one())['tax_amount'] == 10
    finally:
        engine.dispose()


def test_one_time_choice_is_applied_without_changing_legacy_evidence(tmp_path):
    path = tmp_path / 'choice.db'
    engine, config, original, _ = copied_invoice(path)
    try:
        change_warehouse(engine)
        assert not audit_database(path)['ready']
        with pytest.raises(RuntimeError, match='旧数据歧义'):
            upgrade(engine, config)
        config.attributes['v050_current_choices'] = choice_for(engine)
        upgrade(engine, config)
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one() == original
            assert json.loads(connection.exec_driver_sql('SELECT header_json FROM file_current_data').scalar_one())['document_number'] == 'warehouse'
            assert 'migration_choice' in json.loads(connection.exec_driver_sql('SELECT changes_json FROM file_revisions').scalar_one())
        assert audit_database(path)['ready']
    finally:
        engine.dispose()


def test_stale_choice_fails_before_schema_or_data_changes(tmp_path):
    engine, config, _, _ = copied_invoice(tmp_path / 'stale.db')
    try:
        change_warehouse(engine)
        config.attributes['v050_current_choices'] = choice_for(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql('UPDATE data_rows SET row_version=8 WHERE id=11')
        with pytest.raises(RuntimeError, match='过期选择'):
            upgrade(engine, config)
        assert 'file_current_data' not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_alias_allocation_preserves_existing_keys_and_rule_sections():
    fields = [{'key': 'tax', 'section': 'header'}, {'key': 'tax', 'section': 'item'},
              {'key': 'item_tax', 'section': 'item'}]
    normalized, aliases = normalize_fields(fields)
    assert [field['key'] for field in normalized] == ['tax', 'item_tax_2', 'item_tax']
    assert normalize_paths(['header.tax', 'items[].tax', 'item.tax'], aliases) == ['header.tax', 'items[].item_tax_2', 'item.item_tax_2']


def test_confirmed_empty_legacy_file_stays_empty_and_retains_evidence(tmp_path):
    path = tmp_path / 'empty.db'
    engine, config = old_database(path)
    try:
        with engine.begin() as connection:
            original = connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one()
            connection.exec_driver_sql('DELETE FROM data_rows')
        with engine.connect() as connection:
            fingerprint = file_fingerprint(connection.connection.driver_connection, 'file')
        config.attributes['v050_current_choices'] = {'format_version': 1, 'files': [{
            'task_id': 'file', 'fingerprint': fingerprint, 'has_data': False, 'header': {}, 'items': []}]}
        upgrade(engine, config)
        assert audit_database(path)['ready']
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT count(*) FROM data_rows').scalar_one() == 0
            assert connection.exec_driver_sql('SELECT has_data,header_json,validation_json FROM file_current_data').one() == (0, '{}', '[]')
            assert connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one() == original
            assert connection.exec_driver_sql('SELECT count(*) FROM file_revisions').scalar_one() == 1
    finally:
        engine.dispose()


def test_invalid_choice_rolls_back_the_whole_upgrade(tmp_path):
    engine, config, original, _ = copied_invoice(tmp_path / 'invalid.db')
    try:
        change_warehouse(engine)
        manifest = choice_for(engine)
        manifest['files'][0]['items'][0]['values']['item_tax_amount'] = 'not a number'
        config.attributes['v050_current_choices'] = manifest
        with pytest.raises(RuntimeError, match='类型不正确'):
            upgrade(engine, config)
        assert 'file_current_data' not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT result_json FROM extractions').scalar_one() == original
            assert connection.exec_driver_sql('SELECT row_version FROM data_rows WHERE id=11').scalar_one() == 7
    finally:
        engine.dispose()


def test_unrelated_legacy_date_format_is_preserved_in_a_choice():
    from document_pipeline_api.services.legacy_migration_choices import validate_values
    columns = [{'key': 'date', 'label': '日期', 'value_type': 'date'}]
    validate_values({'date': '2017年12月01日'}, columns, unchanged={'date': '2017年12月01日'})
    with pytest.raises(RuntimeError, match='类型不正确'):
        validate_values({'date': '明天'}, columns, unchanged={'date': '2017年12月01日'})


def test_independent_flat_collision_is_not_guessed_from_an_unrelated_file(tmp_path):
    engine, config, _, _ = copied_invoice(tmp_path / 'independent.db')
    try:
        rows = Table('data_rows', MetaData(), autoload_with=engine)
        with engine.begin() as connection:
            connection.execute(rows.insert().values(table_id='table', task_id=None, item_index=1,
                row_json='{"tax_amount": 55}', row_version=1, review_pending=False,
                created_at=utc_now(), updated_at=utc_now()))
        with pytest.raises(RuntimeError, match='未猜测拆分'):
            upgrade(engine, config)
        assert 'file_current_data' not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
