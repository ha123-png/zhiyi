import json

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from test_autonomous_extraction import client as file_client, upload, process, Model, payload
from document_pipeline_api.models import DataRowRecord
from document_pipeline_api.services.assistant_tools import Context, execute_tool, scoped_tool_specs
from document_pipeline_api.services.assistant_memory import model_result
from document_pipeline_api.services.handbook import handbook

client = file_client


def test_file_history_survives_live_tool_event_and_followup_model_call(client):
    from test_assistant import ScriptedProvider, select_profile, send, finish, final_answer
    task = upload(client, '流式历史.txt')
    process(client, task, Model())
    ScriptedProvider.requests = []
    ScriptedProvider.plan = [
        [{'type': 'tool', 'id': 'directory', 'name': 'read_resource',
          'arguments': {'kind': 'file_revisions', 'id': task}}],
        [{'type': 'tool', 'id': 'details', 'name': 'read_resource',
          'arguments': {'kind': 'file_revisions', 'id': task, 'version': 1}}],
        [final_answer('已经读取初次提取的文件版本。')],
    ]
    client.app.state.conversation_factory = ScriptedProvider
    thread = finish(client, send(client, select_profile(client), text='读取此文件修订历史', context={'task_id': task}))
    assert thread['runs'][0]['status'] == 'completed', thread['runs']
    assert len(ScriptedProvider.requests) == 3
    tools = thread['tools']
    assert len(tools) == 2 and all(tool['status'] == 'completed' for tool in tools)
    directory = next(tool['result'] for tool in tools if tool['result']['kind'] == 'file_revisions')
    assert 'T' in directory['revisions'][0]['created_at']
    messages = ScriptedProvider.requests[-1]
    assert json.loads(next(message['content'] for message in messages if message.get('tool_call_id') == 'details'))['version'] == 1


def test_help_and_model_read_the_same_topics_without_loading_all_prose(client):
    context = Context(mode="read")
    with client.app.state.session_factory() as session:
        index = execute_tool(session, context, "read_handbook", {}, {})
        assert index == {"kind": "handbook", **client.get('/api/v1/guide').json()}
        assert len(index['topics']) == 12
        assert all('sections' not in topic for topic in index['topics'])
        for topic in index['topics']:
            api = client.get('/api/v1/guide/' + topic['id']).json()
            tool = execute_tool(session, context, "read_handbook", {'topic': topic['id']}, {})
            assert tool == {'kind': 'handbook', **api}
            assert len(json.dumps(model_result(tool, 'test'), ensure_ascii=False)) < 6000
            if topic['id'] == 'api':
                text = ' '.join(paragraph for section in tool['sections'] for paragraph in section['paragraphs'])
                assert '下载原件并读取修订历史' in text
                assert '旧密钥保留此前的访问权限' in text
        advertised = scoped_tool_specs(session, context, {}, False, '知意的数据怎么流动')
        assert 'read_handbook' in [tool['function']['name'] for tool in advertised]
        assert 'deleted_before' not in json.dumps(advertised)  # no whole manual in tools
        with pytest.raises(HTTPException) as error:
            execute_tool(session, context, "read_handbook", {'topic': '../../settings'}, {})
        assert error.value.status_code == 404
        assert client.get('/api/v1/guide/missing').status_code == 404
    first = handbook('data')
    first['sections'].clear()
    assert handbook('data')['sections']


def test_thousand_detail_file_reads_and_history_stay_bounded_and_scoped(client):
    first, second = upload(client, '一千条.txt'), upload(client, '另一个文件.txt')
    data = payload()
    data['items'] = [{'f2': index} for index in range(1000)]
    data['items'][789]['f2'] = 987654321
    process(client, first, Model(data))
    process(client, second, Model())
    table = client.get('/api/v1/tables').json()[0]['id']
    current = client.get(f'/api/v1/tasks/{first}/current-details?page_size=1').json()
    row_id = current['items'][0]['id']
    selected = Context(table_id=table, row_ids=[row_id], mode='read')
    with client.app.state.session_factory() as session:
        loaded = []
        def track(session, instance):
            if isinstance(instance, DataRowRecord):
                loaded.append(instance.id)
        event.listen(session, 'loaded_as_persistent', track)
        catalog = execute_tool(session, selected, 'catalog', {'kind': 'tasks'}, {})
        assert [item['id'] for item in catalog['items']] == [first]
        read = execute_tool(session, selected, 'read_resource', {'kind': 'task', 'id': first}, {})
        assert read['extraction']['item_count'] == 1
        assert read['extraction']['detail_ids'] == [row_id]
        assert len(set(loaded)) == 1
        for kind in ('file_revisions',):
            with pytest.raises(HTTPException) as error:
                execute_tool(session, selected, 'read_resource', {'kind': kind, 'id': first}, {})
            assert error.value.status_code == 403
        with pytest.raises(HTTPException) as error:
            execute_tool(session, selected, 'read_original_page', {'task_id': first}, {}, client.app.state.settings)
        assert error.value.status_code == 403
        scoped = execute_tool(session, selected, 'read_resource', {'kind': 'file_data', 'id': first, 'page': 2}, {})
        assert scoped['total'] == 1 and scoped['items'] == []
        searched = execute_tool(session, Context(table_id=table, search='987654321'), 'read_resource', {'kind': 'file_data', 'id': first}, {})
        assert searched['total'] == 1 and searched['items'][0]['values']['i1'] == 987654321
        context = Context(table_id=table)
        loaded.clear()
        read = execute_tool(session, context, 'read_resource', {'kind': 'task', 'id': first}, {})
        assert read['extraction']['item_count'] == 1000
        assert len(read['extraction']['result']['items']) == 3
        assert len(set(loaded)) == 3
        assert 'original_result' not in read['extraction']
        next_page = execute_tool(session, context, 'read_resource', {'kind': 'file_data', 'id': first, 'page': 2}, {})
        assert next_page['items'][0]['values']['i1'] == 3
        assert len(next_page['items']) == 3
        for kind in ('task', 'file_data', 'file_revisions'):
            with pytest.raises(HTTPException) as error:
                execute_tool(session, selected, 'read_resource', {'kind': kind, 'id': second}, {})
            assert error.value.status_code == 403
        assert execute_tool(session, Context(table_id=table, row_ids=[]), 'catalog', {'kind': 'tasks'}, {})['items'] == []
        tools = [item['function']['name'] for item in scoped_tool_specs(session, context, {}, False, '分析文件')]
        assert 'read_resource' in tools and 'analyze_data_table' not in tools

    editing = client.post('/api/v1/tables/edit-sessions').json()['id']
    changed = client.patch(f'/api/v1/tasks/{first}/current', json={
        'expected_version': current['version'], 'edit_session_id': editing,
        'header_changes': {'h1': '新客户'}, 'detail_changes': {str(row_id): {'i1': 42}},
    })
    assert changed.status_code == 200, changed.text
    removed = client.patch(f'/api/v1/tasks/{first}/current', json={
        'expected_version': changed.json()['version'], 'deleted_ids': [row_id], 'edit_session_id': editing,
    })
    assert removed.status_code == 200
    assert client.post(f'/api/v1/tables/edit-sessions/{editing}/seal').status_code == 204
    with client.app.state.session_factory() as session:
        context = Context(table_id=table)
        history = execute_tool(session, context, 'read_resource', {'kind': 'file_revisions', 'id': first}, {})
        assert history['total'] == 2
        detail = execute_tool(session, context, 'read_resource', {'kind': 'file_revisions', 'id': first, 'version': 2}, {})
        assert detail['header']['h1'] == {'before': '甲', 'after': '新客户'}
        assert detail['items'][0]['deleted_before']['values'] == {'i1': 42}
        assert detail['items'][0]['before']['values'] == {'i1': 0}
        assert detail['items'][0]['after'] is None
        initial = execute_tool(session, context, 'read_resource', {'kind': 'file_revisions', 'id': first, 'version': 1}, {})
        assert initial['total_details'] == 1000 and len(initial['items']) == 3

    version = removed.json()['version']
    assert client.patch(f'/api/v1/tasks/{first}/current', json={'expected_version': version, 'clear': True}).status_code == 200
    with client.app.state.session_factory() as session:
        history = execute_tool(session, Context(table_id=table), 'read_resource', {'kind': 'file_revisions', 'id': first}, {})
        assert history['total'] == 3  # whole-table scope retains cleared source history
        with pytest.raises(HTTPException):
            execute_tool(session, Context(table_id=table, row_ids=[row_id]), 'read_resource', {'kind': 'file_revisions', 'id': first}, {})
