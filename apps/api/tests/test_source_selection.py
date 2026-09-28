import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from test_file_data_api import client as file_client, get, table
from test_assistant import ScriptedProvider, select_profile, send, finish
from document_pipeline_api.models import FileRevisionRecord, TaskRecord
from document_pipeline_api.services.assistant_tools import (
    Context, check_context, scoped_analysis, file_row_scope, permit_read,
    scoped_tool_specs, execute_tool, change_preview, Changes,
)
from document_pipeline_api.services.assistant_operations import Plan, preview_plan
from document_pipeline_api.services.assistant_analysis import AnalysisRequest, analyze
from document_pipeline_api.services.assistant_memory import scope_identity
from document_pipeline_api.services.assistant_refresh import refreshed_result


client = file_client


def cards(client, table_id, **params):
    response = client.get(f'/api/v1/tables/{table_id}/source-cards', params=params)
    assert response.status_code == 200, response.text
    return response.json()['groups']


def test_task_only_assistant_can_analyze_and_edit_only_its_current_file(client):
    rows = table(client)['rows']
    table_id = table(client)['id']
    a_rows = [row for row in rows if row['task_id'] == 'A']
    b_row = next(row for row in rows if row['task_id'] == 'B')
    with client.app.state.session_factory() as session:
        context = check_context(session, Context(task_id='A', capabilities=['data']))
        assert context.table_id == table_id
        names = {spec['function']['name'] for spec in scoped_tool_specs(
            session, context, {}, False, '统计这个文件的金额并修改供应方')}
        assert {'analyze_data_table', 'read_data_rows', 'propose_operations'} <= names
        result = execute_tool(session, context, 'analyze_data_table',
                              {'table_id': table_id, 'metrics': [{'op': 'sum', 'field': 'item_amount'}]}, {})
        assert result['source']['row_count'] == 2
        assert result['source']['document_count'] == 1
        assert result['totals']['sum:item_amount'] == 30
        catalog = execute_tool(session, context, 'catalog', {'kind': 'tables'}, {})
        assert catalog['items'][0]['rows'] == 2
        header = change_preview(session, context, Changes(
            table_id=table_id, row_id=a_rows[0]['id'], changes={'seller_name': '核对后甲'}))
        assert {item['row_id'] for item in header['affected']} == {row['id'] for row in a_rows}
        with pytest.raises(HTTPException) as denied:
            change_preview(session, context, Changes(
                table_id=table_id, row_id=b_row['id'], changes={'seller_name': '越界'}))
        assert denied.value.status_code == 403
        with pytest.raises(HTTPException) as denied:
            preview_plan(session, context, Plan(title='整表新增', operations=[
                {'kind': 'add_rows', 'table_id': table_id, 'rows': [{'name': '越界'}]}]))
        assert denied.value.status_code == 403
        many = check_context(session, Context(task_ids=['A', 'B']))
        assert many.table_id is None and many.table_ids == []
        with pytest.raises(HTTPException) as denied:
            execute_tool(session, many, 'analyze_data_table', {'table_id': table_id}, {})
        assert denied.value.status_code == 403


def test_task_only_run_and_authorization_update_share_canonical_scope(client):
    ScriptedProvider.plan = []
    client.app.state.conversation_factory = ScriptedProvider
    profile = select_profile(client)
    detail = finish(client, send(client, profile, context={'task_id': 'A'}))
    response = client.put(f"/api/v1/assistant/threads/{detail['id']}/authorization", json={
        'context': {'task_id': 'A', 'table_name': '显示名称已变',
                    'capabilities': ['organize', 'tasks', 'data']}, 'profile_id': profile['id'],
        'profile_version': profile['version'], 'remote_authorized': False,
    })
    assert response.status_code == 200, response.text
    assert response.json()['authorization_revision'] == 1
    assert response.json()['invalidated_tools'] == 0


def test_whole_source_selection_is_identical_for_export_analysis_and_file_permissions(client):
    table_id = table(client)['id']
    selected = next(card for card in cards(client, table_id) if card['task_id'] == 'A')
    keys = [selected['id']]
    export = client.get(f'/api/v1/tables/{table_id}/export.json', params={'source_keys': json.dumps(keys)})
    assert export.status_code == 200, export.text
    assert export.headers['X-Zhiyi-Row-Count'] == '2'
    assert len(cards(client, table_id, source_keys=json.dumps(keys))) == 1
    assert cards(client, table_id, source_keys='[]') == []
    assert client.get(f'/api/v1/tables/{table_id}/source-cards', params={'source_keys': 'null'}).status_code == 422
    with client.app.state.session_factory() as session:
        context = check_context(session, Context(table_id=table_id, source_keys=keys))
        request = scoped_analysis(session, context, AnalysisRequest(table_id=table_id, records=True, limit=30))
        result = analyze(session, request)
        assert result['source']['row_count'] == 2
        assert result['source']['document_count'] == 1
        assert result['source']['source_keys'] == keys
        permit_read(session, context, 'task', 'A')
        assert file_row_scope(session, context, 'A') is None
        with pytest.raises(HTTPException) as error:
            permit_read(session, context, 'task', 'B')
        assert error.value.status_code == 403
        empty = Context(table_id=table_id, source_keys=[])
        assert analyze(session, scoped_analysis(session, empty, AnalysisRequest(table_id=table_id, records=True)))['source']['row_count'] == 0
        with pytest.raises(HTTPException):
            permit_read(session, empty, 'task', 'A')
        limited = context.model_copy(update={'search': '商品一'})
        assert file_row_scope(session, limited, 'A') is not None
        result['analysis_id'] = 'snapshot'
        call = SimpleNamespace(id='saved', status='completed', result_json=json.dumps(result))
        assert refreshed_result(session, call, context)['source']['row_count'] == 2
        with pytest.raises(HTTPException) as error:
            refreshed_result(session, call, Context(table_id=table_id))
        assert error.value.status_code == 409
        assert scope_identity(empty.model_dump()) != scope_identity(Context(table_id=table_id).model_dump())


def test_source_clear_rejects_partial_scope_and_stale_snapshot_without_partial_deletion(client):
    data = table(client)
    table_id = data['id']
    selected = cards(client, table_id)
    body = {'sources': [{'id': card['id'], 'version': card['version']} for card in selected]}
    url = f'/api/v1/tables/{table_id}/source-cards/clear'
    assert client.post(url, params={'search': '商品一'}, json=body).status_code == 409
    assert len(table(client)['rows']) == 4
    changed = data['rows'][0]
    response = client.patch(f'/api/v1/tables/{table_id}/rows/{changed["id"]}', json={
        'expected_version': changed['version'], 'changes': {'name': '修改后'}})
    assert response.status_code == 200, response.text
    assert client.post(url, json=body).status_code == 409
    assert len(table(client)['rows']) == 4
    current = cards(client, table_id)
    session_id = client.post('/api/v1/tables/edit-sessions').json()['id']
    response = client.post(url, json={'sources': [{'id': card['id'], 'version': card['version']} for card in current], 'edit_session_id': session_id})
    assert response.status_code == 200, response.text
    assert response.json() == {'deleted': 4, 'sources': 2}
    assert table(client)['rows'] == []
    assert client.post(f'/api/v1/tables/edit-sessions/{session_id}/seal').status_code == 204
    with client.app.state.session_factory() as session:
        for task_id in ('A', 'B'):
            assert session.get(TaskRecord, task_id) is not None
            history = session.scalars(select(FileRevisionRecord).where(FileRevisionRecord.task_id == task_id).order_by(FileRevisionRecord.version)).all()
            from document_pipeline_api.services.file_revisions import revision_details
            changes = revision_details(session, task_id, history[-1].id)
            assert len(changes['items']) == 2
            assert all(item['after'] is None for item in changes['items'])
    assert get(client, '/tasks/A/result')['result']['items'] == []
    assert client.get('/api/v1/tasks/A/file').status_code == 200


def test_foreign_and_deleted_source_selection_never_falls_back_to_whole_table(client):
    table_id = table(client)['id']
    foreign = json.dumps({'id': 'missing', 'kind': 'file'})
    response = client.get(f'/api/v1/tables/{table_id}/source-cards', params={'source_keys': json.dumps([foreign])})
    assert response.status_code == 409, response.text
    with client.app.state.session_factory() as session:
        with pytest.raises(HTTPException):
            check_context(session, Context(table_id=table_id, source_keys=[foreign]))
