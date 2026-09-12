from pathlib import Path
from unittest.mock import Mock

import subprocess

from document_pipeline_api.model_providers.local_model_manager import (
    LmStudioManager,
    OllamaManager,
    _parse_cli_models,
)


def test_ollama_load_and_unload_use_native_keep_alive_protocol(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, json):
            calls.append((url, json))
            return Response()

    monkeypatch.setattr("document_pipeline_api.model_providers.local_model_manager.httpx.Client", Client)
    manager = OllamaManager("http://127.0.0.1:11434/v1")

    assert manager.load_model("qwen3-vl:4b", 8192)["ok"] is True
    assert manager.unload_model("qwen3-vl:4b")["ok"] is True
    assert calls[0][1]["options"] == {"num_ctx": 8192}
    assert calls[0][1]["keep_alive"] == "30m"
    assert calls[1][1]["keep_alive"] == 0


def test_ollama_start_hides_windows_console(monkeypatch) -> None:
    manager = OllamaManager("http://127.0.0.1:11434")
    manager._ollama = "ollama.exe"
    captured: dict[str, object] = {}

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return Mock(stderr=None)

    monkeypatch.setattr("document_pipeline_api.model_providers.local_model_manager.os.name", "nt")
    monkeypatch.setattr("document_pipeline_api.model_providers.local_model_manager.subprocess.Popen", fake_popen)

    statuses = iter([{"running": False}, {"running": True}])
    monkeypatch.setattr(manager, "_status", lambda: next(statuses))
    assert manager.start_server()["ok"] is True
    assert captured["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_lmstudio_load_model_hides_windows_console(monkeypatch) -> None:
    manager = object.__new__(LmStudioManager)
    manager._lms = "lms.exe"
    manager.server_status = lambda: {"running": True, "loaded": ["model"]}
    manager.loaded_models = lambda: []
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return __import__("subprocess").CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr("document_pipeline_api.model_providers.local_model_manager.os.name", "nt")
    monkeypatch.setattr("document_pipeline_api.model_providers.local_model_manager.subprocess.run", fake_run)

    assert manager.load_model("model")["ok"] is True
    assert captured["creationflags"] == __import__("subprocess").CREATE_NO_WINDOW


def test_lmstudio_start_does_not_launch_from_restricted_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lms = tmp_path / "lms.exe"
    manager._lmstudio_home = tmp_path / "readonly-home"
    run_cli = Mock()

    monkeypatch.setattr(manager, "server_status", lambda: {"running": False})
    monkeypatch.setattr(manager, "_home_is_writable", lambda: False)
    monkeypatch.setattr(manager, "_run_cli", run_cli)

    result = manager.start_server()

    assert result["ok"] is False
    assert "受限" in result["message"]
    assert "手动打开 LM Studio" in result["detail"]
    run_cli.assert_not_called()


def test_lmstudio_home_write_probe_removes_its_temporary_file(tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lmstudio_home = tmp_path

    assert manager._home_is_writable() is True
    assert list(tmp_path.iterdir()) == []


def test_lmstudio_start_uses_daemon_instead_of_launching_gui(monkeypatch, tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:4321/v1")
    manager._lms = tmp_path / "lms.exe"
    statuses = iter([{"running": False}, {"running": True, "message": "ready"}])
    calls: list[tuple[list[str], int]] = []

    monkeypatch.setattr(manager, "server_status", lambda: next(statuses))
    monkeypatch.setattr(manager, "_home_is_writable", lambda: True)
    monkeypatch.setattr(
        manager,
        "_run_cli",
        lambda arguments, timeout: calls.append((arguments, timeout))
        or subprocess.CompletedProcess(arguments, 0, "", ""),
    )

    result = manager.start_server()

    assert result["ok"] is True
    assert calls == [
        (["daemon", "up"], 30),
        (["server", "start", "--port", "4321"], 30),
    ]


def test_lmstudio_load_is_non_interactive(monkeypatch, tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lms = tmp_path / "lms.exe"
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "loaded", ""))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(manager, "loaded_models", lambda: [])
    monkeypatch.setattr(
        manager,
        "server_status",
        lambda: {"running": True, "loaded": ["qwen3.5-4b"]},
    )

    result = manager.load_model("qwen3.5-4b", 4096)

    assert result["ok"] is True
    assert "--yes" in run.call_args.args[0]
    assert run.call_args.args[0][-3:] == ["--identifier", "qwen3.5-4b", "--yes"]


def test_lmstudio_load_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lms = tmp_path / "lms.exe"
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(manager, "loaded_models", lambda: ["qwen3.5-4b"])

    result = manager.load_model("qwen3.5-4b", 4096)

    assert result["ok"] is True
    assert "无需重复" in result["message"]
    run.assert_not_called()


def test_lmstudio_load_timeout_is_not_reported_as_success(monkeypatch, tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lms = tmp_path / "lms.exe"
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("lms", 120)))
    monkeypatch.setattr(manager, "loaded_models", lambda: [])
    monkeypatch.setattr(
        manager,
        "server_status",
        lambda: {"running": True, "loaded": []},
    )

    result = manager.load_model("qwen3.5-4b", 4096)

    assert result["ok"] is False
    assert "超时" in result["message"]


def test_lmstudio_download_uses_official_non_interactive_cli(monkeypatch, tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lms = tmp_path / "lms.exe"
    run_cli = Mock(return_value=subprocess.CompletedProcess([], 0, "done", ""))
    monkeypatch.setattr(manager, "_run_cli", run_cli)

    result = manager.download_model("unsloth/qwen3.5-4b-gguf@q4_k_m")

    assert result["ok"] is True
    run_cli.assert_called_once_with(
        ["get", "unsloth/qwen3.5-4b-gguf@q4_k_m", "--gguf", "--yes"],
        timeout=4 * 60 * 60,
    )


def test_lmstudio_old_cli_does_not_fall_back_to_gui_start(monkeypatch, tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lms = tmp_path / "lms.exe"
    calls: list[list[str]] = []
    monkeypatch.setattr(manager, "server_status", lambda: {"running": False})
    monkeypatch.setattr(manager, "_home_is_writable", lambda: True)
    monkeypatch.setattr(
        manager,
        "_run_cli",
        lambda arguments, timeout: calls.append(arguments)
        or subprocess.CompletedProcess(arguments, 1, "", "unknown command daemon"),
    )

    result = manager.start_server()

    assert result["ok"] is False
    assert "升级" in result["detail"]
    assert calls == [["daemon", "up"]]


def test_parse_lms_json_uses_model_keys() -> None:
    output = '[{"modelKey":"qwen3.5-4b","identifier":"custom"},{"modelKey":"embed"}]'

    assert _parse_cli_models(output) == ["qwen3.5-4b", "embed"]
    assert _parse_cli_models(output, include_identifiers=True) == [
        "qwen3.5-4b",
        "custom",
        "embed",
    ]


def test_lmstudio_status_does_not_treat_jit_downloads_as_loaded(monkeypatch, tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lms = tmp_path / "lms.exe"

    class Response:
        def __init__(self, payload):
            self.payload = payload
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url):
            if url.endswith("/api/v0/models"):
                return Response(
                    {"data": [
                        {"id": "downloaded-only", "state": "not-loaded"},
                        {"id": "ready", "state": "loaded"},
                    ]}
                )
            return Response({"data": [{"id": "downloaded-only"}, {"id": "ready"}]})

    monkeypatch.setattr("document_pipeline_api.model_providers.local_model_manager.httpx.Client", Client)

    status = manager.server_status()

    assert status["running"] is True
    assert status["loaded"] == ["ready"]


def test_lmstudio_port_collision_returns_actionable_status(monkeypatch, tmp_path: Path) -> None:
    manager = LmStudioManager("http://127.0.0.1:1234/v1")
    manager._lms = tmp_path / "lms.exe"

    class BadResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"unexpected": True}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url):
            return BadResponse()

    monkeypatch.setattr("document_pipeline_api.model_providers.local_model_manager.httpx.Client", Client)

    status = manager.server_status()

    assert status["running"] is False
    assert "占用" in status["message"]


def test_ollama_missing_install_does_not_attempt_launch(monkeypatch):
    manager = OllamaManager("http://127.0.0.1:11434")
    manager._ollama = None
    monkeypatch.setattr(manager, "_status", lambda: {"running": False})
    launch = Mock()
    monkeypatch.setattr(subprocess, "Popen", launch)
    result = manager.start_server()
    assert not result["ok"] and "安装" in result["detail"]
    launch.assert_not_called()


def test_ollama_start_reports_process_exit_and_sanitized_reason(monkeypatch):
    import io
    manager = OllamaManager("http://127.0.0.1:11435")
    manager._ollama = "ollama.exe"
    monkeypatch.setattr(manager, "_status", lambda: {"running": False})
    process = Mock(stderr=io.BytesIO(b"address already in use api_key=synthetic-secret\n"), returncode=1)
    process.poll.return_value = 1
    launch = Mock(return_value=process)
    monkeypatch.setattr(subprocess, "Popen", launch)
    result = manager.start_server()
    assert not result["ok"] and "退出" in result["message"]
    assert "端口" in result["detail"] and "synthetic-secret" not in result["detail"]
    assert launch.call_args.kwargs["env"]["OLLAMA_HOST"] == "127.0.0.1:11435"


def test_ollama_start_is_idempotent(monkeypatch):
    manager = OllamaManager("http://127.0.0.1:11434")
    monkeypatch.setattr(manager, "_status", lambda: {"running": True})
    launch = Mock()
    monkeypatch.setattr(subprocess, "Popen", launch)
    assert manager.start_server()["ok"]
    launch.assert_not_called()
