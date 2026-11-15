"""Assistant admission/deletion failures using SQLite and no provider process."""

import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from document_pipeline_api.api import assistant
from document_pipeline_api.config import Settings
from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import AssistantMessage, AssistantRun, AssistantThread
from document_pipeline_api.models.assistant import AssistantEvent
from document_pipeline_api.schemas.model_profiles import ModelProfileCreate
from document_pipeline_api.services.model_profiles import create_model_profile


class UnstartedConversation:
    """Accept only the owned assistant worker without running its target."""

    def __init__(self, *, target, args, daemon, name):
        assert target is assistant.run_conversation
        assert daemon and name == "zhiyi-assistant"
        self.args = args

    def start(self):
        pass


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'lifecycle.db'}", storage_dir=tmp_path / 'uploads')
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        profile = create_model_profile(session, ModelProfileCreate(
            name="Synthetic", provider="openai_compatible", base_url="http://127.0.0.1:1/v1",
            model_name="never-contacted", context_length=32000,
        ), None)
        session.add(AssistantThread(id="thread", title="Synthetic conversation"))
        session.commit()
    app = SimpleNamespace(state=SimpleNamespace(
        session_factory=factory, settings=settings, model_secret_store=None,
        assistant_active={}, active_mutations=0, maintenance_condition=threading.Condition(),
    ))
    monkeypatch.setattr(assistant, 'threading', SimpleNamespace(Event=threading.Event, Thread=UnstartedConversation))
    yield SimpleNamespace(app=app, request=SimpleNamespace(app=app), factory=factory, engine=engine, profile=profile)
    # Accepted workers in these tests are inert stubs, never real providers.
    app.state.assistant_active.clear()
    app.state.active_mutations = 0
    engine.dispose()


def payload(runtime, **updates):
    return assistant.Send(request_id=uuid4(), thread_id="thread", text="Synthetic question",
                          profile_id=runtime.profile.id, profile_version=runtime.profile.version, **updates)


def test_delete_cannot_remove_a_concurrently_accepted_run(runtime):
    observed = {}

    class DeletingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            value = super().scalar(statement, *args, **kwargs)
            # This is the deletion endpoint's only scalar: its active-run check.
            with runtime.factory() as second:
                second.execute(text('PRAGMA busy_timeout=0'))
                try:
                    observed['accepted'] = assistant.start(payload(runtime), runtime.request, second)
                except OperationalError as error:
                    assert error.orig.sqlite_errorcode & 0xff == 5
                    observed['write_blocked'] = True
            return value

    with DeletingSession(runtime.engine) as first:
        deleted = assistant.remove_thread('thread', first)
    assert deleted == {'deleted': True}
    assert observed == {'write_blocked': True}, observed
    with runtime.factory() as session, pytest.raises(HTTPException) as raised:
        assistant.start(payload(runtime), runtime.request, session)
    assert raised.value.status_code == 404
    assert not runtime.app.state.assistant_active
    assert runtime.app.state.active_mutations == 0


def test_delete_refuses_a_previously_accepted_run(runtime):
    with runtime.factory() as session:
        accepted = assistant.start(payload(runtime), runtime.request, session)
    with runtime.factory() as session, pytest.raises(HTTPException) as raised:
        assistant.remove_thread('thread', session)
    assert raised.value.status_code == 409
    with runtime.factory() as session:
        assert session.get(AssistantRun, accepted['run_id']) is not None
        assert session.get(AssistantThread, 'thread') is not None


@pytest.mark.parametrize('stage', ['construct', 'start'])
def test_worker_launch_failure_finishes_run_and_releases_admission(runtime, monkeypatch, stage):
    class FailingConversation(UnstartedConversation):
        def __init__(self, **kwargs):
            if stage == 'construct':
                raise RuntimeError('synthetic thread allocation failure')
            super().__init__(**kwargs)

        def start(self):
            raise RuntimeError('synthetic thread start failure')

    monkeypatch.setattr(assistant.threading, 'Thread', FailingConversation)
    body = payload(runtime)
    observed_error = None
    with runtime.factory() as session:
        try:
            accepted = assistant.start(body, runtime.request, session)
        except Exception as error:
            observed_error = str(error)
            accepted = {'run_id': str(body.request_id), 'thread_id': 'thread'}
    with runtime.factory() as session:
        run = session.get(AssistantRun, accepted['run_id'])
        stored_events = list(session.scalars(select(AssistantEvent).where(AssistantEvent.run_id == run.id)))
        answer = session.get(AssistantMessage, run.message_id)
        assert run.status == 'failed', {
            'status': run.status, 'raised': observed_error,
            'active_mutations': runtime.app.state.active_mutations,
            'registered_run': run.id in runtime.app.state.assistant_active,
        }
        assert run.error and 'synthetic' not in run.error
        assert answer is not None
        assert len(stored_events) == 1
        terminal = json.loads(stored_events[0].payload_json)
        assert terminal['type'] == 'run.completed' and terminal['status'] == 'failed'
        assert run.snapshot_sequence == stored_events[0].sequence
    assert observed_error is None
    assert runtime.app.state.active_mutations == 0
    assert not runtime.app.state.assistant_active
    # A transport retry reads the same durable failure; a new intent can start.
    with runtime.factory() as session:
        assert assistant.start(body, runtime.request, session) == accepted
    monkeypatch.setattr(assistant.threading, 'Thread', UnstartedConversation)
    with runtime.factory() as session:
        newer = assistant.start(payload(runtime), runtime.request, session)
    assert newer['run_id'] != accepted['run_id']


def test_worker_launch_failure_releases_admission_when_failure_receipt_cannot_commit(runtime, monkeypatch):
    class FailingConversation(UnstartedConversation):
        def start(self):
            raise RuntimeError('synthetic thread start failure')

    monkeypatch.setattr(assistant.threading, 'Thread', FailingConversation)
    # A real SQLite write failure expires SQLAlchemy state. Cleanup must not
    # read an expired ORM identifier before releasing the process-local count.
    with runtime.engine.begin() as connection:
        connection.execute(text("""CREATE TRIGGER reject_test_event BEFORE INSERT ON assistant_events
            BEGIN SELECT RAISE(FAIL, 'synthetic event write failure'); END"""))
    with runtime.factory() as session:
        try:
            assistant.start(payload(runtime), runtime.request, session)
        except Exception as error:
            observed_error = error
        else:
            pytest.fail('Synthetic event storage rejection must propagate')
    assert runtime.app.state.active_mutations == 0, type(observed_error).__name__
    assert not runtime.app.state.assistant_active
    assert isinstance(observed_error, IntegrityError)
