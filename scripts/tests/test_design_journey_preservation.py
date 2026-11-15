"""Resume-safe fixture and ledger checks with no model or running service."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONTENT = ("合成测试材料，无真实用户信息。\n会议名称：研发评审会议\n日期：2026年9月12日\n"
           "主要决定：先验证用户流程，再发布升级版本。\n后续行动：整理试用反馈，核对原文件与结构化结果。\n")
CONFLICT = b"existing independent file - do not overwrite"


def setup_journey(tmp_path, monkeypatch, mode):
    spec = importlib.util.spec_from_file_location("design_journey_preservation", ROOT / "scripts/smoke-design-journey.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts/smoke-design-journey.py"))
    monkeypatch.setattr(sys, "argv", ["smoke-design-journey.py", "--key-file", str(tmp_path / "unused-key")])
    monkeypatch.setattr(module, "Settings", lambda **_kwargs: object())
    monkeypatch.setattr(module, "create_app", lambda _settings: object())
    root = tmp_path / ".local/acceptance-design-live"
    root.mkdir(parents=True)
    fields = [{"key": f"field-{i}", "label": f"Field {i}"} for i in range(4)]
    template = {"id": "synthetic-template", "name": "合成模板", "version": 1, "fields": fields,
                "description": "", "extra_instructions": "", "validation_rules": [],
                "deterministic_rules": [], "output_mapping": {}, "behavior": {}}
    cases = {"complete": {"passed": True}, "prefix": {"task_id": "synthetic-task"}} if mode == "conflict" else {}
    state = {"requests": [], "profile": {"id": "synthetic-profile", "version": 1},
             "draft": {}, "template": template, "reordered": mode != "ledger", "cases": cases}
    ledger = root / "journey.json"
    ledger.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    events = []
    class StopBeforeModel(RuntimeError):
        pass
    class Response:
        is_success = True
        status_code = 200
        text = ""
        def __init__(self, value=None, content=b""):
            self.value, self.content = value, content
        def json(self): return self.value
    class Client:
        def __init__(self, _): self.saved = template
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def request(self, method, path, **kwargs):
            events.append((method, path))
            if path.endswith("/activate"):
                return Response({})
            if path.endswith("/templates/synthetic-template"):
                if method == "PUT":
                    self.saved = {**template, **kwargs["json"]}
                return Response(self.saved)
            if path.endswith("/system/settings"):
                if mode in {"fixture", "ledger"}:
                    raise StopBeforeModel("stopped at settings")
                return Response({})
            if path.endswith("/local-export"):
                return Response({"revision": 0})
            if path.endswith("/tasks"):
                return Response([{"id": "synthetic-task", "status": "needs_review"}])
            if path.endswith("/tasks/synthetic-task/result"):
                return Response({"template_id": "synthetic-template", "input_scope": {"coverage": "partial"},
                                 "file_name": {"suggested_filename": "合成副本.txt"}, "review_version": 0})
            if path.endswith("/tasks/synthetic-task/file"):
                return Response(content=(CONTENT + "补充观察记录。" * 200 + "TAIL_NOT_SENT_917").encode())
            if path.endswith("/tasks/synthetic-task/confirm"):
                raise StopBeforeModel("stopped at confirmation")
            raise AssertionError((method, path))
    monkeypatch.setattr(module, "TestClient", Client)
    return module, root, ledger, events, StopBeforeModel


def test_existing_different_source_fixture_is_preserved(tmp_path, monkeypatch):
    module, root, ledger, events, _ = setup_journey(tmp_path, monkeypatch, "fixture")
    fixture = root / "研发评审会议纪要-20260912.txt"
    fixture.write_bytes(b"old unique fixture")
    before = ledger.read_bytes()
    with pytest.raises(RuntimeError, match="different contents"):
        module.main()
    assert fixture.read_bytes() == b"old unique fixture"
    assert ledger.read_bytes() == before
    assert not any(path.endswith("/system/settings") for _, path in events)


def test_matching_fixture_is_reused_without_rewrite(tmp_path, monkeypatch):
    module, root, _, events, stop = setup_journey(tmp_path, monkeypatch, "fixture")
    fixture = root / "研发评审会议纪要-20260912.txt"
    fixture.write_bytes(CONTENT.encode())
    before = fixture.stat().st_mtime_ns
    with pytest.raises(stop, match="settings"):
        module.main()
    assert fixture.read_bytes() == CONTENT.encode() and fixture.stat().st_mtime_ns == before
    assert any(path.endswith("/system/settings") for _, path in events)


@pytest.mark.parametrize("matches", [False, True])
def test_conflict_fixture_is_preserved_or_reused(tmp_path, monkeypatch, matches):
    module, root, _, events, stop = setup_journey(tmp_path, monkeypatch, "conflict")
    conflict = root / "external-copy/合成模板/合成副本.txt"
    conflict.parent.mkdir(parents=True)
    expected = CONFLICT if matches else b"old independent bytes"
    conflict.write_bytes(expected)
    before = conflict.stat().st_mtime_ns
    if matches:
        with pytest.raises(stop, match="confirmation"):
            module.main()
        assert any(path.endswith("/confirm") for _, path in events)
    else:
        with pytest.raises(RuntimeError, match="different contents"):
            module.main()
        assert not any(path.endswith("/confirm") for _, path in events)
    assert conflict.read_bytes() == expected and conflict.stat().st_mtime_ns == before


def test_existing_interrupted_ledger_temp_is_preserved(tmp_path, monkeypatch):
    module, root, ledger, _, _ = setup_journey(tmp_path, monkeypatch, "ledger")
    pending = root / "journey.tmp"
    pending.write_bytes(b"interrupted unique ledger")
    before = ledger.read_bytes()
    with pytest.raises(FileExistsError):
        module.main()
    assert pending.read_bytes() == b"interrupted unique ledger"
    assert ledger.read_bytes() == before


def test_checkpoint_progress_can_still_update_its_own_ledger(tmp_path, monkeypatch):
    module, root, ledger, events, stop = setup_journey(tmp_path, monkeypatch, "ledger")
    with pytest.raises(stop, match="settings"):
        module.main()
    saved = json.loads(ledger.read_text(encoding="utf-8"))
    assert saved["reordered"] is True
    assert not (root / "journey.tmp").exists()
    assert any(path.endswith("/system/settings") for _, path in events)


@pytest.mark.parametrize("predicate", ["is_symlink", "is_junction"])
def test_fixture_rejects_linked_ancestor_without_touching_file(tmp_path, monkeypatch, predicate):
    module, root, _, _, _ = setup_journey(tmp_path, monkeypatch, "fixture")
    parent = root / "linked"
    parent.mkdir()
    target = parent / "old.txt"
    target.write_bytes(b"old independent bytes")
    original = getattr(Path, predicate)
    monkeypatch.setattr(Path, predicate, lambda path: path == parent or original(path))
    with pytest.raises(RuntimeError, match="link"):
        module.write_fixture_once(target, b"different bytes", root)
    assert target.read_bytes() == b"old independent bytes"


def test_fixture_rejects_traversal_outside_its_boundary(tmp_path, monkeypatch):
    module, root, _, _, _ = setup_journey(tmp_path, monkeypatch, "fixture")
    external = root / "external-copy"
    external.mkdir()
    with pytest.raises(RuntimeError, match="escapes"):
        module.write_fixture_once(external / ".." / "unexpected.txt", b"synthetic", external)
    assert not (root / "unexpected.txt").exists()
