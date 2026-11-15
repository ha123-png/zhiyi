"""Synthetic adversarial regressions for second-round input/runtime findings."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import threading
import time
import zipfile

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import BaseModel
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.model_providers import OpenAICompatibleProvider
from document_pipeline_api.model_providers.base import ModelResponseError
from document_pipeline_api.schemas.input_scope import InputPolicy
from document_pipeline_api.services.document_preview import text_window, docx_window, image_thumbnail
from document_pipeline_api.services.file_formats import render_image_frames, UnsupportedImageError
from document_pipeline_api.services.processing_input import prepare_input, render_input_images
from document_pipeline_api.services import worker_health


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'document-pipeline.db'}",
                        storage_dir=tmp_path / 'uploads', queue_enabled=False)
    with TestClient(create_app(settings)) as result:
        yield result


@pytest.mark.parametrize('encoding', ['gb18030', 'utf-8', 'utf-16-le', 'utf-16-be'])
def test_preview_encoding_after_ascii_prefix_roundtrips(tmp_path, encoding):
    expected = 'A' * 33001 + '中文边界𠀀测试\n' * 6000
    path = tmp_path / 'late.txt'
    raw = expected.encode(encoding)
    if encoding.startswith('utf-16'):
        raw = (b'\xff\xfe' if encoding.endswith('le') else b'\xfe\xff') + raw
    path.write_bytes(raw)
    pieces, offset = [], 0
    while offset is not None:
        result = text_window(path, offset)
        pieces.append(result['text'])
        assert result['next_offset'] is None or result['next_offset'] > offset
        offset = result['next_offset']
    assert ''.join(pieces) == expected
    assert path.read_bytes() == raw


def test_docx_preview_preserves_run_separators_in_paragraphs_and_cells(tmp_path):
    path = tmp_path / 'separators.docx'
    paragraph = '<w:p><w:r><w:t>甲</w:t><w:tab/><w:t>123</w:t><w:br/><w:t>乙</w:t><w:cr/><w:t>456</w:t></w:r></w:p>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraph + '<w:tbl><w:tr><w:tc>' + paragraph + '</w:tc></w:tr></w:tbl></w:body></w:document>')
    result = docx_window(path, 0, max_uncompressed_bytes=100000)
    assert result['blocks'][0]['text'] == '甲\t123\n乙\n456'
    assert result['blocks'][1]['rows'] == [['甲\t123\n乙\n456']]


@pytest.mark.parametrize('mode', ['RGBA', 'LA', 'P'])
def test_transparent_preview_retains_black_ink_and_white_background(tmp_path, mode):
    path = tmp_path / 'transparent.png'
    image = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    for x in range(35, 65):
        for y in range(35, 65):
            image.putpixel((x, y), (0, 0, 0, 255))
    image.convert(mode).save(path)
    original = path.read_bytes()
    with Image.open(BytesIO(image_thumbnail(path, max_pixels=100000))) as result:
        assert min(result.getpixel((5, 5))) > 240
        assert max(result.getpixel((50, 50))) < 15
    assert path.read_bytes() == original


def test_rendered_jpeg_applies_orientation_before_stripping_exif(tmp_path):
    source = tmp_path / 'rotated.jpg'
    exif = Image.Exif()
    exif[274] = 6
    Image.new('RGB', (120, 60), 'red').save(source, exif=exif)
    original = source.read_bytes()
    [target] = render_image_frames(source, tmp_path, 'rotated', max_frames=1)
    with Image.open(target) as result:
        assert result.size == (60, 120)
        assert result.getexif().get(274, 1) == 1
    assert source.read_bytes() == original


def test_tiff_16bit_input_uses_full_range_in_rendered_model_input(tmp_path):
    source = tmp_path / 'gray.tiff'
    samples = [0, 16384, 32768, 49152, 65535]
    image = Image.frombytes('I;16', (5, 1), b''.join(value.to_bytes(2, 'little') for value in samples))
    image.save(source)
    settings = Settings(database_url='sqlite://', storage_dir=tmp_path / 'uploads')
    prepared, _ = prepare_input(source, 'image/tiff', settings, InputPolicy())
    paths = render_input_images(source, 'image/tiff', prepared, tmp_path / 'rendered', 'gray', settings)
    with Image.open(paths[0]) as result:
        assert [result.getpixel((i, 0))[0] for i in range(5)] == [0, 64, 128, 191, 255]


def test_corrupt_word_image_is_input_error_not_storage_error(tmp_path):
    from document_pipeline_api.schemas.input_scope import SourceRange
    from document_pipeline_api.services.input_scope import InputUnit, select_input
    path = tmp_path / 'broken.docx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/media/bad.png', b'broken-image')
    plan = select_input([InputUnit(SourceRange(kind='image', start=1, end=1), image_member='word/media/bad.png')], text_budget=100, image_budget=1)
    settings = Settings(database_url='sqlite://', storage_dir=tmp_path / 'uploads')
    with pytest.raises(UnsupportedImageError):
        render_input_images(path, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', plan, tmp_path / 'out', 'bad', settings)


def test_heartbeat_thread_survives_replace_and_cleanup_lock(tmp_path, monkeypatch):
    heartbeat = tmp_path / 'heartbeat.json'
    monkeypatch.setenv('DOCUMENT_PIPELINE_WORKER_HEARTBEAT', str(heartbeat))
    real_replace, real_unlink = worker_health.os.replace, Path.unlink
    locked, attempted = threading.Event(), threading.Event()
    locked.set()

    def replace(source, target):
        if locked.is_set():
            attempted.set()
            raise PermissionError('synthetic publishing lock')
        return real_replace(source, target)

    def unlink(path, *args, **kwargs):
        if path.suffix == '.tmp' and locked.is_set():
            raise PermissionError('synthetic cleanup lock')
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(worker_health.os, 'replace', replace)
    monkeypatch.setattr(Path, 'unlink', unlink)
    worker_health.start_worker_heartbeat(0.01)
    try:
        assert attempted.wait(2)
        time.sleep(0.25)
        locked.clear()
        until = time.monotonic() + 2
        while not heartbeat.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert worker_health._heartbeat_thread.is_alive()
        assert worker_health.read_worker_health(heartbeat)['online']
    finally:
        locked.clear()
        worker_health.stop_worker_heartbeat()


@pytest.mark.parametrize('choices', [[], [None], {}, 'bad', [{'message': None}], [{'message': {'content': 42}}]])
def test_bad_completion_envelopes_have_model_response_error(choices):
    class Result(BaseModel):
        answer: str
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': choices}))) as http:
        provider = OpenAICompatibleProvider('http://fixture.test/v1', 'fixture', client=http)
        with pytest.raises(ModelResponseError):
            provider.complete_text('synthetic', Result)


@pytest.mark.parametrize(('status', 'action'), [('paused', 'retry'), ('failed', 'resume')])
def test_wrong_task_action_is_conflict_not_capacity(client, status, action):
    with client.app.state.session_factory() as session:
        session.add(TaskRecord(id='wrong', filename='synthetic.txt', content_type='text/plain',
                    size_bytes=1, sha256='0' * 64, storage_path='wrong.txt', status=status))
        session.commit()
    response = client.post(f'/api/v1/tasks/wrong/{action}')
    assert response.status_code == 409, response.text
    with client.app.state.session_factory() as session:
        assert session.get(TaskRecord, 'wrong').status == status


def test_since_equivalent_instants_have_equal_list_and_summary(client):
    moment = datetime(2026, 9, 20, tzinfo=timezone.utc)
    with client.app.state.session_factory() as session:
        for index, delta in enumerate([-1, 1]):
            session.add(TaskRecord(id=f'time-{index}', filename='synthetic', content_type='text/plain',
                        size_bytes=1, sha256='0' * 64, storage_path='synthetic', status='completed',
                        updated_at=moment + timedelta(hours=delta)))
        session.commit()
    for since in ['2026-09-20T00:00:00Z', '2026-09-20T08:00:00+08:00', '2026-09-19T19:00:00-05:00', '2026-09-20T00:00:00']:
        response = client.get('/api/v1/tasks', params={'since': since})
        assert response.status_code == 200
        assert [task['id'] for task in response.json()] == ['time-1']
        assert client.get('/api/v1/tasks/summary', params={'since': since}).json()['total'] == 1


@pytest.mark.parametrize(('today', 'current_offset', 'boundary_offset'), [
    ((2026, 3, 10), -4, -5), ((2026, 11, 3), -5, -4),
    ((2026, 3, 10), 8, 8), ((2026, 11, 3), 0, 0),
])
def test_calendar_start_uses_boundary_date_offset(client, monkeypatch, today, current_offset, boundary_offset):
    from document_pipeline_api.api import stats
    boundary = datetime(*today) - timedelta(days=6)

    class LocalClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(*today, 12)
            return value if tz is None else value.astimezone(tz)

        def astimezone(self, tz=None):
            local_offset = current_offset if self.date() == datetime(*today).date() else boundary_offset
            local_tz = timezone(timedelta(hours=local_offset))
            return super(LocalClock, self.replace(tzinfo=self.tzinfo or local_tz)).astimezone(tz or local_tz)

    monkeypatch.setattr(stats, 'datetime', LocalClock)
    expected = boundary.replace(tzinfo=timezone(timedelta(hours=boundary_offset))).astimezone(timezone.utc)
    assert stats._range_start(7).astimezone(timezone.utc) == expected
    with client.app.state.session_factory() as session:
        for index, minutes in enumerate([-30, 30]):
            session.add(TaskRecord(id=f'dst-{index}', filename='synthetic', content_type='text/plain',
                size_bytes=1, sha256='0' * 64, storage_path='synthetic', status='completed',
                created_at=expected + timedelta(minutes=minutes)))
        session.commit()
    assert client.get('/api/v1/stats/summary?days=7').json()['received_count'] == 1


def _upload_backup_task(client, suffix):
    from image_test_data import PNG_BYTES
    result = client.post('/api/v1/tasks', files={'file': (f'{suffix}.png', PNG_BYTES + suffix.encode(), 'image/png')}, data={'template_mode': 'invoice'})
    assert result.status_code == 201, result.text
    return result.json()['id']


def _tamper_backup_database(path, database, sql):
    import hashlib
    import json
    import sqlite3
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    database.write_bytes(members['database.db'])
    connection = sqlite3.connect(database)
    try:
        connection.execute(sql)
        connection.commit()
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        connection.close()
    members['database.db'] = database.read_bytes()
    manifest = json.loads(members['manifest.json'])
    manifest['database']['sha256'] = hashlib.sha256(members['database.db']).hexdigest()
    manifest['database']['size_bytes'] = len(members['database.db'])
    members['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


@pytest.mark.parametrize('column', ['export_state_json', 'file_name_json'])
def test_semantic_backup_corruption_rejected_before_any_live_changes(client, tmp_path, column):
    task_id = _upload_backup_task(client, 'original')
    created = client.post('/api/v1/backups').json()['name']
    path = tmp_path / 'backups' / created
    good_backup = path.read_bytes()
    _tamper_backup_database(path, tmp_path / 'tamper.db', f"UPDATE tasks SET {column}='not-json'")
    _upload_backup_task(client, 'after-backup')
    before = client.get('/api/v1/tasks').json()
    from document_pipeline_api.storage_paths import resolve_task_storage_path
    with client.app.state.session_factory() as session:
        original_path = resolve_task_storage_path(client.app.state.settings.storage_dir, session.get(TaskRecord, task_id))
    original_bytes = original_path.read_bytes()
    response = client.post(f'/api/v1/backups/{created}/restore')
    assert response.status_code == 409, response.text
    assert client.get('/api/v1/tasks').json() == before
    assert original_path.read_bytes() == original_bytes
    path.write_bytes(good_backup)
    response = client.post(f'/api/v1/backups/{created}/restore')
    assert response.status_code == 200, response.text
    assert [task['id'] for task in client.get('/api/v1/tasks').json()] == [task_id]


def test_committed_restore_cleanup_failure_returns_success_and_recovers(client, tmp_path, monkeypatch):
    import json
    from document_pipeline_api.business_backup import recover_interrupted_restore
    task_id = _upload_backup_task(client, 'original')
    name = client.post('/api/v1/backups').json()['name']
    _upload_backup_task(client, 'after-backup')
    real_unlink = Path.unlink
    failed = False

    def unlink(path, *args, **kwargs):
        nonlocal failed
        if path.name == 'restore-pending.json' and not failed and json.loads(path.read_text())['phase'] == 'committed':
            failed = True
            raise OSError('synthetic one-time post-commit cleanup failure')
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', unlink)
    response = client.post(f'/api/v1/backups/{name}/restore')
    assert failed
    assert response.status_code == 200, response.text
    assert response.json()['restart_required']
    import sqlite3
    with sqlite3.connect(tmp_path / 'document-pipeline.db') as connection:
        assert connection.execute('SELECT id FROM tasks').fetchall() == [(task_id,)]
    journal = tmp_path / 'runtime' / 'restore-pending.json'
    assert json.loads(journal.read_text())['phase'] == 'committed'
    recover_interrupted_restore(tmp_path)
    assert not journal.exists()
    assert [task['id'] for task in client.get('/api/v1/tasks').json()] == [task_id]


def _word_with_image(image_bytes, extension='jpg'):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:t>合成原件</w:t><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p></w:body></w:document>')
        archive.writestr('word/_rels/document.xml.rels', f'<Relationships><Relationship Id="rId1" Target="media/image.{extension}"/></Relationships>')
        archive.writestr(f'word/media/image.{extension}', image_bytes)
    return output.getvalue()


def _configured_image_task(client, content, extension, content_type):
    assert client.put('/api/v1/system/settings', json={'office_convert': True, 'image_convert': True, 'word_include_images': True}).status_code == 200
    template = client.post('/api/v1/templates', json={'name': '图像字节验收', 'fields': [{'key': 'title', 'label': '标题', 'section': 'header'}]}).json()
    response = client.post('/api/v1/tasks', files={'file': (f'synthetic.{extension}', content, content_type)}, data={'template_id': template['id']})
    assert response.status_code == 201, response.text
    return response.json()['id']


@pytest.mark.parametrize('kind', ['jpeg', 'docx', 'tiff'])
def test_worker_sends_preserved_or_normalized_image_bytes(client, kind):
    import base64
    import json
    from document_pipeline_api.services.extraction import process_task
    source = BytesIO()
    if kind == 'tiff':
        Image.frombytes('I;16', (5, 1), b''.join(value.to_bytes(2, 'little') for value in [0, 16384, 32768, 49152, 65535])).save(source, 'TIFF')
        content, extension, content_type = source.getvalue(), 'tiff', 'image/tiff'
    else:
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (120, 60), 'red').save(source, 'JPEG', exif=exif)
        content, extension, content_type = source.getvalue(), 'jpg', 'image/jpeg'
        if kind == 'docx':
            content, extension, content_type = _word_with_image(content), 'docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    task_id = _configured_image_task(client, content, extension, content_type)
    captured = []

    def respond(request):
        payload = json.loads(request.content)
        for item in payload['messages'][1]['content']:
            if item['type'] == 'image_url':
                captured.append(base64.b64decode(item['image_url']['url'].split(',', 1)[1]))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"header":{"title":"合成"},"items":[]}'}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        provider = OpenAICompatibleProvider('http://fixture.test/v1', 'fixture', client=http)
        with client.app.state.session_factory() as session:
            assert process_task(session, client.app.state.settings, task_id, client=provider) is not None
    assert len(captured) == 1
    with Image.open(BytesIO(captured[0])) as image:
        if kind == 'jpeg':
            assert captured[0] == content
            assert image.getexif()[274] == 6
        elif kind == 'docx':
            assert image.size == (60, 120)
            assert image.getexif().get(274, 1) == 1
        else:
            assert [image.getpixel((i, 0))[0] for i in range(5)] == [0, 64, 128, 191, 255]


def test_template_generation_sends_oriented_png(client, monkeypatch):
    import base64
    import json
    from document_pipeline_api.services import template_ai
    source = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new('RGB', (120, 60), 'red').save(source, 'JPEG', exif=exif)
    captured = []

    def respond(request):
        payload = json.loads(request.content)
        image_item = next(item for item in payload['messages'][1]['content'] if item['type'] == 'image_url')
        captured.append(base64.b64decode(image_item['image_url']['url'].split(',', 1)[1]))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"name":"样例","fields":[{"label":"标题","section":"header","value_type":"text"}],"presentation_mode":"table"}'}}]})

    provider = OpenAICompatibleProvider('http://fixture.test/v1', 'fixture', client=httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(template_ai, 'build_model_provider', lambda *args, **kwargs: provider)
    response = client.post('/api/v1/templates/generate', files={'files': ('sample.jpg', source.getvalue(), 'image/jpeg')}, data={'requirement': '读取标题'})
    assert response.status_code == 200, response.text
    with Image.open(BytesIO(captured[0])) as image:
        assert image.format == 'PNG'
        assert image.size == (60, 120)


def test_worker_diagnoses_corrupt_word_image_before_model_call(client):
    from document_pipeline_api.services.extraction import process_task
    from document_pipeline_api.models import ExtractionRecord
    from sqlalchemy import select
    content = _word_with_image(b'corrupt', 'png')
    task_id = _configured_image_task(client, content, 'docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))) as http:
        provider = OpenAICompatibleProvider('http://fixture.test/v1', 'fixture', client=http)
        with client.app.state.session_factory() as session:
            with pytest.raises(UnsupportedImageError):
                process_task(session, client.app.state.settings, task_id, client=provider)
            task = session.get(TaskRecord, task_id)
            assert task.failure_code == 'input_preprocessing_failed'
            assert session.scalar(select(ExtractionRecord).where(ExtractionRecord.task_id == task_id)) is None
            from document_pipeline_api.storage_paths import resolve_task_storage_path
            assert resolve_task_storage_path(client.app.state.settings.storage_dir, task).read_bytes() == content
    assert calls == []
