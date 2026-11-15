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
from document_pipeline_api.model_providers.openai_compatible import parse_structured_content
from document_pipeline_api.services.autonomous_extraction import AUTONOMOUS_TEMPLATE_ID, AutonomousExtraction, AutonomousModelResult
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


def model_payload(data):
    header = [field for field in data['fields'] if field['section'] == 'header']
    items = [field for field in data['fields'] if field['section'] == 'item']
    return {
        'header': [{'label': field['label'], 'value_type': field['value_type'],
                    'value': data['header'].get(field['key'])} for field in header],
        'item_fields': [{'label': field['label'], 'value_type': field['value_type']} for field in items],
        'items': [[row.get(field['key']) for field in items] for row in data['items']],
    }


class Model:
    model_name = 'fixture'

    def __init__(self, data=None, decision=None):
        self.data = data or payload()
        self.decision = decision or {'outcome': 'none', 'template_ids': []}
        self.prompts = []

    def complete_text(self, prompt, result_type):
        self.prompts.append(prompt)
        if result_type.__name__ == 'TemplateMatchDecision':
            return result_type.model_validate(self.decision)
        return result_type.model_validate(model_payload(self.data))


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
    assert details['header']['h1'] == 18 and len(details['rows']) == 20
    assert next(column for column in details['schema'] if column['key'] == 'h1')['label'] == '温度'
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
    assert records[first]['header'] == {'h1': '甲'}
    assert records[second]['header'] == {'h1': 18}
    assert records[first]['schema'][0]['label'] == '客户'
    assert records[second]['schema'][0]['label'] == '温度'
    items = client.get(f'/api/v1/tasks/{first}/current-details?page=2&page_size=10').json()
    assert items['total'] == 25 and len(items['items']) == 10
    assert items['items'][0]['values']['i1'] == 10
    extraction = client.get(f'/api/v1/tasks/{first}/result').json()
    extraction['result']['header']['h1'] = '修订客户'
    response = client.put(f'/api/v1/tasks/{first}/review', json={
        'expected_version': extraction['review_version'], 'result': extraction['result'], 'detail_ids': extraction['detail_ids'],
    })
    assert response.status_code == 200, response.text
    updated = client.get(f'/api/v1/tables/{table_id}/files').json()['files']
    assert next(record for record in updated if record['task_id'] == first)['header']['h1'] == '修订客户'
    with client.app.state.session_factory() as session:
        original = session.get(ExtractionRecord, first)
        assert json.loads(original.result_json)['header']['h1'] == '甲'
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


def test_autonomous_row_width_is_required_before_assigning_cells():
    with pytest.raises(ValidationError, match="数量不一致"):
        AutonomousModelResult.model_validate({
            'header': [],
            'item_fields': [{'label': '品名', 'value_type': 'text'}, {'label': '数量', 'value_type': 'number'}],
            'items': [['合成纸笔']],
        })


def test_autonomous_header_and_detail_may_use_the_same_label():
    result = AutonomousModelResult.model_validate({
        'header': [{'label': '金额', 'value_type': 'number', 'value': 21}],
        'item_fields': [{'label': '金额', 'value_type': 'number'}],
        'items': [[10], [11]],
    }).extraction()
    assert [(field.key, field.label, field.section) for field in result.fields] == [
        ('h1', '金额', 'header'), ('i1', '金额', 'item'),
    ]
    assert result.header == {'h1': 21}
    assert result.items == [{'i1': 10}, {'i1': 11}]


def test_autonomous_same_section_label_collision_still_fails():
    with pytest.raises(ValidationError, match="同一分区"):
        AutonomousModelResult.model_validate({
            'header': [{'label': '金额', 'value_type': 'number', 'value': 21},
                       {'label': ' 金额 ', 'value_type': 'number', 'value': 20}],
            'item_fields': [],
            'items': [],
        })


def test_autonomous_model_dates_remove_sentence_punctuation_and_leave_text_unchanged():
    result = AutonomousModelResult.model_validate({
        'header': [
            {'label': '日期', 'value_type': 'date', 'value': ' 2026-11-20， '},
            {'label': '备注', 'value_type': 'text', 'value': '2026-11-20。'},
        ],
        'item_fields': [{'label': '日期', 'value_type': 'date'}],
        'items': [['2024-02-29.'], [None]],
    }).extraction()
    assert result.header == {'h1': '2026-11-20', 'h2': '2026-11-20。'}
    assert result.items == [{'i1': '2024-02-29'}, {'i1': None}]


@pytest.mark.parametrize('value', ['2026-02-29。', '2026-11-20 或 2026-11-21', '下周五', '2026-11', 20261120])
def test_autonomous_model_does_not_guess_dates(value):
    result = AutonomousModelResult.model_validate({
        'header': [{'label': '日期', 'value_type': 'date', 'value': value}],
        'item_fields': [],
        'items': [],
    })
    assert result.extraction().header == {'h1': None}
    assert result.type_issues()[0].raw_value == value


def test_autonomous_transport_keeps_exact_decimal_lexeme_for_review():
    result = parse_structured_content(
        '{"header":[{"label":"甲","value_type":"number","value":0.1234567890123456789},'
        '{"label":"乙","value_type":"number","value":2.5}],"item_fields":[],"items":[]}',
        AutonomousModelResult,
    )
    assert result.extraction().header == {'h1': None, 'h2': 2.5}
    issue, = result.type_issues()
    assert issue.field == 'header.h1'
    assert issue.raw_value == '0.1234567890123456789'


def test_autonomous_accepts_numeric_text_without_losing_digits(client):
    class NumericTextModel:
        model_name = 'fixture'

        def complete_text(self, prompt, result_type):
            return parse_structured_content(
                '{"header":[{"label":"编号","value_type":"text","value":42},'
                '{"label":"批次","value_type":"text","value":"0042"},'
                '{"label":"期限说明","value_type":"text","value":0.1234567890123456789}],'
                '"item_fields":[{"label":"记录号","value_type":"text"}],"items":[[9007199254740993],[42.5]]}',
                result_type,
            )

    task_id = upload(client, '编号.txt')
    process(client, task_id, NumericTextModel())
    saved = client.get(f'/api/v1/tasks/{task_id}/result').json()
    assert saved['result']['header'] == {'h1': '42', 'h2': '0042', 'h3': '0.1234567890123456789'}
    assert saved['result']['items'] == [{'i1': '9007199254740993'}, {'i1': '42.5'}]
    assert saved['validation_issues'] == []
    assert client.get('/api/v1/tasks').json()[0]['status'] == 'completed'


def test_autonomous_type_issues_keep_other_values_and_follow_detail_identity(client):
    task = upload(client)
    data = payload('日期', '下周五', 'date')
    data['items'] = [{'f2': 1}, {'f2': '10–20'}, {'f2': 3}]
    result = process(client, task, Model(data))
    assert result.result.header == {'h1': None}
    assert result.result.items == [{'i1': 1}, {'i1': None}, {'i1': 3}]
    assert [(issue.field, issue.raw_value) for issue in result.validation_issues] == [
        ('header.h1', '下周五'), ('items[1].i1', '10–20'),
    ]
    response = client.put(f'/api/v1/tasks/{task}/review', json={
        'expected_version': result.review_version,
        'detail_ids': result.detail_ids[1:],
        'result': {'header': {'h1': '2026-11-20'}, 'items': [{'i1': None}, {'i1': 3}]},
    })
    assert response.status_code == 200, response.text
    updated = response.json()
    assert [(issue['field'], issue['raw_value']) for issue in updated['validation_issues']] == [('items[0].i1', '10–20')]
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, task).status == 'needs_review'
    response = client.put(f'/api/v1/tasks/{task}/review', json={
        'expected_version': updated['review_version'], 'detail_ids': updated['detail_ids'],
        'result': {'header': {'h1': '2026-11-20'}, 'items': [{'i1': 10}, {'i1': 3}]},
    })
    assert response.status_code == 200, response.text
    assert not response.json()['validation_issues']
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, task).status == 'completed'


def test_warehouse_edits_keep_unresolved_model_value_with_its_detail(client):
    task = upload(client)
    data = payload()
    data['items'] = [{'f2': 1}, {'f2': '10–20'}, {'f2': 3}]
    result = process(client, task, Model(data))
    current_url = f'/api/v1/tasks/{task}/current'
    response = client.patch(current_url, json={
        'expected_version': result.review_version,
        'header_changes': {'h1': '更新后的客户'},
        'deleted_ids': result.detail_ids[:1],
    })
    assert response.status_code == 200, response.text
    after = client.get(f'/api/v1/tasks/{task}/result').json()
    assert after['result']['header'] == {'h1': '更新后的客户'}
    assert [(issue['field'], issue['raw_value']) for issue in after['validation_issues']] == [('items[0].i1', '10–20')]
    response = client.patch(current_url, json={
        'expected_version': after['review_version'],
        'detail_changes': {str(result.detail_ids[1]): {'i1': 12}},
    })
    assert response.status_code == 200, response.text
    assert client.get(f'/api/v1/tasks/{task}/result').json()['validation_issues'] == []


def test_failed_autonomous_response_reports_schema_path_without_model_values(client):
    content = json.dumps({'header': [{'label': '合成私有字段', 'value': '合成私有值'}], 'item_fields': [], 'items': []})
    envelope = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
    task_id = upload(client)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=envelope))) as transport:
        model = OpenAICompatibleProvider('http://synthetic.test/v1', 'synthetic', client=transport)
        with pytest.raises(RuntimeError, match="字段结构"):
            process(client, task_id, model)
    diagnostic = client.get(f'/api/v1/tasks/{task_id}/diagnostics').json()
    assert diagnostic['code'] == 'model_invalid_response'
    assert 'header.1.value_type' in diagnostic['detail']
    assert '缺少必填字段' in diagnostic['detail'] and '合成私有' not in diagnostic['detail']


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
    original = (Path(__file__).parent / 'fixtures' / 'autonomous_numeric_string_response.json').read_text(encoding='utf-8')
    data = json.loads(original)
    captured = json.dumps(model_payload(data))
    expected = {'header': {f'h{index}': value for index, value in enumerate(data['header'].values(), 1)},
                'items': [{f'i{index}': value for index, value in enumerate(row.values(), 1)} for row in data['items']]}
    expected['header']['h6'] = 21.5
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
    assert type(current['header']['h6']) is float and current['header']['h6'] == 21.5
    table_id = client.get('/api/v1/tables').json()[0]['id']
    exported = client.get(f'/api/v1/tables/{table_id}/export.json').json()['files'][0]
    assert exported['header'] == expected['header']
    assert [item['values'] for item in exported['items']] == expected['items']

    # Manual edits keep the supplied text instead of applying the model's repair.
    patch = client.patch(f'/api/v1/tasks/{task_id}/current', json={
        'expected_version': current['version'], 'header_changes': {'h6': '21.50'},
    })
    assert patch.status_code == 200, patch.text
    edited = client.get(f'/api/v1/tasks/{task_id}/current-details').json()
    assert edited['header']['h6'] == '21.50'
    review = client.put(f'/api/v1/tasks/{task_id}/review', json={
        'expected_version': edited['version'],
        'detail_ids': extraction['detail_ids'],
        'result': {**expected, 'header': {**expected['header'], 'h6': '22.00'}},
    })
    assert review.status_code == 200, review.text
    after = client.get(f'/api/v1/tasks/{task_id}/result').json()
    assert after['original_result'] == expected
    assert after['result']['header']['h6'] == '22.00'


def test_current_patch_uses_one_file_revision_and_never_restores_deleted_data(client):
    task = upload(client)
    process(client, task, Model())
    before = client.get(f'/api/v1/tasks/{task}/current-details').json()
    editing = client.post('/api/v1/tables/edit-sessions').json()['id']
    url = f'/api/v1/tasks/{task}/current'
    edited = client.patch(url, json={'expected_version': before['version'], 'edit_session_id': editing,
        'header_changes': {'h1': '新的客户'}, 'detail_changes': {str(before['items'][0]['id']): {'i1': 100}}})
    assert edited.status_code == 200, edited.text
    assert client.patch(url, json={'expected_version': before['version'], 'header_changes': {'h1': '过期'}}).status_code == 409
    deleted = client.patch(url, json={'expected_version': edited.json()['version'], 'edit_session_id': editing,
        'deleted_ids': [before['items'][1]['id']]})
    assert deleted.status_code == 200, deleted.text
    versions = client.get(f'/api/v1/tasks/{task}/revisions').json()['revisions']
    assert len(versions) == 2 and versions[0]['modified_fields'] == 2 and versions[0]['deleted_details'] == 1
    history = client.get(f"/api/v1/tasks/{task}/revisions/{versions[0]['id']}").json()
    removed = next(item for item in history['items'] if item['after'] is None)
    assert removed['before']['values'] == {'i1': 1}
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
        for index, part in enumerate(manifest['parts']):
            workbook = load_workbook(BytesIO(archive.read(part['workbook'])))
            assert len(workbook.worksheets) == 2
            assert workbook.worksheets[0]['C2'].value == 25
            sheet = workbook.worksheets[1]
            values = [cell for row in sheet.iter_rows() for cell in row]
            assert all(cell.data_type != 'f' for cell in values)
            assert part['files'] == ['相同名字.txt']
            assert all(cell.value not in {'文件标识', '当前数据版本', '提取读取范围'} for cell in values)
            if any(cell.value == '客户' for cell in values):
                assert any(cell.value == '=DANGEROUS()' for cell in values)
                assert any(cell.value == '客户' for cell in values)
                assert not any(cell.value == '温度' for cell in values)
            workbook.close()
    exported = client.get(url + '/export.json').json()
    assert exported['format'] == 'zhiyi-file-data-v1'
    by_id = {file['file_id']: file for file in exported['files']}
    assert by_id[first]['header']['h1'] == '=DANGEROUS()'
    assert by_id[second]['header']['h1'] == 18
    assert all(len(file['items']) == 25 for file in by_id.values())
    assert client.get(url + '/export.csv').status_code == 409
    monkeypatch.setattr(file_exports, 'FILES_PER_WORKBOOK', 100)
    excel = client.get(url + '/export.xlsx')
    assert excel.status_code == 200
    workbook = load_workbook(BytesIO(excel.content))
    assert len(workbook.sheetnames) == 3 and len(set(workbook.sheetnames)) == 3
    workbook.close()
