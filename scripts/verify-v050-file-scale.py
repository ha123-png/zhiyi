"""10,000 heterogeneous synthetic files; no model calls or production files."""
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import DataTableRecord, TaskRecord, ExtractionRecord
from document_pipeline_api.services.current_data import initialize_current


def main():
    root = Path(__file__).resolve().parents[1] / '.local' / ('v050-file-scale-' + uuid4().hex[:8])
    root.mkdir(parents=True)
    settings = Settings(database_url=f'sqlite:///{root / "document-pipeline.db"}', storage_dir=root / 'uploads', queue_enabled=False)
    report = {'synthetic_files': 10000, 'real_model_calls': 0, 'measurements': [], 'checks': {}}
    started = perf_counter()
    with TestClient(create_app(settings)) as client:
        table_id = str(uuid4())
        with client.app.state.session_factory() as session:
            session.add(DataTableRecord(id=table_id, name='万份异构文件验收', template_key='builtin-autonomous',
                template_version='scale-fixture', document_kind='custom', collection_kind='files'))
            session.commit()
            for index in range(10000):
                task_id = f'scale-file-{index:05d}'
                field = f'value_{index % 3}'
                fields = [{'key': 'title', 'label': '标题', 'section': 'header', 'value_type': 'text'},
                    {'key': field, 'label': ('数量', '联系人', '日期')[index % 3], 'section': 'item',
                     'value_type': ('number', 'text', 'date')[index % 3]}]
                values = ([index, index + 1, index + 2], ['张三', '李四', '王五'], ['2026-09-21'] * 3)[index % 3]
                header, items = {'title': f'异构文件 {index:05d}'}, [{field: value} for value in values]
                original = json.dumps({'fields': fields, 'header': header, 'items': items}, ensure_ascii=False)
                content = original.encode()
                relative = f'{task_id}.txt'
                (settings.storage_dir / relative).write_bytes(content)
                session.execute(insert(TaskRecord), [{'id': task_id, 'filename': relative, 'content_type': 'text/plain',
                    'size_bytes': len(content), 'sha256': sha256(content).hexdigest(), 'storage_path': relative,
                    'status': 'completed', 'template_mode': 'custom', 'template_id': 'builtin-autonomous', 'template_version': 1}])
                session.execute(insert(ExtractionRecord), [{'task_id': task_id, 'document_kind': 'custom',
                    'template_id': 'builtin-autonomous', 'template_version': 1, 'model_name': 'synthetic-file-scale',
                    'prompt_version': 'scale', 'elapsed_seconds': 0, 'result_json': original,
                    'output_schema_json': json.dumps(fields), 'validation_json': '[]'}])
                initialize_current(session, task_id=task_id, table_id=table_id, header=header, items=items, schema=fields)
                if (index + 1) % 500 == 0:
                    session.commit()
                    session.expunge_all()
            session.commit()
        report['seed_seconds'] = round(perf_counter() - started, 2)

        def get(name, url, **kwargs):
            begin = perf_counter()
            response = client.get('/api/v1' + url, **kwargs)
            report['measurements'].append({'name': name, 'seconds': round(perf_counter() - begin, 3), 'bytes': len(response.content)})
            assert response.status_code == 200, response.text[:500]
            return response.json()

        first = get('first_six_cards', f'/tables/{table_id}/source-cards')
        assert first['total'] == 10000 and len(first['groups']) == 6
        assert report['measurements'][-1]['bytes'] < 30000
        last = get('last_cards', f'/tables/{table_id}/source-cards?page=1667')
        assert len(last['groups']) == 4
        search = get('one_file_search', f'/tables/{table_id}/source-cards', params={'search': '异构文件 09999'})
        assert search['total'] == 1 and search['groups'][0]['task_id'] == 'scale-file-09999'
        details = get('file_detail', '/tasks/scale-file-09999/current-details')
        assert details['total'] == 3 and details['items'][-1]['values'] == {'value_0': 10001}
        report['checks']['file_pagination_search_and_final_values'] = True
        scope = {'source_keys': json.dumps([card['id'] for card in first['groups']])}
        exported = get('six_distinct_file_json_export', f'/tables/{table_id}/export.json', params=scope)
        assert len(exported['files']) == 6
        assert len({json.dumps(file['schema'], sort_keys=True) for file in exported['files']}) == 3
        response = client.get(f'/api/v1/tables/{table_id}/export.xlsx', params=scope)
        assert response.status_code == 200
        assert client.get(f'/api/v1/tables/{table_id}/export.xlsx').status_code == 409
        report['checks']['heterogeneous_export_scope_and_workbook_limit'] = True
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(root / 'report.json'), **report}, ensure_ascii=False))


if __name__ == '__main__':
    main()
