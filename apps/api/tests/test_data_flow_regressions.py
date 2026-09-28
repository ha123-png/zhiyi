import json
from datetime import datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import DataRowRecord, DataTableRecord
from document_pipeline_api.services.data_tables import parse_import_rows, ImportFileError


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / 'uploads', queue_enabled=False))) as client:
        yield client


def sheet_bytes(headers=('客户', '商品'), rows=(('甲', '一'), (None, '二')), merged='A2:A3'):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    if merged:
        sheet.merge_cells(merged)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_import_batches_never_share_a_header_edit_group(client):
    content = sheet_bytes()
    created = client.post('/api/v1/tables/import-new', files={'file': ('first.xlsx', content)})
    assert created.status_code == 201, created.text
    table_id = created.json()['id']
    assert client.post(f'/api/v1/tables/{table_id}/import', files={'file': ('second.xlsx', content)}).status_code == 200
    table = client.get(f'/api/v1/tables/{table_id}').json()
    rows = table['rows']
    assert rows[0]['values']['__row_group'] == rows[1]['values']['__row_group']
    assert rows[2]['values']['__row_group'] == rows[3]['values']['__row_group']
    assert rows[0]['values']['__row_group'] != rows[2]['values']['__row_group']
    key = next(column['key'] for column in table['columns'] if column['label'] == '客户')
    response = client.patch(f"/api/v1/tables/{table_id}/rows/{rows[0]['id']}", json={'expected_version': rows[0]['version'], 'changes': {key: '只修改第一次导入'}})
    assert response.status_code == 200, response.text
    assert [row['values'][key] for row in client.get(f'/api/v1/tables/{table_id}').json()['rows']] == ['只修改第一次导入', '只修改第一次导入', '甲', '甲']


def test_import_cannot_promote_an_existing_detail_to_shared_header(client):
    with client.app.state.session_factory() as session:
        session.add(DataTableRecord(id='independent', name='明细表', template_key='manual-test', template_version='1', document_kind='custom',
            columns_json=json.dumps([{'key': 'customer', 'label': '客户', 'section': 'item', 'value_type': 'text'}])))
        session.commit()
    response = client.post('/api/v1/tables/independent/import', files={'file': ('merged.xlsx', sheet_bytes())})
    assert response.status_code == 422, response.text
    table = client.get('/api/v1/tables/independent').json()
    assert table['row_count'] == 0
    assert table['columns'][0]['section'] == 'item'


def test_xlsx_export_does_not_merge_across_interleaved_groups_or_differing_values(client):
    with client.app.state.session_factory() as session:
        session.add(DataTableRecord(id='mixed', name='交错来源', template_key='merged-test', template_version='1', document_kind='custom',
            columns_json=json.dumps([{'key': 'customer', 'label': '客户', 'section': 'header', 'value_type': 'text'}, {'key': 'item', 'label': '商品', 'section': 'item', 'value_type': 'text'}])))
        session.flush()
        for index, (group, value) in enumerate([('a','甲'),('b','乙'),('a','甲'),('a','另一抬头')], 1):
            session.add(DataRowRecord(table_id='mixed', item_index=index, row_json=json.dumps({'__row_group': group, 'customer': value, 'item': str(index)})))
        session.commit()
    response = client.get('/api/v1/tables/mixed/export.xlsx')
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content))
    try:
        assert [workbook.active.cell(row, 1).value for row in range(2,6)] == ['甲','乙','甲','另一抬头']
        assert not list(workbook.active.merged_cells.ranges)
    finally:
        workbook.close()


def test_csv_chinese_and_corrupt_encoding_are_not_silently_replaced():
    content = '客户,商品\n甲公司,螺丝\n'
    for encoding in ('utf-8-sig', 'gb18030'):
        assert parse_import_rows(content.encode(encoding), 'data.csv') == [{'客户':'甲公司','商品':'螺丝'}]
    with pytest.raises(ImportFileError):
        parse_import_rows(b'customer\n\xff\n', 'data.csv')


def test_xlsx_date_value_is_serializable_without_losing_the_date(client):
    content = sheet_bytes(headers=('日期',), rows=((datetime(2026,9,20),),), merged=None)
    response = client.post('/api/v1/tables/import-new', files={'file': ('date.xlsx', content)})
    assert response.status_code == 201, response.text
    row = client.get(f"/api/v1/tables/{response.json()['id']}").json()['rows'][0]
    assert row['values']['import_1'].startswith('2026-09-20')


def test_xlsx_native_types_and_text_identifiers_survive_import(client):
    content = sheet_bytes(headers=('编号', '数量', '日期', '完成'),
        rows=(('00123', 0, datetime(2026, 9, 20), False), ('00456', 12.5, datetime(2026, 9, 21), True)), merged=None)
    response = client.post('/api/v1/tables/import-new', files={'file': ('types.xlsx', content)})
    assert response.status_code == 201, response.text
    table = client.get(f"/api/v1/tables/{response.json()['id']}").json()
    assert [column['value_type'] for column in table['columns']] == ['text', 'number', 'date', 'boolean']
    first = table['rows'][0]['values']
    assert [first[f'import_{index}'] for index in range(1, 5)] == ['00123', 0, '2026-09-20', False]


def test_import_into_typed_columns_is_atomic_and_reports_the_bad_row(client):
    created = client.post('/api/v1/tables', json={'name': '类型校验', 'template_key': 'builtin-invoice'}).json()
    url = f"/api/v1/tables/{created['id']}"
    content = '销售方,价税合计,开票日期,新列\n甲公司,120,2026-09-20,一\n乙公司,错误,2026-09-21,二\n'.encode()
    response = client.post(url + '/import', files={'file': ('bad.csv', content)})
    assert response.status_code == 422
    assert '第 2 条数据' in response.json()['detail']
    assert '价税合计' in response.json()['detail']
    table = client.get(url).json()
    assert table['row_count'] == 0
    assert not any(column['label'] == '新列' for column in table['columns'])
    content = '销售方,价税合计,开票日期\n甲公司,"1,234.50",2026-09-20\n'.encode()
    assert client.post(url + '/import', files={'file': ('valid.csv', content)}).status_code == 200
    values = client.get(url).json()['rows'][0]['values']
    assert values['total_amount'] == 1234.5 and values['document_date'] == '2026-09-20'


def test_custom_numeric_column_rejects_text_and_zero_prevents_deletion(client):
    table = client.post('/api/v1/tables', json={'name': '附加列', 'template_key': 'builtin-invoice'}).json()
    url = f"/api/v1/tables/{table['id']}"
    column = client.post(url + '/columns', json={'label': '评级', 'section': 'item', 'value_type': 'number'}).json()
    assert column['value_type'] == 'number'
    assert client.post(url + '/rows', json={'values': {column['key']: 'bad'}}).status_code == 422
    assert client.post(url + '/rows', json={'values': {column['key']: 0}}).status_code == 201
    assert client.delete(url + '/columns/' + column['key']).status_code == 409
