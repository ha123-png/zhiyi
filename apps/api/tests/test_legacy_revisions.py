"""Wrong pre-baseline histories must not become a second runtime history API."""
import json

from sqlalchemy import select

from test_file_data_api import client as file_client, get
from document_pipeline_api.models import DataRowRecord, DataRowRevisionRecord, ReviewRevisionRecord

client = file_client


def test_old_models_have_no_runtime_history_routes_and_do_not_affect_current(client):
    before = get(client, '/tasks/A/current-details')
    with client.app.state.session_factory() as session:
        row = session.scalar(select(DataRowRecord).where(DataRowRecord.task_id == 'A'))
        session.add(ReviewRevisionRecord(task_id='A', version=2, result_json='{}', validation_json='[]',
            changes_json=json.dumps([{'path': 'seller_name', 'before': '旧值', 'after': '错误副本'}])))
        session.add(DataRowRevisionRecord(table_id=row.table_id, row_id=row.id, task_id='A', version=3,
            operation='update', before_json='{}', after_json='{}', editor='old-model'))
        session.commit()
    history = get(client, '/tasks/A/revisions')
    assert history['total'] == 1 and 'legacy_total' not in history
    assert get(client, f'/tables/{row.table_id}/rows/{row.id}/revisions') == []
    assert client.get('/api/v1/tasks/A/legacy-revisions').status_code == 404
    assert client.get('/api/v1/tasks/A/legacy-revisions/review:1').status_code == 404
    assert not any('legacy-revisions' in path for path in client.app.openapi()['paths'])
    assert get(client, '/tasks/A/current-details') == before
    revision = get(client, f"/tasks/A/revisions/{history['revisions'][0]['id']}")
    assert revision['added_details'] == 2
    assert client.delete('/api/v1/tasks/A').status_code == 200
    with client.app.state.session_factory() as session:
        assert session.scalar(select(ReviewRevisionRecord).where(ReviewRevisionRecord.task_id == 'A')) is None
        assert session.scalar(select(DataRowRevisionRecord).where(DataRowRevisionRecord.task_id == 'A')) is None
