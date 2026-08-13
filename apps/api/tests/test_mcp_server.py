import asyncio
from pathlib import Path

import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.mcp_server import create_mcp_server


WRITE_TOKEN = "write-" + ("c" * 40)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'mcp.db'}",
        storage_dir=tmp_path / "uploads",
        integration_write_token=WRITE_TOKEN,
    )


def _tool_names(server) -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


def test_mcp_exposes_no_business_tools_by_default(tmp_path: Path) -> None:
    server = create_mcp_server(_settings(tmp_path))

    names = _tool_names(server)
    assert names == {"get_capabilities"}
    assert "update_data_row" not in names


def test_mcp_read_capabilities_are_independently_registered(tmp_path: Path) -> None:
    server = create_mcp_server(
        _settings(tmp_path),
        task_read_enabled=True,
        template_read_enabled=True,
        result_read_enabled=True,
        data_read_enabled=True,
    )
    assert _tool_names(server) == {
        "get_capabilities",
        "list_tasks",
        "list_templates",
        "get_task_result",
        "list_data_tables",
        "get_data_table",
        "aggregate_data_table",
        "list_data_views",
        "get_data_view",
    }


def test_mcp_write_tool_requires_explicit_matching_write_token(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(RuntimeError, match="写入密钥无效"):
        create_mcp_server(
            settings,
            write_enabled=True,
            write_token="wrong-" + ("x" * 40),
        )

    server = create_mcp_server(
        settings,
        write_enabled=True,
        write_token=WRITE_TOKEN,
    )
    assert "update_data_row" in _tool_names(server)


def test_mcp_privileged_capabilities_are_independently_registered(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    with pytest.raises(RuntimeError, match="没有配置允许目录"):
        create_mcp_server(
            settings,
            file_access_enabled=True,
            write_token=WRITE_TOKEN,
        )

    server = create_mcp_server(
        settings,
        task_control_enabled=True,
        file_access_enabled=True,
        allowed_file_roots=(allowed,),
        write_token=WRITE_TOKEN,
    )
    names = _tool_names(server)
    assert {"control_task", "select_task_template"} <= names
    assert {"create_task_from_file", "export_data_table"} <= names
    assert "update_data_row" not in names


def test_cloud_planner_can_control_tasks_without_reading_results_or_rows(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(
        _settings(tmp_path),
        task_control_enabled=True,
        write_token=WRITE_TOKEN,
        result_read_enabled=False,
        data_read_enabled=False,
    )
    names = _tool_names(server)
    assert "control_task" in names
    assert "list_tasks" not in names
    assert "list_templates" not in names
    assert "list_data_tables" not in names
    assert "get_task_result" not in names
    assert "get_data_table" not in names
    assert "aggregate_data_table" not in names
    assert "get_data_view" not in names
