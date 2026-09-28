import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import ExtractionRecord, TaskRecord
from document_pipeline_api.model_providers import OllamaProvider, OpenAICompatibleProvider
from document_pipeline_api.services.autonomous_extraction import AUTONOMOUS_TEMPLATE_ID, AutonomousExtraction
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.services.template_processing import match_template
from document_pipeline_api.services.templates import list_templates


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}",
                                       storage_dir=tmp_path / 'uploads', queue_enabled=False))) as value:
        yield value


def body(template):
    return {key: template[key] for key in ('name', 'description', 'extra_instructions', 'fields',
            'validation_rules', 'deterministic_rules', 'output_mapping', 'behavior')}


def payload(label='客户', value='甲', value_type='text'):
    return {
        'fields': [{'key': 'f1', 'label': label, 'value_type': value_type, 'section': 'header'},
                   {'key': 'f2', 'label': '数量', 'value_type': 'number', 'section': 'item'}],
        'header': {'f1': value}, 'items': [{'f2': index} for index in range(25)],
    }


class Model:
    model_name = 'fixture'

    def __init__(self, data=None, decision=None):
        self.data = data or payload()
        self.decision = decision or {'outcome': 'none', 'template_ids': []}
        self.prompts = []

    def complete_text(self, prompt, result_type):
        self.prompts.append(prompt)
        return result_type.model_validate(self.decision if result_type.__name__ == 'TemplateMatchDecision' else self.data)


def upload(client, name='样例.txt', template=AUTONOMOUS_TEMPLATE_ID, *, content=None):
    response = client.post('/api/v1/tasks', files={'file': (name, name.encode() if content is None else content, 'text/plain')},
        data={'template_id': template} if template else {'template_mode': 'smart'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def process(client, task_id, model):
    with client.app.state.session_factory() as session:
        return process_task(session, client.app.state.settings, task_id, client=model)


def test_special_template_allows_advanced_settings_but_not_fixed_fields(client):
    url = '/api/v1/templates/' + AUTONOMOUS_TEMPLATE_ID
    current = client.get(url).json()
    assert current['is_system'] and current['engine_kind'] == 'autonomous'
    assert current['fields'] == [] and not current['in_smart_pool']
    response = client.put(url, json={**body(current), 'expected_version': 1,
        'extra_instructions': '关注交付日期', 'behavior': {**current['behavior'], 'suggest_filename': True, 'filename_mode': 'fixed'}})
    assert response.status_code == 200, response.text
    assert response.json()['engine_kind'] == 'autonomous'
    invalid = client.put(url, json={**body(response.json()), 'expected_version': 2,
        'fields': [{'key': 'x', 'label': '预设字段'}]})
    assert invalid.status_code == 422
    assert client.get(url).json()['version'] == 2
    assert client.post(url + '/copy').status_code == 409
    assert client.post('/api/v1/tables', json={'name': '错误平面表', 'template_key': AUTONOMOUS_TEMPLATE_ID}).status_code == 409
    restored = client.post(url + '/versions/1/restore', json={'expected_version': 2})
    assert restored.status_code == 200, restored.text
    assert restored.json()['extra_instructions'] == ''


def test_heterogeneous_files_share_one_collection_not_one_schema(client):
    first = upload(client)
    second = upload(client, '温度.txt')
    process(client, first, Model())
    process(client, second, Model(payload('温度', 18, 'number')))
    tables = client.get('/api/v1/tables').json()
    assert len(tables) == 1 and tables[0]['collection_kind'] == 'files'
    table_id = tables[0]['id']
    sources = client.get(f'/api/v1/tables/{table_id}/source-cards').json()['groups']
    assert len(sources) == 2 and all(source['item_count'] == 25 for source in sources)
    source = next(card for card in sources if card['task_id'] == second)
    details = client.get(f'/api/v1/tables/{table_id}/source-details', params={'source_key': source['id']}).json()
    assert details['header']['f1'] == 18 and len(details['rows']) == 20
    assert next(column for column in details['schema'] if column['key'] == 'f1')['label'] == '温度'
    exported = client.get(f'/api/v1/tables/{table_id}/export.json', params={'source_keys': json.dumps([source['id']])})
    assert exported.status_code == 200 and exported.headers['X-Zhiyi-File-Count'] == '1'
    detail = client.get(f'/api/v1/tables/{table_id}').json()
    assert detail['columns'] == [] and detail['rows'] == []
    page = client.get(f'/api/v1/tables/{table_id}/files?page_size=1').json()
    assert page['total'] == 2 and len(page['files']) == 1
    card = page['files'][0]
    assert card['item_count'] == 25 and len(card['items']) == 3
    records = client.get(f'/api/v1/tables/{table_id}/files').json()['files']
    records = {record['task_id']: record for record in records}
    assert records[first]['header'] == {'f1': '甲'}
    assert records[second]['header'] == {'f1': 18}
    assert records[first]['schema'][0]['label'] == '客户'
    assert records[second]['schema'][0]['label'] == '温度'
    items = client.get(f'/api/v1/tasks/{first}/current-details?page=2&page_size=10').json()
    assert items['total'] == 25 and len(items['items']) == 10
    assert items['items'][0]['values']['f2'] == 10
    extraction = client.get(f'/api/v1/tasks/{first}/result').json()
    extraction['result']['header']['f1'] = '修订客户'
    response = client.put(f'/api/v1/tasks/{first}/review', json={
        'expected_version': extraction['review_version'], 'result': extraction['result'], 'detail_ids': extraction['detail_ids'],
    })
    assert response.status_code == 200, response.text
    updated = client.get(f'/api/v1/tables/{table_id}/files').json()['files']
    assert next(record for record in updated if record['task_id'] == first)['header']['f1'] == '修订客户'
    with client.app.state.session_factory() as session:
        original = session.get(ExtractionRecord, first)
        assert json.loads(original.result_json)['header']['f1'] == '甲'
        assert json.loads(original.output_schema_json)[0]['label'] == '客户'


def test_smart_pool_is_frozen_and_autonomous_is_only_opted_in_fallback(client):
    before = upload(client, '无兜底.txt', template=None)
    assert client.put(f'/api/v1/templates/{AUTONOMOUS_TEMPLATE_ID}/smart-pool', json={'in_smart_pool': True}).status_code == 200
    process(client, before, Model())
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, before).status == 'waiting_for_template'
        assert AUTONOMOUS_TEMPLATE_ID not in session.get(TaskRecord, before).template_pool_json
    after = upload(client, '有兜底.txt', template=None)
    client.put(f'/api/v1/templates/{AUTONOMOUS_TEMPLATE_ID}/smart-pool', json={'in_smart_pool': False})
    result = process(client, after, Model())
    assert result is not None and result.template_id == AUTONOMOUS_TEMPLATE_ID


@pytest.mark.parametrize('decision', [
    {'outcome': 'matched', 'template_ids': [AUTONOMOUS_TEMPLATE_ID]},
    {'outcome': 'none', 'template_ids': ['builtin-invoice']},
    {'outcome': 'matched', 'template_ids': []},
])
def test_invalid_matching_response_never_becomes_fallback(client, decision):
    with client.app.state.session_factory() as session:
        templates = list_templates(session)
        with pytest.raises(HTTPException) as error:
            match_template(None, templates, Model(decision=decision))
        assert error.value.status_code == 502


def test_autonomous_result_cannot_silently_drop_unknown_or_conflicting_fields():
    for mutate in (
        lambda data: data['fields'].append(deepcopy(data['fields'][0])),
        lambda data: data['header'].update({'unknown': '隐藏值'}),
        lambda data: data['items'][0].update({'f2': False}),
    ):
        data = payload()
        mutate(data)
        with pytest.raises(ValidationError):
            AutonomousExtraction.model_validate(data)


@pytest.mark.parametrize('literal,expected', [
    ('21.50', 21.5), ('0', 0.0), ('-0', -0.0), ('-3.5', -3.5),
    ('1e2', 100.0), ('1E-2', 0.01), ('1e+2', 100.0),
    ('9007199254740991', 9007199254740991.0), ('5e-324', 5e-324),
])
def test_autonomous_declared_numbers_accept_only_json_number_strings(literal, expected):
    data = payload('合计', literal, 'number')
    data['items'] = [{'f2': literal}]
    before = deepcopy(data)
    result = AutonomousExtraction.model_validate(data)
    assert type(result.header['f1']) is float and result.header['f1'] == expected
    assert type(result.items[0]['f2']) is float and result.items[0]['f2'] == expected
    assert data == before  # Parsing must not rewrite the captured model response.


@pytest.mark.parametrize('value', [
    '21.50 CNY', '1,000', '21,50', ' 21.50', '21.50 ', '+21', '01', '.5', '1.',
    '１２', 'NaN', 'Infinity', '-Infinity', 'true', 'null', '1_000', '0x10',
    '1e309', '1e-999', '9007199254740992', '9007199254740993',
    '9.007199254740993e15', '123456789012345678901234567890', False, True,
])
@pytest.mark.parametrize('section', ['header', 'item'])
def test_autonomous_number_adaptation_rejects_ambiguous_or_lossy_values(value, section):
    data = payload('合计', value if section == 'header' else 1, 'number')
    if section == 'item':
        data['items'] = [{'f2': value}]
    with pytest.raises(ValidationError):
        AutonomousExtraction.model_validate(data)


@pytest.mark.parametrize('value', ['00123', '21.50', '9007199254740993', '1e2'])
def test_autonomous_numeric_text_identifiers_are_never_coerced(value):
    result = AutonomousExtraction.model_validate(payload('编号', value))
    assert result.header['f1'] == value and type(result.header['f1']) is str


@pytest.mark.parametrize('provider_class', [OpenAICompatibleProvider, OllamaProvider])
def test_captured_autonomous_response_keeps_original_and_current_numbers_consistent(client, provider_class):
    # Real model response to the synthetic R3 document; no customer data or credentials.
    captured = (Path(__file__).parent / 'fixtures' / 'autonomous_numeric_string_response.json').read_text(encoding='utf-8')
    data = json.loads(captured)
    expected = {'header': {**data['header'], 'f6': 21.5}, 'items': data['items']}
    calls = []

    def respond(request):
        calls.append(request)
        assert 'number 字段使用不带引号的 JSON 数字' in json.loads(request.content)['messages'][-1]['content']
        envelope = ({'choices': [{'message': {'content': captured}, 'finish_reason': 'stop'}]}
                    if provider_class is OpenAICompatibleProvider else {'message': {'content': captured}})
        return httpx.Response(200, json=envelope)

    task_id = upload(client, 'R3-synthetic.txt')
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        model = provider_class('http://synthetic.test/v1', 'synthetic-model', client=transport)
        assert process(client, task_id, model) is not None
    assert len(calls) == 1
    extraction = client.get(f'/api/v1/tasks/{task_id}/result').json()
    assert extraction['original_result'] == extraction['result'] == expected
    assert len(extraction['current_schema']) == 12
    with client.app.state.session_factory() as session:
        record = session.get(ExtractionRecord, task_id)
        assert json.loads(record.result_json) == expected
    current = client.get(f'/api/v1/tasks/{task_id}/current-details').json()
    assert type(current['header']['f6']) is float and current['header']['f6'] == 21.5
    table_id = client.get('/api/v1/tables').json()[0]['id']
    exported = client.get(f'/api/v1/tables/{table_id}/export.json').json()['files'][0]
    assert exported['header'] == expected['header']
    assert [item['values'] for item in exported['items']] == expected['items']

    # This repair belongs only to model output, never to API/current fact edits.
    invalid_patch = client.patch(f'/api/v1/tasks/{task_id}/current', json={
        'expected_version': current['version'], 'header_changes': {'f6': '21.50'},
    })
    assert invalid_patch.status_code == 422
    invalid_review = client.put(f'/api/v1/tasks/{task_id}/review', json={
        'expected_version': extraction['review_version'],
        'detail_ids': extraction['detail_ids'],
        'result': {**expected, 'header': data['header']},
    })
    assert invalid_review.status_code == 422
    assert client.get(f'/api/v1/tasks/{task_id}/result').json()['result'] == expected


def test_current_patch_uses_one_file_revision_and_never_restores_deleted_data(client):
    task = upload(client)
    process(client, task, Model())
    before = client.get(f'/api/v1/tasks/{task}/current-details').json()
    editing = client.post('/api/v1/tables/edit-sessions').json()['id']
    url = f'/api/v1/tasks/{task}/current'
    edited = client.patch(url, json={'expected_version': before['version'], 'edit_session_id': editing,
        'header_changes': {'f1': '新的客户'}, 'detail_changes': {str(before['items'][0]['id']): {'f2': 100}}})
    assert edited.status_code == 200, edited.text
    assert client.patch(url, json={'expected_version': before['version'], 'header_changes': {'f1': '过期'}}).status_code == 409
    deleted = client.patch(url, json={'expected_version': edited.json()['version'], 'edit_session_id': editing,
        'deleted_ids': [before['items'][1]['id']]})
    assert deleted.status_code == 200, deleted.text
    versions = client.get(f'/api/v1/tasks/{task}/revisions').json()['revisions']
    assert len(versions) == 2 and versions[0]['modified_fields'] == 2 and versions[0]['deleted_details'] == 1
    history = client.get(f"/api/v1/tasks/{task}/revisions/{versions[0]['id']}").json()
    removed = next(item for item in history['items'] if item['after'] is None)
    assert removed['before']['values'] == {'f2': 1}
    cleared = client.patch(url, json={'expected_version': deleted.json()['version'], 'clear': True})
    assert cleared.status_code == 200 and not cleared.json()['has_data']
    assert client.get(f'/api/v1/tasks/{task}/current-details').json()['total'] == 0
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, task) is not None
        assert session.get(ExtractionRecord, task) is not None


def test_per_file_exports_keep_distinct_schemas_and_all_details(client, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile
    from openpyxl import load_workbook
    from document_pipeline_api.services import file_exports
    monkeypatch.setattr(file_exports, 'FILES_PER_WORKBOOK', 1)
    first = upload(client, '相同名字.txt')
    # Same filename, different originals: export must keep both schemas and
    # disambiguate their names, while ordinary SHA duplicate protection stays on.
    second = upload(client, '相同名字.txt', content='温度记录'.encode())
    process(client, first, Model(payload('客户', '=DANGEROUS()')))
    process(client, second, Model(payload('温度', 18, 'number')))
    table = client.get('/api/v1/tables').json()[0]
    assert table['file_count'] == 2
    url = '/api/v1/tables/' + table['id']
    assert client.get(url + '/export.xlsx').status_code == 409
    packed = client.get(url + '/export.zip')
    assert packed.status_code == 200, packed.text if packed.status_code != 200 else ''
    with ZipFile(BytesIO(packed.content)) as archive:
        manifest = json.loads(archive.read('目录.json'))
        assert manifest['total_files'] == 2 and len(manifest['parts']) == 2
        for part in manifest['parts']:
            workbook = load_workbook(BytesIO(archive.read(part['workbook'])))
            assert len(workbook.worksheets) == 2
            assert workbook.worksheets[0]['C2'].value == 25
            sheet = workbook.worksheets[1]
            values = [cell for row in sheet.iter_rows() for cell in row]
            assert all(cell.data_type != 'f' for cell in values)
            if part['files'][0]['id'] == first:
                assert any(cell.value == '=DANGEROUS()' for cell in values)
                assert any(cell.value == '客户' for cell in values)
                assert not any(cell.value == '温度' for cell in values)
            workbook.close()
    exported = client.get(url + '/export.json').json()
    assert exported['format'] == 'zhiyi-file-data-v1'
    by_id = {file['file_id']: file for file in exported['files']}
    assert by_id[first]['header']['f1'] == '=DANGEROUS()'
    assert by_id[second]['header']['f1'] == 18
    assert all(len(file['items']) == 25 for file in by_id.values())
    assert client.get(url + '/export.csv').status_code == 409
    monkeypatch.setattr(file_exports, 'FILES_PER_WORKBOOK', 100)
    excel = client.get(url + '/export.xlsx')
    assert excel.status_code == 200
    workbook = load_workbook(BytesIO(excel.content))
    assert len(workbook.sheetnames) == 3 and len(set(workbook.sheetnames)) == 3
    workbook.close()
