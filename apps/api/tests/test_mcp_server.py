import asyncio
from pathlib import Path

import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.mcp_server import create_mcp_server
from document_pipeline_api.services.integration_config import write_integration_config


@pytest.mark.parametrize("change", [
    {"mcp_data_read": False},
    {"mcp_file_roots": ["D:/narrow"]},
])
def test_connected_mcp_rejects_permission_changes(tmp_path: Path, change) -> None:
    write_integration_config(tmp_path, {"mcp_data_read": True, "mcp_file_roots": ["D:/wide"]})
    server = create_mcp_server(_settings(tmp_path), data_read_enabled=True)
    asyncio.run(server.call_tool("list_data_tables", {}))
    write_integration_config(tmp_path, change)
    with pytest.raises(Exception, match="权限已变更"):
        asyncio.run(server.call_tool("list_data_tables", {}))
    # Once invalidated, restoring the old settings does not revive old closures.
    write_integration_config(tmp_path, {"mcp_data_read": True, "mcp_file_roots": ["D:/wide"]})
    with pytest.raises(Exception, match="权限已变更"):
        asyncio.run(server.call_tool("list_data_tables", {}))


def test_http_key_rotation_does_not_invalidate_mcp(tmp_path: Path) -> None:
    server = create_mcp_server(_settings(tmp_path), data_read_enabled=True)
    write_integration_config(tmp_path, {"read_token": "synthetic-rotated-token"})
    asyncio.run(server.call_tool("list_data_tables", {}))


@pytest.mark.parametrize("name,arguments", [
    ("get_data_table", {"table_id": "private"}),
    ("aggregate_data_table", {"table_id": "private"}),
    ("list_data_views", {"table_id": "private"}),
    ("get_data_view", {"table_id": "private", "view_id": "v"}),
    ("update_data_row", {"table_id": "private", "row_id": 1, "expected_version": 1, "changes": {"amount": 9}}),
    ("export_data_table", {"table_id": "private", "output_path": "unused.csv"}),
])
def test_mcp_table_scope_covers_every_data_path(tmp_path: Path, name: str, arguments: dict) -> None:
    server = create_mcp_server(_settings(tmp_path), data_read_enabled=True, write_enabled=True,
        file_access_enabled=True, allowed_file_roots=(tmp_path,), allowed_table_ids=("allowed",))
    with pytest.raises(Exception, match="授权范围"):
        asyncio.run(server.call_tool(name, arguments))
    assert not (tmp_path / "unused.csv").exists()


def test_empty_selected_scope_does_not_mean_all_tables(tmp_path: Path) -> None:
    from sqlalchemy.orm import Session
    from document_pipeline_api.db import build_engine
    from document_pipeline_api.models.data_table import DataTableRecord
    settings = _settings(tmp_path)
    server = create_mcp_server(settings, data_read_enabled=True, allowed_table_ids=())
    with Session(build_engine(settings.database_url)) as session:
        session.add(DataTableRecord(id="private", name="private", template_key="private", template_version="1", document_kind="manual"))
        session.commit()
    result = asyncio.run(server.call_tool("list_data_tables", {}))
    assert "private" not in str(result)
    with pytest.raises(Exception, match="授权范围"):
        asyncio.run(server.call_tool("get_data_table", {"table_id": "private"}))


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'mcp.db'}",
        storage_dir=tmp_path / "uploads",
    )


def _tool_names(server) -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


def test_mcp_exposes_no_business_tools_by_default(tmp_path: Path) -> None:
    server = create_mcp_server(_settings(tmp_path))

    names = _tool_names(server)
    assert names == {"get_capabilities"}
    assert "update_data_row" not in names


@pytest.mark.parametrize("marker", ["clear-data-pending.json", "clear-data-result.json"])
def test_existing_mcp_connection_refuses_tools_during_or_after_clear(tmp_path: Path, marker: str) -> None:
    server = create_mcp_server(_settings(tmp_path))
    asyncio.run(server.call_tool("get_capabilities", {}))
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / marker).write_text('{"state":"succeeded"}', encoding="utf-8")
    with pytest.raises(Exception, match="重新连接 MCP"):
        asyncio.run(server.call_tool("get_capabilities", {}))
    if marker == "clear-data-result.json":
        replacement = create_mcp_server(_settings(tmp_path))
        asyncio.run(replacement.call_tool("get_capabilities", {}))


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
        "get_task",
        "get_task_diagnostics",
        "list_templates",
        "get_task_result",
        "list_data_tables",
        "get_data_table",
        "aggregate_data_table",
        "list_data_views",
        "get_data_view",
    }


def test_mcp_write_tool_only_requires_explicit_permission(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    server = create_mcp_server(
        settings,
        write_enabled=True,
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
        )

    server = create_mcp_server(
        settings,
        task_control_enabled=True,
        file_access_enabled=True,
        allowed_file_roots=(allowed,),
    )
    names = _tool_names(server)
    assert {"control_task", "select_task_template"} <= names
    assert "create_task_from_file" in names
    assert "export_data_table" not in names
    with_export = create_mcp_server(settings, file_access_enabled=True, data_read_enabled=True, allowed_file_roots=(allowed,))
    assert "export_data_table" in _tool_names(with_export)
    assert "update_data_row" not in names


def test_cloud_planner_can_control_tasks_without_reading_results_or_rows(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(
        _settings(tmp_path),
        task_control_enabled=True,
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
