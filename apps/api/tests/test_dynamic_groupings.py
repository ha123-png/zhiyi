import json
from io import BytesIO
from time import perf_counter

from alembic import command
from openpyxl import load_workbook
from sqlalchemy import event

from test_table_management import _client, _create_manual_table, _add_row
from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import _alembic_config
from document_pipeline_api.models import DataRowRecord


def test_grouping_rules_survive_empty_data_discover_new_categories_and_can_be_removed(tmp_path):
    with _client(tmp_path) as client:
        table = _create_manual_table(client)
        identifier = table['id']
        assert client.post(f'/api/v1/tables/{identifier}/split', json={'field_key': 'seller_name'}).json() == []
        first = _add_row(client, identifier, seller_name='甲')
        group = client.get(f'/api/v1/tables/{identifier}/views').json()[0]
        assert group['field_value'] == '甲' and group['row_count'] == 1
        _add_row(client, identifier, seller_name='乙')
        _add_row(client, identifier, seller_name=None)
        _add_row(client, identifier)
        groups = client.get(f'/api/v1/tables/{identifier}/views').json()
        assert {(view['field_value'], view['row_count']) for view in groups} == {('甲', 1), ('乙', 1), (None, 2)}
        assert next(view for view in groups if view['field_value'] == '甲')['id'] == group['id']
        null_group = next(view for view in groups if view['field_value'] is None)
        assert null_group['name'] == '未填写'
        assert len(client.get(f"/api/v1/tables/{identifier}/views/{null_group['id']}").json()['rows']) == 2
        updated = client.patch(f"/api/v1/tables/{identifier}/rows/{first['id']}", json={'expected_version': first['version'], 'changes': {'seller_name': '丙'}})
        assert updated.status_code == 200
        # No refresh/list call before export: it must discover the new category itself.
        response = client.get(f'/api/v1/tables/{identifier}/export-views.xlsx')
        assert response.status_code == 200, response.text
        workbook = load_workbook(BytesIO(response.content))
        assert set(workbook.sheetnames) == {'乙', '丙', '未填写'}
        workbook.close()
        assert client.get(f"/api/v1/tables/{identifier}/views/{group['id']}").json()['row_count'] == 0
        assert client.delete(f'/api/v1/tables/{identifier}/views').status_code == 204
        assert client.get(f'/api/v1/tables/{identifier}/views').json() == []
        assert client.get(f'/api/v1/tables/{identifier}').json()['row_count'] == 4
        assert client.get(f"/api/v1/tables/{identifier}/rows/{first['id']}/revisions").json()[-1]['version'] == 2


def test_independent_sources_do_not_collapse_into_one_missing_file_bucket(tmp_path):
    with _client(tmp_path) as client:
        identifier = _create_manual_table(client)['id']
        with client.app.state.session_factory() as session:
            for index, group in enumerate(['one', 'one', 'two', 'two', None, None], 1):
                value = {'seller_name': f'记录 {index}', 'source_filename': '同名.xlsx'}
                if group:
                    value['__row_group'] = group
                session.add(DataRowRecord(table_id=identifier, item_index=index, row_json=json.dumps(value)))
            session.commit()
        response = client.post(f'/api/v1/tables/{identifier}/split', json={'field_key': 'source_filename', 'group_by': 'file'})
        assert response.status_code == 200, response.text
        groups = response.json()
        assert sorted(group['row_count'] for group in groups) == [1, 1, 2, 2]
        assert len({group['name'] for group in groups}) == 4
        for group in groups:
            rows = client.get(f"/api/v1/tables/{identifier}/views/{group['id']}").json()['rows']
            assert len(rows) == group['row_count']
            assert all(row['task_id'] is None for row in rows)
            if group['field_value']['kind'] == 'group':
                assert {row['values']['__row_group'] for row in rows} == {group['field_value']['id']}
            else:
                assert [row['id'] for row in rows] == [group['field_value']['id']]


def test_empty_grouping_rule_protects_its_column_until_removed(tmp_path):
    with _client(tmp_path) as client:
        identifier = _create_manual_table(client)['id']
        response = client.post(f'/api/v1/tables/{identifier}/columns', json={'label': '空分类', 'value_type': 'text', 'section': 'item'})
        assert response.status_code == 201, response.text
        key = response.json()['key']
        assert client.post(f'/api/v1/tables/{identifier}/split', json={'field_key': key}).json() == []
        assert client.get('/api/v1/tables').json()[0]['grouping_fields'] == [key]
        assert client.delete(f'/api/v1/tables/{identifier}/columns/{key}').status_code == 409
        assert client.delete(f'/api/v1/tables/{identifier}/views').status_code == 204
        assert client.get(f'/api/v1/tables/{identifier}').json()['grouping_fields'] == []
        assert client.delete(f'/api/v1/tables/{identifier}/columns/{key}').status_code == 204


def test_ten_thousand_categories_are_discovered_in_sql_and_returned_in_pages(tmp_path):
    with _client(tmp_path) as client:
        identifier = _create_manual_table(client)['id']
        with client.app.state.session_factory() as session:
            session.add_all([DataRowRecord(table_id=identifier, item_index=index + 1,
                row_json=json.dumps({'seller_name': f'类别 {index:05d}'})) for index in range(10000)])
            session.commit()
        loaded = []
        def track(session, instance):
            if isinstance(instance, DataRowRecord):
                loaded.append(instance.id)
        event.listen(client.app.state.session_factory.class_, 'loaded_as_persistent', track)
        try:
            start = perf_counter()
            first = client.post(f'/api/v1/tables/{identifier}/split', json={'field_key': 'seller_name'})
            initial_ms = (perf_counter() - start) * 1000
            assert first.status_code == 200, first.text
            assert len(first.json()) == 200
            next_page = client.get(f'/api/v1/tables/{identifier}/views?offset=200')
            assert len(next_page.json()) == 200
            assert set(view['id'] for view in first.json()).isdisjoint(view['id'] for view in next_page.json())
            last = client.get(f'/api/v1/tables/{identifier}/views?offset=9800')
            assert len(last.json()) == 200
            assert last.json()[-1]['name'] == '类别 09999'
            assert loaded == []
            print(f'10000 categories: initial {initial_ms:.1f}ms, first page {len(first.content)} bytes')
        finally:
            event.remove(client.app.state.session_factory.class_, 'loaded_as_persistent', track)


def test_migration_keeps_existing_category_ids_and_records_their_rules(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'grouping-migration.db'}")
    config = _alembic_config()
    try:
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '0042_autonomous_contract')
            connection.exec_driver_sql("INSERT INTO data_tables(id,name,template_key,template_version,document_kind,created_at) VALUES('t','表','custom','1','custom',CURRENT_TIMESTAMP)")
            for identifier, field, value in [('field', 'seller', '"甲"'), ('file', '', '"file-a"'), ('legacy-unlinked', '', 'null')]:
                connection.exec_driver_sql('INSERT INTO data_views(id,table_id,name,field_key,field_value_json,created_at,updated_at) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)', (identifier, 't', '分类', field, value))
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '0043_dynamic_groupings')
            assert json.loads(connection.exec_driver_sql('SELECT grouping_json FROM data_tables').scalar_one()) == ['', 'seller']
            assert set(connection.exec_driver_sql('SELECT id FROM data_views').scalars()) == {'file', 'field'}
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '0043_dynamic_groupings')
            assert connection.exec_driver_sql('SELECT count(*) FROM data_views').scalar_one() == 2
    finally:
        engine.dispose()
