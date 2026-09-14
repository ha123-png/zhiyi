"""Cross-entry product contracts found by the v0.4 acceptance audit."""
from datetime import datetime, timedelta
from io import BytesIO
import csv
import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from document_pipeline_api.business_backup import create_business_backup, inspect_business_backup
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import DataRowRecord, DataTableRecord, TaskRecord
from document_pipeline_api.services.assistant_analysis import AnalysisRequest, Filter, Metric, analyze
from document_pipeline_api.services.assistant_tools import Context, execute_tool
from document_pipeline_api.services.file_names import automatic_file_name
from document_pipeline_api.services.integration_config import write_integration_config
from document_pipeline_api.services.integration_queries import aggregate_data_table, export_data_table_file
from document_pipeline_api.services.model_profiles import create_model_profile
from document_pipeline_api.services.model_runtime import settings_for_active_profile_metadata
from document_pipeline_api.schemas.model_profiles import ModelProfileCreate


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f'sqlite:///{tmp_path / "document-pipeline.db"}', storage_dir=tmp_path / 'uploads', queue_enabled=False)
    with TestClient(create_app(settings), raise_server_exceptions=False) as result:
        yield result


def seed(session, identifier, values, columns=None):
    columns = columns or [
        {'key': 'amount', 'label': '金额', 'value_type': 'number', 'section': 'header'},
        {'key': 'category', 'label': '类别', 'value_type': 'text', 'section': 'header'},
        {'key': 'date', 'label': '日期', 'value_type': 'date', 'section': 'header'},
        {'key': 'currency', 'label': '币种', 'value_type': 'text', 'section': 'header'},
    ]
    session.add(DataTableRecord(id=identifier, name=identifier, template_key=identifier, template_version='1', document_kind='custom', columns_json=json.dumps(columns, ensure_ascii=False)))
    session.flush()
    session.bulk_insert_mappings(DataRowRecord, [{'table_id': identifier, 'item_index': i, 'row_json': json.dumps(value, ensure_ascii=False), 'review_pending': True} for i, value in enumerate(values)])
    session.commit()


@pytest.mark.parametrize('kind', ['read', 'write'])
def test_revoke_after_startup_never_revives_environment_copy(tmp_path, kind):
    token = 'audit-disposable-token-' + 'x' * 32
    write_integration_config(tmp_path, {kind + '_token': token})
    settings = Settings(database_url=f'sqlite:///{tmp_path / "document-pipeline.db"}', storage_dir=tmp_path / 'uploads', queue_enabled=False,
                        **{'integration_' + kind + '_token': token})
    with TestClient(create_app(settings)) as client:
        headers = {'Authorization': 'Bearer ' + token}
        assert client.get('/api/integration/v1/tables', headers=headers).status_code == 200
        assert client.post('/api/v1/integration/settings/keys/revoke', json={'kind': kind}).status_code == 200
        assert client.get('/api/integration/v1/tables', headers=headers).status_code in (401, 503)
        (tmp_path / 'config/integration.json').unlink()
        assert client.get('/api/integration/v1/tables', headers=headers).status_code in (401, 503)


def test_external_aggregation_shares_business_grain_totals_and_currency(client):
    with client.app.state.session_factory() as session:
        seed(session, 'shared', [{'amount': 100, 'currency': 'CNY', '__row_group': 'one'}] * 2)
        seed(session, 'groups', [{'amount': 1, 'currency': 'CNY', 'category': str(i)} for i in range(101)])
        seed(session, 'mixed', [{'amount': 100, 'currency': 'CNY'}, {'amount': 10, 'currency': 'USD'}])
        result = aggregate_data_table(session, 'shared', value_field='amount')
        assert result['sum'] == 100
        assert result['count'] == 2
        result = aggregate_data_table(session, 'groups', value_field='amount', group_by='category')
        assert result['sum'] == result['count'] == result['group_count'] == 101
        assert len(result['groups']) == 100 and result['truncated']
        with pytest.raises(HTTPException, match='币种'):
            aggregate_data_table(session, 'mixed', value_field='amount')


def test_spreadsheet_headers_are_text_and_legal_business_names_export(client, tmp_path):
    with client.app.state.session_factory() as session:
        seed(session, 'export', [{'a': '=1+1'}], [{'key': 'a', 'label': '=1+1', 'value_type': 'text', 'section': 'header'}])
        table = session.get(DataTableRecord, 'export')
        table.name = '采购/2026'
        session.commit()
        export_data_table_file(session, 'export', tmp_path / 'mcp.csv', 'csv')
    response = client.get('/api/v1/tables/export/export.xlsx')
    assert response.status_code == 200
    book = load_workbook(BytesIO(response.content))
    assert book.active.title == '采购_2026'
    assert book.active['A1'].value == '=1+1' and book.active['A1'].data_type == 's'
    headers = next(csv.reader((tmp_path / 'mcp.csv').read_text(encoding='utf-8-sig').splitlines()))
    assert headers[0] == "'=1+1"
    assert '知意来源状态（非业务字段）' in headers


def test_cloud_budget_auto_and_explicit_local_limits_are_independent(client):
    with client.app.state.session_factory() as session:
        common = dict(name='cloud', provider='openai_compatible', base_url='https://dashscope.aliyuncs.com/compatible-mode/v1', model_name='qwen3.6-flash')
        automatic = create_model_profile(session, ModelProfileCreate(**common), None)
        fixed = create_model_profile(session, ModelProfileCreate(**{**common, 'name': 'fixed'}, context_length=8192), None)
        assert automatic.context_length == 1_000_000 and automatic.context_policy == 'auto'
        assert fixed.context_length == 8192 and fixed.context_policy == 'fixed'
        local = settings_for_active_profile_metadata(session, client.app.state.settings)
        assert local.model_context_length == 8192


def test_backup_handles_many_small_originals(client, tmp_path):
    settings = client.app.state.settings
    content = b'only synthetic audit data'
    with client.app.state.session_factory() as session:
        for index in range(5000):
            identifier = str(uuid4())
            path = settings.storage_dir / f'{identifier}.txt'
            path.write_bytes(content)
            session.add(TaskRecord(id=identifier, filename=f'{index}.txt', content_type='text/plain', size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), storage_path=path.name, status='completed'))
        session.commit()
    output = create_business_backup(settings, tmp_path / 'many.dpbak')
    assert len(inspect_business_backup(output)['files']) == 5000
    from document_pipeline_api.business_backup import restore_business_backup
    restored_dir = tmp_path / 'restored'
    restored_dir.mkdir()
    restored = Settings(database_url=f'sqlite:///{restored_dir / "document-pipeline.db"}', storage_dir=restored_dir / 'uploads')
    restore_business_backup(restored, output, restored_dir)
    assert len(list(restored.storage_dir.rglob('*.txt'))) == 5000


def test_naming_uses_same_extraction_and_preserves_original_identity():
    original = 'v2-062299d754ec02ffd96640802431f996_r.jpg'
    state = automatic_file_name(original, {'rename': False, 'name': None}, {'header': {'party': '星河商贸', 'date': '2026-09-14'}})
    assert state.status == 'confirmed'
    assert state.confirmed_filename == '星河商贸.jpg'
    assert state.decisions[0].before == original
    meaningful = automatic_file_name('采购单.jpg', {'rename': False, 'name': None}, {'title': '其他标题'})
    assert meaningful.confirmed_filename == '采购单.jpg'


def test_catalog_finds_old_authorized_file_and_pages_without_loss(client):
    with client.app.state.session_factory() as session:
        old_id = None
        for index in range(501):
            identifier = str(uuid4())
            if index == 0:
                old_id = identifier
            session.add(TaskRecord(id=identifier, filename=f'audit-{index}.txt', content_type='text/plain', size_bytes=1, sha256='0' * 64,
                storage_path=f'{identifier}.txt', status='completed', created_at=datetime.now() + timedelta(seconds=index)))
        session.commit()
        found = execute_tool(session, Context(workspace=True), 'catalog', {'kind': 'tasks', 'search': 'audit-0.txt'}, {})
        assert [item['id'] for item in found['items']] == [old_id]
        selected = execute_tool(session, Context(task_id=old_id), 'catalog', {'kind': 'tasks'}, {})
        assert len(selected['items']) == 1
        first = execute_tool(session, Context(workspace=True), 'catalog', {'kind': 'tasks'}, {})
        second = execute_tool(session, Context(workspace=True), 'catalog', {'kind': 'tasks', 'offset': first['next_offset']}, {})
        assert not ({v['id'] for v in first['items']} & {v['id'] for v in second['items']})


def test_small_date_slice_in_large_table_is_not_rejected(client):
    with client.app.state.session_factory() as session:
        seed(session, 'large', [{'amount': 1, 'currency': 'CNY', 'date': '2020-01-01'}] * 100001 + [{'amount': 8, 'currency': 'CNY', 'date': '2026-09-14'}])
        result = analyze(session, AnalysisRequest(table_id='large', filters=[Filter(field='date', op='gte', value='2026-09-01')], metrics=[Metric(op='sum', field='amount')]))
        assert result['totals']['sum:amount'] == 8 and result['source']['row_count'] == 1


def test_single_group_bar_and_mixed_axis_return_actual_shape(client):
    with client.app.state.session_factory() as session:
        seed(session, 'chart', [{'amount': 10, 'category': '同名', 'currency': 'CNY'}, {'amount': 20, 'category': '同名', 'currency': 'CNY'}])
        artifacts = {}
        result = execute_tool(session, Context(workspace=True), 'analyze_data_table', {'table_id': 'chart', 'dimensions': ['category'], 'metrics': [{'op': 'sum', 'field': 'amount'}, {'op': 'count'}]}, artifacts)
        body = {'analysis_id': result['analysis_id'], 'title': '业务统计', 'type': 'bar', 'series': ['sum:amount']}
        chart = execute_tool(session, Context(workspace=True), 'render_chart', body, artifacts)
        assert chart['chart']['type'] == chart['actual_chart_type'] == 'bar'
        mixed = execute_tool(session, Context(workspace=True), 'render_chart', {**body, 'series': ['sum:amount', 'count:*']}, artifacts)
        assert mixed['actual_chart_type'] == 'composed'
        assert mixed['notice'] in mixed['analysis']['warnings']


def test_card_save_checks_same_all_time_scope_as_preview(client):
    with client.app.state.session_factory() as session:
        seed(session, 'range', [{'amount': 10, 'category': '甲', 'date': '2020-01-01', 'currency': 'CNY'},
                                {'amount': 20, 'category': '乙', 'date': '2020-01-01', 'currency': 'USD'}])
    body = {'name': '范围测试', 'table_id': 'range', 'metric': 'sum', 'metric_field': 'amount', 'date_field': 'date', 'time_range': 'dashboard'}
    # An empty recent range is valid, but all-time must reject mixed currencies.
    assert client.post('/api/v1/stats/cards/preview?days=7', json=body).json()['status'] == 'empty'
    assert client.post('/api/v1/stats/cards/preview?days=all', json=body).json()['status'] == 'invalid'
    assert client.post('/api/v1/stats/cards?days=all', json=body).status_code == 422


def test_new_and_copied_templates_do_not_compete_with_builtins(client):
    fresh = client.post('/api/v1/templates', json={'name': '新模板', 'fields': [{'key': 'a', 'label': '字段', 'section': 'header'}]}).json()
    copied = client.post('/api/v1/templates/builtin-invoice/copy').json()
    assert fresh['in_smart_pool'] is False and copied['in_smart_pool'] is False
    builtins = client.get('/api/v1/templates').json()
    assert next(t for t in builtins if t['id'] == 'builtin-invoice')['in_smart_pool'] is True
    assert client.put(f"/api/v1/templates/{copied['id']}/smart-pool", json={'in_smart_pool': True}).json()['in_smart_pool'] is True


def test_long_thread_incremental_read_and_event_resume(client):
    from document_pipeline_api.models.assistant import AssistantThread, AssistantMessage, AssistantRun, AssistantToolCall
    with client.app.state.session_factory() as session:
        session.add(AssistantThread(id='long', title='合成历史'))
        session.flush()
        for index in range(200):
            session.add(AssistantMessage(id=f'm-{index}', thread_id='long', role='assistant', position=index,
                                        parts_json=json.dumps([{'type': 'text', 'text': '合成业务内容' * 500}])))
        session.flush()
        session.add(AssistantRun(id='latest', thread_id='long', message_id='m-199', profile_id='synthetic', profile_version=1,
                                 model='fixture', provider='fixture', status='completed', events_json='[{"type":"text"},{"type":"done"}]'))
        session.flush()
        session.add(AssistantToolCall(id='bad', run_id='latest', name='read_tool_result', arguments_json='{}', status='failed',
                                      result_json=json.dumps({'error': '1 validation error for ToolDetail input_value=SECRET'})))
        session.get(AssistantMessage, 'm-199').parts_json = json.dumps([{'type': 'tool', 'id': 'bad', 'name': 'read_tool_result',
                                                                       'result': {'error': '1 validation error for ToolDetail input_value=SECRET'}, 'status': 'failed'}])
        session.commit()
    whole = client.get('/api/v1/assistant/threads/long')
    delta = client.get('/api/v1/assistant/threads/long?run_id=latest')
    assert len(delta.json()['messages']) == 1 and delta.json()['partial']
    assert len(delta.content) < len(whole.content) / 100
    assert 'SECRET' not in delta.text and 'validation error' not in delta.text
    assert client.get('/api/v1/assistant/threads/long?run_id=missing').status_code == 404
    events = client.get('/api/v1/assistant/runs/latest/events', headers={'Last-Event-ID': '1'}).text
    assert 'id: 2' in events and 'id: 1' not in events


def test_large_extraction_exposes_business_fields_and_real_read_paths():
    from document_pipeline_api.services.assistant_memory import summarize_result
    source = {'extraction': {'result': {'header': {'seller': '合成商家'}, 'items': [{'amount': i} for i in range(100)]},
                            'template': {'instructions': 'x' * 10000}, 'evidence': [], 'validation_issues': []}}
    brief = summarize_result(source)
    assert brief['result_preview']['header']['seller'] == '合成商家'
    assert len(brief['result_preview']['items']) == 3 and brief['item_count'] == 100
    for path in brief['read_paths']:
        value = source
        for key in path:
            value = value[key]


def test_existing_pending_names_are_adopted_once_without_changing_manual_names(client):
    from document_pipeline_api.models import ExtractionRecord
    from document_pipeline_api.schemas.file_name import FileNameRead
    from document_pipeline_api.services.file_names import adopt_pending_file_names
    with client.app.state.session_factory() as session:
        for identifier, state in [('pending', FileNameRead(suggested_filename='原始记录.txt', explanation='保留原文件名。')),
                                  ('manual', FileNameRead(status='confirmed', suggested_filename='建议.txt', confirmed_filename='手动名称.txt', explanation='手动修改。'))]:
            session.add(TaskRecord(id=identifier, filename='原始记录.txt', content_type='text/plain', size_bytes=1,
                                   sha256='0' * 64, storage_path=identifier + '.txt', status='needs_review', file_name_json=state.model_dump_json()))
        session.flush()
        for identifier in ('pending', 'manual'):
            session.add(ExtractionRecord(task_id=identifier, document_kind='custom', model_name='fixture', prompt_version='fixture', elapsed_seconds=0,
                                          result_json='{"header":{"title":"业务内容"},"items":[]}', validation_json='[]'))
        session.commit()
        assert adopt_pending_file_names(session) == 1
        assert session.get(TaskRecord, 'pending').file_name.status == 'confirmed'
        assert session.get(TaskRecord, 'manual').display_filename == '手动名称.txt'
        assert session.get(TaskRecord, 'pending').status == 'needs_review'
        assert adopt_pending_file_names(session) == 0


def test_original_read_uses_configured_upload_limit(client):
    from document_pipeline_api.services.system_settings import set_setting
    from document_pipeline_api.services.assistant_originals import read_original
    from document_pipeline_api.services.assistant_tools import OriginalPage
    settings = client.app.state.settings
    original = settings.storage_dir / 'configured.txt'
    original.write_text('合成正文' * 100, encoding='utf-8')
    with client.app.state.session_factory() as session:
        session.add(TaskRecord(id='configured', filename='configured.txt', content_type='text/plain', size_bytes=original.stat().st_size,
                              sha256=hashlib.sha256(original.read_bytes()).hexdigest(), storage_path=original.name, status='completed'))
        set_setting(session, 'upload_limit_mb', '1')
        session.commit()
        from dataclasses import replace
        result = read_original(session, replace(settings, max_upload_bytes=10), OriginalPage(task_id='configured'))
        assert result['text'].startswith('合成正文')
