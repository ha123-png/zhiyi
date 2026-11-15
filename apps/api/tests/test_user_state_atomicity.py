import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from document_pipeline_api.api import user_state


def _request(path):
    settings = SimpleNamespace(storage_dir=path / "uploads")
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


@pytest.mark.parametrize("payload", ["[]", "null", '"state"', "42"])
def test_non_object_state_returns_uncompleted_without_overwriting_file(tmp_path, payload):
    path = tmp_path / "user-state.json"
    path.write_text(payload, encoding="utf-8")

    assert user_state.get_onboarding(_request(tmp_path)).completed_version == 0
    assert path.read_text(encoding="utf-8") == payload


def test_concurrent_onboarding_updates_publish_independent_complete_files(tmp_path, monkeypatch):
    written = threading.Barrier(2)
    publish = threading.Lock()
    original_replace = user_state.os.replace

    def ordered_publish(source, target):
        # Both requests finish their write before either publishes; the second
        # rename runs after the first completes, a valid thread interleaving.
        written.wait(timeout=10)
        with publish:
            original_replace(source, target)

    monkeypatch.setattr(user_state.os, "replace", ordered_publish)
    request = _request(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda version: user_state.update_onboarding(
                user_state.OnboardingState(completed_version=version), request
            ).completed_version,
            [1, 2],
        ))

    assert results == [1, 2]
    stored = json.loads((tmp_path / "user-state.json").read_text(encoding="utf-8"))
    assert stored in [{"onboarding_completed_version": 1}, {"onboarding_completed_version": 2}]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["user-state.json"]


def test_failed_publish_keeps_previous_state_and_removes_only_own_temporary(tmp_path, monkeypatch):
    path = tmp_path / "user-state.json"
    path.write_text('{"onboarding_completed_version": 1}', encoding="utf-8")

    def fail_publish(*_):
        raise OSError("synthetic publish failure")

    monkeypatch.setattr(user_state.os, "replace", fail_publish)
    with pytest.raises(OSError, match="synthetic publish failure"):
        user_state.update_onboarding(user_state.OnboardingState(completed_version=2), _request(tmp_path))

    assert user_state.get_onboarding(_request(tmp_path)).completed_version == 1
    assert sorted(item.name for item in tmp_path.iterdir()) == ["user-state.json"]
