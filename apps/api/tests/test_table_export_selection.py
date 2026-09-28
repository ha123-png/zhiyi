import csv
import json
from io import BytesIO, StringIO
from urllib.parse import unquote
from zipfile import ZipFile

from openpyxl import load_workbook

from test_table_management import _client, _create_manual_table, _add_row
from test_autonomous_extraction import client as file_client, upload, process, Model, payload

client = file_client


def test_every_export_applies_the_intersection_of_group_search_and_selection(tmp_path):
    with _client(tmp_path) as client:
        table = _create_manual_table(client)
        rows = [_add_row(client, table['id'], seller_name=seller, name=item, quantity=quantity)
                for seller, item, quantity in [('甲', '目标', 1), ('甲', '其他', 2), ('乙', '目标', 3)]]
        views = client.post(f"/api/v1/tables/{table['id']}/split", json={'field_key': 'seller_name'}).json()
        view = next(view for view in views if view['field_value'] == '甲')
        scope = {'view_id': view['id'], 'search': '目标', 'row_ids': f"{rows[0]['id']},{rows[2]['id']}"}
        for extension in ('json', 'csv', 'xlsx'):
            response = client.get(f"/api/v1/tables/{table['id']}/export.{extension}", params=scope)
            assert response.status_code == 200, response.text
            assert response.headers['X-Zhiyi-Row-Count'] == '1'
            assert '选中1条' in unquote(response.headers['Content-Disposition'])
            assert '搜索结果' in unquote(response.headers['Content-Disposition'])
            if extension == 'json':
                values = response.json()
                assert len(values) == 1 and values[0]['quantity'] == 1
            elif extension == 'csv':
                values = list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))
                assert len(values) == 1 and values[0]['数量'] == '1'
            else:
                book = load_workbook(BytesIO(response.content))
                data = list(book.active.values)
                assert len(data) == 2 and data[1][data[0].index('数量')] == 1
                book.close()
        split = client.get(f"/api/v1/tables/{table['id']}/export-views.xlsx", params=scope)
        assert split.status_code == 200, split.text
        book = load_workbook(BytesIO(split.content))
        assert book.sheetnames == ['甲'] and book.active.max_row == 2
        book.close()
        assert client.get(f"/api/v1/tables/{table['id']}/export.json", params={'row_ids': ''}).json() == []
        assert client.get(f"/api/v1/tables/{table['id']}/export.json", params={'row_ids': str(rows[0]['id'])}).json()[0]['quantity'] == 1
        for invalid in ({'row_ids': '999999'}, {'view_id': 'deleted'}, {'row_ids': 'true'}, {'row_ids': ',1'}, {'task_ids': 'not-this-table'}):
            assert client.get(f"/api/v1/tables/{table['id']}/export.json", params=invalid).status_code in (409, 422)


def test_unmatched_null_or_missing_fields_survive_split_export(tmp_path):
    with _client(tmp_path) as client:
        table = _create_manual_table(client)
        _add_row(client, table['id'], seller_name='甲', quantity=1)
        _add_row(client, table['id'], seller_name=None, quantity=2)
        _add_row(client, table['id'], quantity=3)
        assert client.post(f"/api/v1/tables/{table['id']}/split", json={'field_key': 'seller_name'}).status_code == 200
        response = client.get(f"/api/v1/tables/{table['id']}/export-views.xlsx")
        assert response.status_code == 200, response.text
        book = load_workbook(BytesIO(response.content))
        quantities = [row[list(sheet.values)[0].index('数量')] for sheet in book for row in list(sheet.values)[1:]]
        assert sorted(quantities) == [1, 2, 3]
        book.close()


def test_autonomous_export_matches_file_search_and_preserves_all_its_details(client):
    first, second = upload(client, '匹配文件.txt'), upload(client, '别的文件.txt')
    data = payload()
    data['items'] = [{'f2': 987654321}, {'f2': 20}]
    process(client, first, Model(data))
    process(client, second, Model())
    table = client.get('/api/v1/tables').json()[0]['id']
    scope = {'search': '987654321'}
    listing = client.get(f'/api/v1/tables/{table}/files', params=scope).json()
    assert [file['task_id'] for file in listing['files']] == [first]
    response = client.get(f'/api/v1/tables/{table}/export.json', params=scope)
    assert response.status_code == 200, response.text
    assert response.headers['X-Zhiyi-File-Count'] == '1'
    assert [file['file_id'] for file in response.json()['files']] == [first]
    assert len(response.json()['files'][0]['items']) == 2  # search selects a whole file card
    packed = client.get(f'/api/v1/tables/{table}/export.zip', params=scope)
    assert packed.status_code == 200, packed.text
    with ZipFile(BytesIO(packed.content)) as archive:
        manifest = json.loads(archive.read('目录.json'))
        assert manifest['total_files'] == 1 and manifest['parts'][0]['files'][0]['id'] == first
        book = load_workbook(BytesIO(archive.read(manifest['parts'][0]['workbook'])))
        assert book['目录'].cell(2, 3).value == 2
        book.close()
    assert client.get(f'/api/v1/tables/{table}/export.json', params={'task_ids': second}).json()['files'][0]['file_id'] == second
    assert client.get(f'/api/v1/tables/{table}/export.json', params={'task_ids': ''}).json()['files'] == []
    assert client.get(f'/api/v1/tables/{table}/export.json', params={'row_ids': ''}).status_code == 422
