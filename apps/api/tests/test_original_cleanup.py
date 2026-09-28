import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import PendingOriginalDeletionRecord, TaskRecord
from document_pipeline_api.services.original_cleanup import (
    enqueue_original_deletions, pending_cleanup_count, recover_original_deletions,
)


def settings_at(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path / 'data.db'}", storage_dir=tmp_path / 'uploads', queue_enabled=False)


def seed(client, task_id="owned"):
    settings = client.app.state.settings
    path = settings.storage_dir / f"{task_id}.txt"
    path.write_text("internal original", encoding="utf-8")
    with client.app.state.session_factory() as session:
        session.add(TaskRecord(id=task_id, filename='原件.txt', content_type='text/plain', size_bytes=17,
            sha256=task_id, storage_path=path.name, template_mode='auto', status='failed'))
        session.commit()
    return path


@pytest.mark.skipif(os.name != 'nt', reason='Real Windows file sharing lock')
def test_locked_original_is_reported_durably_and_cleaned_after_release(tmp_path):
    settings = settings_at(tmp_path)
    with TestClient(create_app(settings)) as client:
        path = seed(client)
        with path.open('rb'):
            response = client.delete('/api/v1/tasks/owned')
            assert response.status_code == 200, response.text
            assert response.json()['pending_cleanup'] == 1
            with client.app.state.session_factory() as session:
                assert session.get(TaskRecord, 'owned') is None
                assert pending_cleanup_count(session) == 1
            assert path.exists()
        recover_original_deletions(client.app.state.session_factory, settings)
        assert not path.exists()
        with client.app.state.session_factory() as session:
            assert pending_cleanup_count(session) == 0


def test_post_commit_interruption_keeps_cleanup_for_restart(tmp_path, monkeypatch):
    settings = settings_at(tmp_path)
    from document_pipeline_api.services import original_cleanup
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        path = seed(client)
        real_drain = original_cleanup.drain_original_deletions
        def interrupted(*args, **kwargs):
            raise RuntimeError('simulated exit after the database committed')
        with monkeypatch.context() as patch:
            patch.setattr(original_cleanup, 'drain_original_deletions', interrupted)
            response = client.delete('/api/v1/tasks/owned')
            assert response.status_code == 500
            with client.app.state.session_factory() as session:
                assert session.get(TaskRecord, 'owned') is None
                assert pending_cleanup_count(session) == 2
            assert path.exists()
        assert original_cleanup.drain_original_deletions is real_drain
    with TestClient(create_app(settings)) as client:
        recover_original_deletions(client.app.state.session_factory, settings)
        assert not path.exists()
        with client.app.state.session_factory() as session:
            assert pending_cleanup_count(session) == 0


def test_uncommitted_cleanup_intent_cannot_delete_a_live_original(tmp_path):
    settings = settings_at(tmp_path)
    with TestClient(create_app(settings)) as client:
        path = seed(client)
        with client.app.state.session_factory() as session:
            enqueue_original_deletions(session, settings, [path])
            session.rollback()
        recover_original_deletions(client.app.state.session_factory, settings)
        assert path.read_text(encoding='utf-8') == 'internal original'
        with client.app.state.session_factory() as session:
            assert pending_cleanup_count(session) == 0


def test_delete_task_removes_rendered_pages_and_keeps_retry_obligation(tmp_path, monkeypatch):
    settings = settings_at(tmp_path)
    from document_pipeline_api.services import original_cleanup
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        original = seed(client)
        rendered = settings.storage_dir / 'rendered' / 'owned'
        rendered.mkdir(parents=True)
        page = rendered / 'page-1.png'
        page.write_bytes(b'derived image')
        with monkeypatch.context() as patch:
            patch.setattr(original_cleanup, 'drain_original_deletions',
                          lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('interrupted')))
            assert client.delete('/api/v1/tasks/owned').status_code == 500
        with client.app.state.session_factory() as session:
            assert session.get(TaskRecord, 'owned') is None
            assert pending_cleanup_count(session) == 2
        assert original.exists() and page.exists()
    with TestClient(create_app(settings)) as client:
        recover_original_deletions(client.app.state.session_factory, settings)
        assert not original.exists() and not rendered.exists()
        with client.app.state.session_factory() as session:
            assert pending_cleanup_count(session) == 0


def test_rendered_reparse_point_keeps_external_file_and_cleanup_obligation(tmp_path):
    settings = settings_at(tmp_path)
    external = tmp_path / 'external-pages'
    external.mkdir()
    (external / 'private.png').write_bytes(b'external')
    with TestClient(create_app(settings)) as client:
        seed(client)
        rendered_root = settings.storage_dir / 'rendered'
        rendered_root.mkdir()
        try:
            (rendered_root / 'owned').symlink_to(external, target_is_directory=True)
        except OSError:
            pytest.skip('Directory symlink creation is unavailable')
        response = client.delete('/api/v1/tasks/owned')
        assert response.status_code == 200, response.text
        assert response.json()['pending_cleanup'] == 1
        assert (external / 'private.png').read_bytes() == b'external'
        with client.app.state.session_factory() as session:
            pending = session.get(PendingOriginalDeletionRecord, 'rendered/owned')
            assert pending is not None and pending.failure_code == 'invalid_path'


def test_cleanup_rejects_external_paths_and_reused_owner(tmp_path):
    settings = settings_at(tmp_path)
    external = tmp_path / 'external.txt'
    external.write_text('keep', encoding='utf-8')
    with TestClient(create_app(settings)) as client:
        path = seed(client)
        with client.app.state.session_factory() as session:
            enqueue_original_deletions(session, settings, [path])
            session.add(PendingOriginalDeletionRecord(relative_path='../external.txt', task_id='external'))
            session.commit()
        recover_original_deletions(client.app.state.session_factory, settings)
        assert external.read_text(encoding='utf-8') == 'keep'
        assert path.exists()
        with client.app.state.session_factory() as session:
            assert {entry.failure_code for entry in session.scalars(select(PendingOriginalDeletionRecord))} == {'owner_exists', 'invalid_path'}
