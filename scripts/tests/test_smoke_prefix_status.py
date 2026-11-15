"""Verify the prefix smoke result without network, models or a service."""

import importlib
import json
from pathlib import Path
import runpy
import shutil
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("partial_only, outcomes, expected_exit", [
    (True, [False], 1), (True, [True], 0),
    (False, [True, False], 1), (False, [True, True], 0),
])
def test_prefix_smoke_status_matches_report(tmp_path, monkeypatch, partial_only, outcomes, expected_exit):
    script = tmp_path / "scripts/smoke-prefix.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/smoke-prefix.py", script)
    config = importlib.import_module("document_pipeline_api.config")
    app = importlib.import_module("document_pipeline_api.main")
    model = importlib.import_module("document_pipeline_api.model_providers.openai_compatible")
    extraction_module = importlib.import_module("document_pipeline_api.services.extraction")
    testclient = importlib.import_module("fastapi.testclient")
    key_file = tmp_path / "synthetic-key.txt"
    key_file.write_text("test-only-placeholder", encoding="utf-8")
    arguments = ["smoke-prefix.py", "--key-file", str(key_file)]
    if partial_only:
        arguments.append("--partial-only")
    monkeypatch.setattr(sys, "argv", arguments)

    provider = SimpleNamespace(closed=False)
    provider.close = lambda: setattr(provider, "closed", True)
    monkeypatch.setattr(model, "OpenAICompatibleProvider", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(config, "Settings", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(app, "create_app", lambda _settings: object())

    class Response:
        def __init__(self, value=None, content=b""):
            self.value, self.content = value, content
        def raise_for_status(self): pass
        def json(self): return self.value

    class Session:
        def get(self, *_):
            return SimpleNamespace(failure_code="synthetic_failure", failure_message="simulated")

    class Client:
        def __init__(self, _app):
            self.app = SimpleNamespace(state=SimpleNamespace(session_factory=lambda: nullcontext(Session())))
            self.source = b""
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def request(self, method, path, **kwargs):
            if method == "POST" and path.endswith("/templates"):
                return Response({"id": "synthetic-template"})
            if method == "PUT" and path.endswith("/system/settings"):
                return Response({})
            if method == "POST" and path.endswith("/tasks"):
                self.source = kwargs["files"]["file"][1]
                return Response({"id": "synthetic-task"})
            if method == "GET" and path.endswith("/file"):
                return Response(content=self.source)
            raise AssertionError((method, path))

    calls = []
    def process_task(*_args, **_kwargs):
        index = len(calls)
        calls.append(index)
        if not outcomes[index]:
            return None
        limited = partial_only or index == 1
        coverage = "partial" if limited else "complete"
        name = "合成建议名.txt" if limited else "知意合成测试-植物观察记录.txt"
        scope = SimpleNamespace(coverage=coverage, model_dump=lambda: {"coverage": coverage})
        return SimpleNamespace(file_name=SimpleNamespace(suggested_filename=name),
                               input_scope=scope, result=SimpleNamespace(model_dump=lambda: {}))
    monkeypatch.setattr(testclient, "TestClient", Client)
    monkeypatch.setattr(extraction_module, "process_task", process_task)

    try:
        runpy.run_path(str(script), run_name="__main__")
        exit_code = 0
    except SystemExit as error:
        exit_code = error.code if error.code is not None else 0
    reports = list((tmp_path / ".local/tests").glob("prefix-live-*/report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert provider.closed
    assert report["calls"] == len(outcomes)
    assert [case["success"] for case in report["cases"]] == outcomes
    assert exit_code == expected_exit
