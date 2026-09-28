import json

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from test_table_management import _client, _create_manual_table, _add_row
from document_pipeline_api.models import DataRowRevisionRecord
from document_pipeline_api.services.assistant_tools import Context, execute_tool


def test_deleted_independent_data_has_bounded_history_and_does_not_grant_past_scope(tmp_path):
    with _client(tmp_path) as client:
        table = _create_manual_table(client)['id']
        other_table = _create_manual_table(client, '其他表')['id']
        row = _add_row(client, table, seller_name='留在历史的客户', total_amount=42)
        other = _add_row(client, other_table, seller_name='不能跨表读到')
        with client.app.state.session_factory() as session:
            for version in range(2, 62):
                session.add(DataRowRevisionRecord(table_id=table, row_id=row['id'], task_id=None,
                    version=version, operation='table_edit', editor='test', before_json='{"total_amount":1}',
                    after_json=json.dumps({'total_amount': version, '__row_group': 'internal'})))
            session.commit()
        response = client.request('DELETE', f'/api/v1/tables/{table}/rows', json={'row_ids': [row['id']]})
        assert response.status_code == 204
        assert client.get(f'/api/v1/tables/{table}').json()['row_count'] == 0
        history = client.get(f'/api/v1/tables/{table}/row-history?page_size=1').json()
        assert history['total'] == 62 and len(history['revisions']) == 1
        deletion = history['revisions'][0]
        assert deletion['operation'] == 'delete'
        assert deletion['before'] == row['values'] and deletion['after'] is None
        old_api = client.get(f'/api/v1/tables/{table}/rows/{row["id"]}/revisions').json()
        assert len(old_api) == 50
        assert all('__row_group' not in (record['after'] or {}) for record in old_api)
        assert len(client.get(f'/api/v1/tables/{table}/rows/{row["id"]}/revisions?page=2').json()) == 12
        assert client.get(f'/api/v1/tables/{table}/rows/{other["id"]}/revisions').status_code == 404
        assert client.get(f'/api/v1/tables/{table}/row-history?row_id={other["id"]}').json()['total'] == 0
        with client.app.state.session_factory() as session:
            loaded = []
            event.listen(session, 'loaded_as_persistent', lambda _, value: loaded.append(value))
            tool = execute_tool(session, Context(table_id=table), 'read_resource', {'kind': 'revisions', 'id': table}, {})
            assert tool['total'] == 62 and len(tool['revisions']) == 3
            assert sum(isinstance(value, DataRowRevisionRecord) for value in loaded) == 3
            for context in (Context(table_id=table, row_ids=[row['id']]), Context(table_id=table, row_ids=[]),
                            Context(table_id=table, search='留在历史的客户'), Context(table_id=table, source_keys=[])):
                for arguments in ({'kind': 'revisions', 'id': table}, {'kind': 'revisions', 'id': table, 'row_id': row['id']}):
                    with pytest.raises(HTTPException) as error:
                        execute_tool(session, context, 'read_resource', arguments, {})
                    assert error.value.status_code in (403, 409)
        assert client.delete(f'/api/v1/tables/{table}').status_code == 204
        assert client.get(f'/api/v1/tables/{table}/row-history').status_code == 404
