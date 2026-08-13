import os
from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.services.integration_config import (
    apply_integration_config,
    effective_integration_settings,
    generate_token,
    read_integration_config,
    write_integration_config,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )


def test_generate_token_is_long_and_unique() -> None:
    first = generate_token()
    second = generate_token()
    assert len(first) >= 32
    assert first != second


def test_write_read_roundtrip(tmp_path: Path) -> None:
    write_integration_config(
        tmp_path,
        {
            "read_token": "read-secret",
            "write_token": "write-secret",
            "mcp_task_control": True,
            "mcp_task_read": True,
            "mcp_template_read": True,
            "mcp_result_read": True,
            "mcp_data_read": True,
            "mcp_file_roots": ["D:/inbox", "D:/outbox"],
        },
    )
    config = read_integration_config(tmp_path)
    assert config["read_token"] == "read-secret"
    assert config["write_token"] == "write-secret"
    assert config["mcp_task_control"] == "1"
    assert config["mcp_task_read"] == "1"
    assert config["mcp_file_roots"] == os.pathsep.join(["D:/inbox", "D:/outbox"])


def test_write_none_removes_field(tmp_path: Path) -> None:
    write_integration_config(tmp_path, {"read_token": "abc"})
    write_integration_config(tmp_path, {"read_token": None})
    assert "read_token" not in read_integration_config(tmp_path)


def test_apply_integration_config_sets_env(tmp_path: Path, monkeypatch) -> None:
    write_integration_config(
        tmp_path,
        {
            "read_token": "read-secret",
            "write_token": "write-secret",
            "mcp_task_control": True,
            "mcp_write_enabled": True,
        },
    )
    for key in (
        "DOCUMENT_PIPELINE_INTEGRATION_READ_TOKEN",
        "DOCUMENT_PIPELINE_INTEGRATION_WRITE_TOKEN",
        "DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED",
        "DOCUMENT_PIPELINE_MCP_WRITE_ENABLED",
        "DOCUMENT_PIPELINE_MCP_WRITE_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    apply_integration_config(tmp_path)
    try:
        assert os.environ["DOCUMENT_PIPELINE_INTEGRATION_READ_TOKEN"] == "read-secret"
        assert os.environ["DOCUMENT_PIPELINE_INTEGRATION_WRITE_TOKEN"] == "write-secret"
        assert os.environ["DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED"] == "1"
        # 写开关开启时，MCP 写密钥同步为集成写密钥
        assert os.environ["DOCUMENT_PIPELINE_MCP_WRITE_TOKEN"] == "write-secret"
    finally:
        # apply_integration_config 直接写 os.environ，必须显式清理，
        # 否则短密钥会泄漏进后续 MCP 测试的子进程环境（monkeypatch.setenv
        # 的撤销语义会把它恢复成 apply 写入的值，等于没清）。
        for key in (
            "DOCUMENT_PIPELINE_INTEGRATION_READ_TOKEN",
            "DOCUMENT_PIPELINE_INTEGRATION_WRITE_TOKEN",
            "DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED",
            "DOCUMENT_PIPELINE_MCP_WRITE_ENABLED",
            "DOCUMENT_PIPELINE_MCP_WRITE_TOKEN",
            "DOCUMENT_PIPELINE_MCP_FILE_ACCESS_ENABLED",
        ):
            os.environ.pop(key, None)


def test_effective_settings_never_exposes_plaintext(tmp_path: Path) -> None:
    write_integration_config(
        tmp_path,
        {
            "read_token": "read-secret",
            "write_token": "write-secret",
            "mcp_task_control": True,
            "mcp_file_roots": ["D:/inbox"],
        },
    )
    effective = effective_integration_settings(tmp_path)
    assert effective["read_token_set"] is True
    assert effective["write_token_set"] is True
    assert effective["task_control"] is True
    assert effective["task_read"] is False
    assert effective["template_read"] is False
    assert effective["result_read"] is False
    assert effective["data_read"] is False
    assert effective["file_roots"] == ["D:/inbox"]
    assert "read-secret" not in str(effective)
    assert "write-secret" not in str(effective)


def test_generate_key_endpoint_persists_and_returns_plaintext_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/integration/settings/keys",
            json={"kind": "read"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["kind"] == "read"
        token = payload["token"]
        assert len(token) >= 32

        config = read_integration_config(tmp_path)
        assert config["read_token"] == token

        status = client.get("/api/v1/integration/settings").json()
        assert status["read_token_set"] is True
        assert "token" not in status


def test_revoke_key_endpoint_clears(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/api/v1/integration/settings/keys", json={"kind": "write"})
        assert read_integration_config(tmp_path)["write_token"]
        response = client.post(
            "/api/v1/integration/settings/keys/revoke",
            json={"kind": "write"},
        )
        assert response.status_code == 200
        assert "write_token" not in read_integration_config(tmp_path)
        assert client.get("/api/v1/integration/settings").json()["write_token_set"] is False


def test_permissions_save_and_write_requires_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        # 没有写密钥时开启写能力被拒绝
        denied = client.post(
            "/api/v1/integration/settings/permissions",
            json={"task_control": True, "write_enabled": True, "file_access": False},
        )
        assert denied.status_code == 409

        # 生成写密钥后可开启写能力
        client.post("/api/v1/integration/settings/keys", json={"kind": "write"})
        saved = client.post(
            "/api/v1/integration/settings/permissions",
            json={
                "task_read": True,
                "template_read": True,
                "result_read": True,
                "data_read": True,
                "task_control": True,
                "write_enabled": True,
                "file_access": True,
                "file_roots": ["D:/inbox", "D:/outbox"],
            },
        )
        assert saved.status_code == 200
        config = read_integration_config(tmp_path)
        assert config["mcp_task_control"] == "1"
        assert config["mcp_task_read"] == "1"
        assert config["mcp_template_read"] == "1"
        assert config["mcp_result_read"] == "1"
        assert config["mcp_data_read"] == "1"
        assert config["mcp_write_enabled"] == "1"
        assert config["mcp_file_roots"] == os.pathsep.join(["D:/inbox", "D:/outbox"])

        status = client.get("/api/v1/integration/settings").json()
        assert status["task_control"] is True
        assert status["task_read"] is True
        assert status["template_read"] is True
        assert status["result_read"] is True
        assert status["data_read"] is True
        assert status["write_enabled"] is True
        assert status["file_access"] is True
        assert status["file_roots"] == ["D:/inbox", "D:/outbox"]


def test_mcp_config_endpoint_uses_current_runtime_paths(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/v1/integration/settings/mcp-config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"] == "development"
    server = payload["mcp_servers"]["mcpServers"]["zhiyi"]
    assert server["command"] == "uv"
    assert server["args"][-6:] == [
        "python",
        "-m",
        "document_pipeline_api.launcher",
        "mcp",
        "--data-dir",
        str(tmp_path.resolve()),
    ]
