from io import BytesIO

from openpyxl import Workbook

from test_table_management import _client


def workbook_bytes():
    workbook = Workbook()
    workbook.active.title = '第一批'
    workbook.active.append(['编号', '数量'])
    workbook.active.append(['001', 10])
    sheet = workbook.create_sheet('第二批')
    sheet.append(['编号', '数量'])
    sheet.append(['002', 20])
    workbook.active = 1
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_sheet_choice_is_required_before_any_write_and_each_import_is_explicit(tmp_path):
    with _client(tmp_path) as client:
        contents = workbook_bytes()
        files = {'file': ('两页.xlsx', contents)}
        result = client.post('/api/v1/tables/import-new', files=files)
        assert result.status_code == 409, result.text
        assert result.json()['detail'] == {'code': 'select_sheet', 'message': '工作簿有多个业务工作表，请选择本次导入的一页。', 'sheets': ['第一批', '第二批']}
        assert client.get('/api/v1/tables').json() == []
        created = client.post('/api/v1/tables/import-new', files=files, data={'sheet_name': '第一批'})
        assert created.status_code == 201, created.text
        table = created.json()
        assert table['name'] == '两页 · 第一批'
        initial = client.get(f"/api/v1/tables/{table['id']}").json()
        columns = {column['label']: column['key'] for column in initial['columns']}
        assert len(initial['rows']) == 1
        assert initial['rows'][0]['values'][columns['编号']] == '001'
        assert initial['rows'][0]['values'][columns['数量']] == 10
        assert client.post(f"/api/v1/tables/{table['id']}/import", files=files).status_code == 409
        assert client.get(f"/api/v1/tables/{table['id']}").json()['row_count'] == 1
        assert client.post(f"/api/v1/tables/{table['id']}/import", files=files, data={'sheet_name': '第二批'}).status_code == 200
        final = client.get(f"/api/v1/tables/{table['id']}").json()
        assert [row['values'][columns['编号']] for row in final['rows']] == ['001', '002']
        assert len({row['values']['__row_group'] for row in final['rows']}) == 2
        assert client.post(f"/api/v1/tables/{table['id']}/import", files=files, data={'sheet_name': '已删除页'}).status_code == 422
        assert client.get(f"/api/v1/tables/{table['id']}").json()['row_count'] == 2


def test_a_user_sheet_is_not_metadata_just_because_of_its_name(tmp_path):
    with _client(tmp_path) as client:
        workbook = Workbook()
        workbook.active.title = '知意输入范围'
        workbook.active.append(['业务内容'])
        workbook.active.append(['不能被忽略'])
        workbook.create_sheet('空白页')
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        response = client.post('/api/v1/tables/import-new', files={'file': ('内容.xlsx', output.getvalue())})
        assert response.status_code == 201, response.text
        table = client.get('/api/v1/tables/' + response.json()['id']).json()
        assert len(table['columns']) == 1 and table['row_count'] == 1
        assert '不能被忽略' in table['rows'][0]['values'].values()
