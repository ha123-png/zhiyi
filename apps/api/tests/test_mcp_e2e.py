"""模拟"外部 MCP 客户端"通过 stdio 调用本服务的端到端测试。

用户要求（2026-08-08 十次追加第 12 条）：构建模拟真实端到端测试，覆盖
"MCP 外部能不能调用"这类无法手动测试的功能。这里用官方 mcp 客户端库
（mcp.client.stdio）连接 MCP server 子进程，等价于真实外部客户端，
走完整握手（initialize → initialized → tools/list → tools/call），
验证外部客户端确实能读写本服务。
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import DataRowRecord, DataTableRecord, TaskRecord
from document_pipeline_api.schemas.extraction import DocumentExtraction, DocumentKind
from document_pipeline_api.services.data_tables import confirm_task
from image_test_data import PNG_BYTES
from fastapi.testclient import TestClient
from document_pipeline_api.services.extraction import process_task


def _mcp_params(data_dir: Path, *, write_enabled: bool = False) -> StdioServerParameters:
    env = os.environ.copy()
    env["DOCUMENT_PIPELINE_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["DOCUMENT_PIPELINE_MCP_TASK_READ_ENABLED"] = "1"
    env["DOCUMENT_PIPELINE_MCP_TEMPLATE_READ_ENABLED"] = "1"
    env["DOCUMENT_PIPELINE_MCP_RESULT_READ_ENABLED"] = "1"
    env["DOCUMENT_PIPELINE_MCP_DATA_READ_ENABLED"] = "1"
    if write_enabled:
        env["DOCUMENT_PIPELINE_MCP_WRITE_ENABLED"] = "1"
        env["DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED"] = "1"
        env["DOCUMENT_PIPELINE_MCP_FILE_ACCESS_ENABLED"] = "1"
        env["DOCUMENT_PIPELINE_MCP_FILE_ROOTS"] = str(data_dir)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "document_pipeline_api.mcp_server"],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1] / "src"),
    )


def _seed_task(session, task_id: str) -> None:
    session.add(
        TaskRecord(
            id=task_id,
            filename=f"{task_id}.png",
            content_type="image/png",
            size_bytes=1,
            sha256=f"digest-{task_id}",
            storage_path=f"{task_id}.png",
            template_mode="invoice",
            status="needs_review",
        )
    )
    session.flush()
    session.add(
        __import__("document_pipeline_api.models", fromlist=["ExtractionRecord"]).ExtractionRecord(
            task_id=task_id,
            document_kind=DocumentKind.INVOICE.value,
            model_name="test-model",
            prompt_version="test-v1",
            elapsed_seconds=1.0,
            result_json=DocumentExtraction(
                document_type="发票",
                seller_name="销售方",
                buyer_name="购买方",
                document_number="NO-1",
                document_date="2026-08-01",
                amount_before_tax=100,
                tax_amount=13,
                total_amount=113,
                items=[
                    {
                        "name": "商品A",
                        "specification": None,
                        "unit": "件",
                        "quantity": 1,
                        "unit_price": 100,
                        "amount": 100,
                        "tax_rate": None,
                        "tax_amount": None,
                    }
                ],
            ).model_dump_json(),
            validation_json="[]",
            evidence_json="[]",
        )
    )
    session.commit()


def _seed_db(
    tmp_path: Path, db_name: str, task_id: str, table_id: str, row_id_out: list[int]
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / db_name}",
        storage_dir=tmp_path / "uploads",
    )
    app = create_app(settings)
    from fastapi.testclient import TestClient

    with TestClient(app):  # startup 触发迁移建表
        with app.state.session_factory() as session:
            _seed_task(session, task_id)
            table = DataTableRecord(
                id=table_id,
                name="发票",
                template_key="invoice",
                template_version="builtin-v1",
                document_kind="invoice",
            )
            session.add(table)
            session.flush()
            row = DataRowRecord(
                table_id=table_id,
                task_id=task_id,
                item_index=1,
                row_json=__import__("json").dumps({"name": "商品A"}),
            )
            session.add(row)
            session.flush()
            row_id_out.append(row.id)
            session.commit()
            confirm_task(session, task_id, 0)
            session.add(
                TaskRecord(
                    id=f"{task_id}-queued",
                    filename="queued.png",
                    content_type="image/png",
                    size_bytes=1,
                    sha256=f"digest-{task_id}-queued",
                    storage_path="queued.png",
                    template_mode="invoice",
                    status="queued",
                )
            )
            session.commit()


def test_mcp_external_client_can_read_via_stdio(tmp_path: Path) -> None:
    """显式开启读取后，外部 MCP 客户端可读任务与数据表，但不能改行。"""
    _seed_db(tmp_path, "mcp-e2e.db", "mcp-e2e-task", "mcp-e2e-table", [])

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy2(tmp_path / "mcp-e2e.db", data_dir / "document-pipeline.db")

    asyncio.run(_read_via_stdio(data_dir))


def _parse_result(call_result) -> object:
    """从 CallToolResult 提取结构化数据（structuredContent 的 result 键或文本 JSON）。"""
    if call_result.structuredContent is not None:
        value = call_result.structuredContent.get("result")
        if value is not None and set(call_result.structuredContent) == {"result"}:
            return value
        return call_result.structuredContent
    text = call_result.content[0].text if call_result.content else "[]"
    import json

    return json.loads(text)


async def _read_via_stdio(data_dir: Path) -> None:
    params = _mcp_params(data_dir, write_enabled=False)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "list_tasks" in names
            assert "get_capabilities" in names
            assert "aggregate_data_table" in names
            assert "list_templates" in names
            assert "update_data_row" not in names
            assert "control_task" not in names

            result = await session.call_tool("list_tasks", {})
            tasks = _parse_result(result)
            assert any(task["id"] == "mcp-e2e-task" for task in tasks)

            tables = await session.call_tool("list_data_tables", {})
            table_list = _parse_result(tables)
            assert any(table["id"] == "mcp-e2e-table" for table in table_list)

            aggregate = await session.call_tool(
                "aggregate_data_table", {"table_id": "mcp-e2e-table"}
            )
            assert _parse_result(aggregate)["count"] == 1


def test_mcp_external_client_write_uses_permission_without_http_token(tmp_path: Path) -> None:
    """显式开启写入的 MCP server 暴露 update_data_row，且外部客户端可改行。"""
    row_id: list[int] = []
    _seed_db(tmp_path, "mcp-write.db", "mcp-write-task", "mcp-write-table", row_id)

    data_dir = tmp_path / "write-data"
    data_dir.mkdir()
    shutil.copy2(tmp_path / "mcp-write.db", data_dir / "document-pipeline.db")

    asyncio.run(_write_via_stdio(data_dir, row_id[0]))


async def _write_via_stdio(data_dir: Path, row_id: int) -> None:
    input_path = data_dir / "mcp-input.png"
    input_path.write_bytes(PNG_BYTES)
    params = _mcp_params(data_dir, write_enabled=True)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert "update_data_row" in tool_names
            assert "control_task" in tool_names
            assert "create_task_from_file" in tool_names

            detail_before = await session.call_tool(
                "get_data_table", {"table_id": "mcp-write-table"}
            )
            before_payload = _parse_result(detail_before)
            current_version = before_payload["rows"][0]["version"]
            assert current_version >= 1

            updated = await session.call_tool(
                "update_data_row",
                {
                    "table_id": "mcp-write-table",
                    "row_id": row_id,
                    "expected_version": current_version,
                    "changes": {"name": "外部客户端改的行"},
                },
            )
            payload = _parse_result(updated)
            assert payload["values"]["name"] == "外部客户端改的行"
            assert payload["version"] == current_version + 1

            detail = await session.call_tool("get_data_table", {"table_id": "mcp-write-table"})
            detail_payload = _parse_result(detail)
            assert detail_payload["rows"][0]["values"]["name"] == "外部客户端改的行"

            paused = await session.call_tool(
                "control_task",
                {"task_id": "mcp-write-task-queued", "action": "pause"},
            )
            assert _parse_result(paused)["status"] == "paused"

            created = await session.call_tool(
                "create_task_from_file",
                {"file_path": str(input_path), "template_id": "builtin-invoice"},
            )
            created_payload = _parse_result(created)
            assert created_payload["filename"] == "mcp-input.png"
            assert created_payload["status"] == "queued"
            # Windows refuses deletion while a source handle is leaked. MCP creates the
            # UploadFile itself, so successful import must release it before returning.
            input_path.unlink()
            assert not input_path.exists()

            escaped = await session.call_tool(
                "create_task_from_file",
                {"file_path": str(data_dir.parent / "outside.png")},
            )
            assert escaped.isError is True


def test_mcp_full_file_progress_review_table_export_chain(tmp_path: Path):
    """Real stdio transport; deterministic model keeps CI offline and reproducible."""
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'document-pipeline.db'}", storage_dir=tmp_path / "uploads")
    class Model:
        model_name = "offline-mcp-fixture"
        def complete_text(self, prompt, result_type):
            assert "合成读书会" in prompt
            return result_type.model_validate({"header": {"title": "合成读书会"}, "items": []})
    with TestClient(create_app(settings)) as api:
        template = api.post("/api/v1/templates", json={"name": "会议记录", "fields": [{"key": "title", "label": "名称"}],
            "deterministic_rules": [{"kind": "required", "field": "header.title"}]}).json()
        source = tmp_path / "meeting.txt"
        raw = "合成读书会，测试材料，无用户信息。".encode()
        source.write_bytes(raw)
        async def journey():
            async with stdio_client(_mcp_params(tmp_path, write_enabled=True)) as (read, write):
                async with ClientSession(read, write) as mcp:
                    await mcp.initialize()
                    capabilities = _parse_result(await mcp.call_tool("get_capabilities", {}))
                    assert capabilities["table_export"] is True
                    imported = await mcp.call_tool("create_task_from_file", {"file_path": str(source), "template_id": template["id"]})
                    assert not imported.isError
                    task = _parse_result(imported)
                    paused = _parse_result(await mcp.call_tool("control_task", {"task_id": task["id"], "action": "pause"}))
                    assert paused["status"] == "paused"
                    await mcp.call_tool("control_task", {"task_id": task["id"], "action": "resume"})
                    with api.app.state.session_factory() as session:
                        result = process_task(session, settings, task["id"], client=Model())
                        assert result is not None
                    status = _parse_result(await mcp.call_tool("get_task", {"task_id": task["id"]}))
                    assert status["status"] in {"needs_review", "completed"}
                    extracted = _parse_result(await mcp.call_tool("get_task_result", {"task_id": task["id"]}))
                    assert extracted["result"]["header"]["title"] == "合成读书会"
                    # Confirmation remains the existing explicit product action.
                    confirmed = api.post(f"/api/v1/tasks/{task['id']}/confirm", json={"expected_review_version": result.review_version})
                    assert confirmed.status_code == 200
                    table_id = confirmed.json()["table_id"]
                    table = _parse_result(await mcp.call_tool("get_data_table", {"table_id": table_id}))
                    row = table["rows"][0]
                    update = {"table_id": table_id, "row_id": row["id"], "expected_version": row["version"], "changes": {"title": "合成读书会（核对后）"}}
                    changed = await mcp.call_tool("update_data_row", update)
                    assert not changed.isError
                    stale = await mcp.call_tool("update_data_row", update)
                    assert stale.isError
                    output = tmp_path / "export.json"
                    args = {"table_id": table_id, "output_path": str(output), "format": "json"}
                    exported = await mcp.call_tool("export_data_table", args)
                    assert not exported.isError
                    original_export = output.read_bytes()
                    assert "合成读书会（核对后）" in original_export.decode("utf-8-sig")
                    assert (await mcp.call_tool("export_data_table", args)).isError
                    assert output.read_bytes() == original_export
                    assert (await mcp.call_tool("export_data_table", {**args, "output_path": str(tmp_path.parent / "outside.json")})).isError
                    assert source.read_bytes() == raw
        asyncio.run(journey())
