import json
from time import perf_counter

from sqlalchemy import event

from test_table_management import _client, _create_manual_table
from document_pipeline_api.models import DataRowRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.services.source_cards import list_source_cards, source_card_details
from document_pipeline_api.services.table_scope import TableExportScope


def add_file(session, table_id, identifier, count, *, header_only=False):
    session.add(TaskRecord(id=identifier, filename='同名资料.pdf', content_type='application/pdf', size_bytes=1,
        sha256=identifier, storage_path=f'{identifier}.pdf', template_mode='invoice', status='completed'))
    session.flush()
    session.add(FileCurrentDataRecord(task_id=identifier, table_id=table_id,
        header_json=json.dumps({'seller_name': '文件客户' * 1000}), has_data=True, current_version=4))
    session.flush()
    session.add_all([DataRowRecord(table_id=table_id, task_id=identifier, item_index=index + 1,
        row_json=json.dumps({'name': f'商品{index}', 'quantity': index}), row_version=4,
        is_header_only=header_only) for index in range(count)])
    session.commit()


def test_ten_thousand_details_are_one_card_and_only_bounded_previews_are_loaded(tmp_path):
    with _client(tmp_path) as client:
        table_id = _create_manual_table(client)['id']
        with client.app.state.session_factory() as session:
            add_file(session, table_id, 'large-file', 10000)
            add_file(session, table_id, 'second-file', 1)
        loaded = []
        def track(session, instance):
            if isinstance(instance, DataRowRecord):
                loaded.append(instance.id)
        event.listen(client.app.state.session_factory.class_, 'loaded_as_persistent', track)
        try:
            with client.app.state.session_factory() as session:
                start = perf_counter()
                page = list_source_cards(session, table_id, page_size=1)
                elapsed = (perf_counter() - start) * 1000
                assert page['total'] == 2 and len(page['groups']) == 1
                card = page['groups'][0]
                assert card['task_id'] == 'large-file' and card['item_count'] == 10000
                assert card['matched_row_count'] == 10000 and card['complete']
                assert len(card['preview_items']) == 3
                assert card['preview_header'][0]['truncated']
                assert 'header' not in card and 'schema' not in card
                assert len(json.dumps(page, ensure_ascii=False)) < 5000
                assert len(loaded) == 3
                details = source_card_details(session, table_id, card['id'], page=500, page_size=20)
                assert details['total'] == 10000 and len(details['rows']) == 20
                assert details['rows'][-1]['item_index'] == 10000
                assert len(details['header']['seller_name']) == 4000
                print(f'10000 detail card: {elapsed:.1f}ms, {len(json.dumps(page, ensure_ascii=False))} characters')
        finally:
            event.remove(client.app.state.session_factory.class_, 'loaded_as_persistent', track)


def test_scoped_card_never_expands_selected_details_to_the_whole_file(tmp_path):
    with _client(tmp_path) as client:
        table_id = _create_manual_table(client)['id']
        with client.app.state.session_factory() as session:
            add_file(session, table_id, 'file', 10)
            whole = list_source_cards(session, table_id)['groups'][0]
            chosen = whole['preview_items'][1]['id']
            scope = TableExportScope(row_ids=[chosen])
            limited = list_source_cards(session, table_id, scope=scope)['groups'][0]
            assert not limited['complete'] and limited['matched_row_count'] == 1
            assert limited['row_count'] == 10
            detail = source_card_details(session, table_id, limited['id'], scope=scope)
            assert [row['id'] for row in detail['rows']] == [chosen]
            assert not detail['complete']


def test_independent_groups_share_only_equal_headers_and_remain_unlinked(tmp_path):
    with _client(tmp_path) as client:
        table_id = _create_manual_table(client)['id']
        with client.app.state.session_factory() as session:
            for index, (group, seller) in enumerate([('one', '甲'), ('one', '乙'), ('two', '丙'), ('two', '丙'), (None, '丁')], 1):
                values = {'seller_name': seller, 'source_filename': '同名.xlsx', 'name': f'商品{index}'}
                if group:
                    values['__row_group'] = group
                session.add(DataRowRecord(table_id=table_id, item_index=index, row_json=json.dumps(values)))
            session.commit()
            cards = list_source_cards(session, table_id)['groups']
            assert len(cards) == 3 and all(card['task_id'] is None for card in cards)
            assert sorted(card['row_count'] for card in cards) == [1, 2, 2]
            varied = next(card for card in cards if json.loads(card['id'])['id'] == 'one')
            shared = next(card for card in cards if json.loads(card['id'])['id'] == 'two')
            assert 'seller_name' not in varied['shared_header_keys']
            assert 'seller_name' in shared['shared_header_keys']
            detail = source_card_details(session, table_id, varied['id'])
            assert {row['values']['seller_name'] for row in detail['rows']} == {'甲', '乙'}
            assert next(column for column in detail['schema'] if column['key'] == 'seller_name')['section'] == 'item'
        table = client.get(f'/api/v1/tables/{table_id}').json()
        varied_rows = [row for row in table['rows'] if row['values'].get('__row_group') == 'one']
        shared_rows = [row for row in table['rows'] if row['values'].get('__row_group') == 'two']
        assert all('seller_name' not in row['shared_header_keys'] for row in varied_rows)
        assert all('seller_name' in row['shared_header_keys'] for row in shared_rows)
        for row, seller in [(varied_rows[0], '独立修改'), (shared_rows[0], '共同修改')]:
            response = client.patch(f'/api/v1/tables/{table_id}/rows/{row["id"]}', json={
                'expected_version': row['version'], 'changes': {'seller_name': seller}})
            assert response.status_code == 200, response.text
        table = client.get(f'/api/v1/tables/{table_id}').json()
        assert {row['values']['seller_name'] for row in table['rows'] if row['values'].get('__row_group') == 'one'} == {'独立修改', '乙'}
        assert {row['values']['seller_name'] for row in table['rows'] if row['values'].get('__row_group') == 'two'} == {'共同修改'}
        # A draft opened when this field varied cannot silently become a shared edit.
        first, second = [row for row in table['rows'] if row['values'].get('__row_group') == 'one']
        client.patch(f'/api/v1/tables/{table_id}/rows/{second["id"]}', json={
            'expected_version': second['version'], 'changes': {'seller_name': '独立修改'}})
        stale = client.patch(f'/api/v1/tables/{table_id}/rows/{first["id"]}', json={
            'expected_version': first['version'], 'changes': {'seller_name': '扩大覆盖'}, 'expected_shared_header_keys': first['shared_header_keys']})
        assert stale.status_code == 409, stale.text


def test_header_only_file_has_no_fake_detail_preview(tmp_path):
    with _client(tmp_path) as client:
        table_id = _create_manual_table(client)['id']
        with client.app.state.session_factory() as session:
            add_file(session, table_id, 'header', 1, header_only=True)
            card = list_source_cards(session, table_id)['groups'][0]
            assert card['item_count'] == 0 and card['row_count'] == 1 and card['preview_items'] == []


def test_source_card_api_preserves_search_and_rejects_invalid_identity(tmp_path):
    with _client(tmp_path) as client:
        table_id = _create_manual_table(client)['id']
        with client.app.state.session_factory() as session:
            add_file(session, table_id, 'file', 10)
        response = client.get(f'/api/v1/tables/{table_id}/source-cards', params={'search': '商品9', 'page_size': 1})
        assert response.status_code == 200, response.text
        card = response.json()['groups'][0]
        assert card['matched_row_count'] == 1 and not card['complete']
        response = client.get(f'/api/v1/tables/{table_id}/source-details', params={'source_key': card['id'], 'search': '商品9'})
        assert response.status_code == 200, response.text
        assert len(response.json()['rows']) == 1 and response.json()['rows'][0]['values']['name'] == '商品9'
        assert client.get(f'/api/v1/tables/{table_id}/source-details', params={'source_key': '{"kind":"file","id":{}}'}).status_code == 422
        assert client.get(f'/api/v1/tables/{table_id}/source-cards?page_size=500').status_code == 422


def test_long_search_match_stays_in_short_preview_and_full_details(tmp_path):
    with _client(tmp_path) as client:
        table_id = _create_manual_table(client)['id']
        full_text = '长文本。' * 2000 + '深处匹配' + '后文。' * 100
        with client.app.state.session_factory() as session:
            session.add(DataRowRecord(table_id=table_id, item_index=1, row_json=json.dumps({'name': full_text})))
            session.commit()
            scope = TableExportScope(search='深处匹配')
            card = list_source_cards(session, table_id, scope=scope)['groups'][0]
            preview = card['preview_items'][0]['fields'][0]
            assert '深处匹配' in preview['value'] and len(preview['value']) < 190
            assert preview['truncated']
            details = source_card_details(session, table_id, card['id'], scope=scope)
            assert details['rows'][0]['values']['name'] == full_text
