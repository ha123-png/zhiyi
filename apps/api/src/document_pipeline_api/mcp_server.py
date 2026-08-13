import os
import mimetypes
from pathlib import Path
from secrets import compare_digest

from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers, UploadFile

from document_pipeline_api.api.integration_auth import validate_integration_tokens
from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import upgrade_database
from document_pipeline_api.schemas.data_tables import DataRowUpdate
from document_pipeline_api.schemas.tasks import TaskRead
from document_pipeline_api.services.data_rows import update_data_row
from document_pipeline_api.services.data_tables import (
    get_data_table,
    list_data_tables,
)
from document_pipeline_api.services.data_views import get_data_view, list_data_views
from document_pipeline_api.services.extraction import get_extraction
from document_pipeline_api.services.integration_queries import (
    aggregate_data_table,
    export_data_table_file,
)
from document_pipeline_api.services.tasks import (
    cancel_task,
    create_task_from_upload,
    list_tasks,
    pause_task,
    resume_task,
    retry_task,
    select_task_template,
)
from document_pipeline_api.services.templates import list_templates


def create_mcp_server(
    settings: Settings,
    *,
    write_enabled: bool = False,
    write_token: str = "",
    task_control_enabled: bool = False,
    file_access_enabled: bool = False,
    allowed_file_roots: tuple[Path, ...] = (),
    task_read_enabled: bool = False,
    template_read_enabled: bool = False,
    result_read_enabled: bool = False,
    data_read_enabled: bool = False,
) -> FastMCP:
    if write_enabled or task_control_enabled or file_access_enabled:
        validate_integration_tokens(settings)
        if (
            not settings.integration_write_token
            or not write_token
            or not compare_digest(write_token, settings.integration_write_token)
        ):
            raise RuntimeError("MCP 写入已请求，但写入密钥无效。")
    resolved_roots = tuple(root.resolve() for root in allowed_file_roots)
    if file_access_enabled and not resolved_roots:
        raise RuntimeError("MCP 文件访问已启用，但没有配置允许目录。")

    engine = build_engine(settings.database_url)
    upgrade_database(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    server = FastMCP(
        "Document Pipeline",
        instructions=(
            "按用户明确授权查询或操作本地文档处理任务和结构化数据；"
            "不要把查询结果解释为原始文件中不存在的事实。"
        ),
        json_response=True,
    )

    @server.tool(name="get_capabilities")
    def mcp_get_capabilities() -> dict[str, object]:
        """返回当前 MCP 实例实际开放的能力；未授权工具不会注册。"""
        return {
            "read": [
                *(["tasks"] if task_read_enabled else []),
                *(["templates"] if template_read_enabled else []),
                *(["results"] if result_read_enabled else []),
                *(
                    ["table_metadata", "table_rows", "views", "aggregate"]
                    if data_read_enabled
                    else []
                ),
            ],
            "fact_write": write_enabled,
            "task_control": task_control_enabled,
            "file_access": file_access_enabled,
            "result_read": result_read_enabled,
            "data_read": data_read_enabled,
            "task_read": task_read_enabled,
            "template_read": template_read_enabled,
            "raw_file_read": False,
            "allowed_file_roots": [str(root) for root in resolved_roots],
        }

    if task_read_enabled:

        @server.tool(name="list_tasks")
        def mcp_list_tasks(
            status: str | None = None,
            search: str | None = None,
            template_id: str | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> list[dict[str, object]]:
            """按状态、文件名或模板筛选任务；分页返回，单页最多 500 条。"""
            if not 1 <= limit <= 500 or offset < 0:
                raise ValueError("limit 必须在 1 到 500 之间，offset 不能小于 0。")
            with session_factory() as session:
                return [
                    TaskRead.model_validate(task).model_dump(mode="json")
                    for task in list_tasks(
                        session,
                        status=status,
                        search=search,
                        template_id=template_id,
                        limit=limit,
                        offset=offset,
                    )
                ]

    if result_read_enabled:

        @server.tool(name="get_task_result")
        def mcp_get_task_result(task_id: str) -> dict[str, object]:
            """读取一个任务的结构化提取结果和校验问题。"""
            with session_factory() as session:
                return get_extraction(session, task_id).model_dump(mode="json")

    if template_read_enabled:

        @server.tool(name="list_templates")
        def mcp_list_templates(include_inactive: bool = False) -> list[dict[str, object]]:
            """列出模板、字段和规则，供模型理解可处理的数据结构。"""
            with session_factory() as session:
                return [
                    template.model_dump(mode="json")
                    for template in list_templates(session, include_inactive=include_inactive)
                ]

    if data_read_enabled:

        @server.tool(name="list_data_tables")
        def mcp_list_data_tables() -> list[dict[str, object]]:
            """列出原始数据表及其行数，不返回全部行。"""
            with session_factory() as session:
                return [table.model_dump(mode="json") for table in list_data_tables(session)]

        @server.tool(name="get_data_table")
        def mcp_get_data_table(
            table_id: str,
            page: int = 1,
            page_size: int = 100,
            search: str | None = None,
        ) -> dict[str, object]:
            """分页读取原始数据表，单页最多 500 行。"""
            _validate_page(page, page_size)
            with session_factory() as session:
                return get_data_table(
                    session,
                    table_id,
                    page=page,
                    page_size=page_size,
                    search=search,
                ).model_dump(mode="json")

        @server.tool(name="aggregate_data_table")
        def mcp_aggregate_data_table(
            table_id: str,
            value_field: str | None = None,
            group_by: str | None = None,
            search: str | None = None,
        ) -> dict[str, object]:
            """由数据库精确计数/求和/分组；不让模型把整张表读入上下文自行计算。"""
            with session_factory() as session:
                return aggregate_data_table(
                    session,
                    table_id,
                    value_field=value_field,
                    group_by=group_by,
                    search=search,
                )

        @server.tool(name="list_data_views")
        def mcp_list_data_views(table_id: str) -> list[dict[str, object]]:
            """列出原始表已有的分 Sheet 视图。"""
            with session_factory() as session:
                return [view.model_dump(mode="json") for view in list_data_views(session, table_id)]

        @server.tool(name="get_data_view")
        def mcp_get_data_view(
            table_id: str,
            view_id: str,
            page: int = 1,
            page_size: int = 100,
        ) -> dict[str, object]:
            """分页读取一个分 Sheet；返回的行与原始表是同一事实。"""
            _validate_page(page, page_size)
            with session_factory() as session:
                return get_data_view(
                    session,
                    table_id,
                    view_id,
                    page=page,
                    page_size=page_size,
                ).model_dump(mode="json")

    if write_enabled:

        @server.tool(name="update_data_row")
        def mcp_update_data_row(
            table_id: str,
            row_id: int,
            expected_version: int,
            changes: dict[str, object | None],
        ) -> dict[str, object]:
            """按期望版本修改事实行；版本冲突时拒绝覆盖。"""
            with session_factory() as session:
                return update_data_row(
                    session,
                    table_id,
                    row_id,
                    DataRowUpdate(
                        expected_version=expected_version,
                        changes=changes,
                        editor="mcp",
                    ),
                ).model_dump(mode="json")

    if task_control_enabled:

        @server.tool(name="control_task")
        def mcp_control_task(task_id: str, action: str) -> dict[str, object]:
            """暂停、恢复、重试或取消一个任务；所有转换复用产品状态机。"""
            with session_factory() as session:
                if action == "pause":
                    task = pause_task(session, task_id)
                elif action == "resume":
                    task = resume_task(session, settings, task_id)
                elif action == "retry":
                    task = retry_task(session, settings, task_id)
                elif action == "cancel":
                    task = cancel_task(session, task_id)
                else:
                    raise ValueError("action 只允许 pause、resume、retry、cancel。")
                if settings.queue_enabled:
                    from document_pipeline_api.services.queueing import enqueue_task, revoke_task
                    from document_pipeline_api.services.queue_pause import freeze_new_task_if_paused

                    if action in {"pause", "cancel"}:
                        revoke_task(task.id)
                    elif not freeze_new_task_if_paused(session, task.id):
                        enqueue_task(task.id)
                return TaskRead.model_validate(task).model_dump(mode="json")

        @server.tool(name="select_task_template")
        def mcp_select_task_template(task_id: str, template_id: str) -> dict[str, object]:
            """为待选任务选择任意当前有效模板并重新入队。"""
            with session_factory() as session:
                task = select_task_template(session, settings, task_id, template_id)
                from document_pipeline_api.services.queue_pause import freeze_new_task_if_paused

                frozen = freeze_new_task_if_paused(session, task.id)
                if settings.queue_enabled and not frozen:
                    from document_pipeline_api.services.queueing import enqueue_task

                    enqueue_task(task.id)
                return TaskRead.model_validate(task).model_dump(mode="json")

    if file_access_enabled:

        @server.tool(name="create_task_from_file")
        async def mcp_create_task_from_file(
            file_path: str,
            template_mode: str = "smart",
            template_id: str | None = None,
        ) -> dict[str, object]:
            """从用户明确允许的目录导入一个文件；不能访问目录外路径。"""
            path = _resolve_allowed_file(file_path, resolved_roots, must_exist=True)
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            handle = path.open("rb")
            upload = UploadFile(
                file=handle,
                filename=path.name,
                headers=Headers({"content-type": media_type}),
                size=path.stat().st_size,
            )
            try:
                with session_factory() as session:
                    task = await create_task_from_upload(
                        session,
                        settings,
                        upload,
                        "manual" if template_id else template_mode,
                        template_id,
                    )
                    from document_pipeline_api.services.queue_pause import freeze_new_task_if_paused

                    frozen = freeze_new_task_if_paused(session, task.id)
                    if settings.queue_enabled and not frozen:
                        from document_pipeline_api.services.queueing import enqueue_task

                        enqueue_task(task.id)
                    return TaskRead.model_validate(task).model_dump(mode="json")
            finally:
                # FastAPI normally closes request uploads; MCP constructs this UploadFile
                # itself, so it must also release the source handle on every error path.
                await upload.close()

        @server.tool(name="export_data_table")
        def mcp_export_data_table(
            table_id: str,
            output_path: str,
            format: str = "csv",
        ) -> dict[str, object]:
            """把表导出为 CSV 或 JSON 到允许目录；拒绝覆盖已有文件。"""
            if format not in {"csv", "json"}:
                raise ValueError("format 只允许 csv 或 json。")
            path = _resolve_allowed_file(output_path, resolved_roots, must_exist=False)
            if path.exists():
                raise ValueError("目标文件已存在，拒绝覆盖。")
            path.parent.mkdir(parents=True, exist_ok=True)
            with session_factory() as session:
                row_count = export_data_table_file(session, table_id, path, format)
            return {"path": str(path), "format": format, "row_count": row_count}

    return server


def main() -> None:
    settings = Settings.local()
    write_enabled = os.getenv("DOCUMENT_PIPELINE_MCP_WRITE_ENABLED", "").lower() in {
        "1",
        "true",
        "yes",
    }
    task_control_enabled = _env_enabled("DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED")
    task_read_enabled = _env_enabled("DOCUMENT_PIPELINE_MCP_TASK_READ_ENABLED")
    template_read_enabled = _env_enabled("DOCUMENT_PIPELINE_MCP_TEMPLATE_READ_ENABLED")
    file_access_enabled = _env_enabled("DOCUMENT_PIPELINE_MCP_FILE_ACCESS_ENABLED")
    result_read_enabled = _env_enabled("DOCUMENT_PIPELINE_MCP_RESULT_READ_ENABLED")
    data_read_enabled = _env_enabled("DOCUMENT_PIPELINE_MCP_DATA_READ_ENABLED")
    allowed_file_roots = tuple(
        Path(item.strip())
        for item in os.getenv("DOCUMENT_PIPELINE_MCP_FILE_ROOTS", "").split(os.pathsep)
        if item.strip()
    )
    server = create_mcp_server(
        settings,
        write_enabled=write_enabled,
        write_token=os.getenv("DOCUMENT_PIPELINE_MCP_WRITE_TOKEN", ""),
        task_control_enabled=task_control_enabled,
        file_access_enabled=file_access_enabled,
        allowed_file_roots=allowed_file_roots,
        task_read_enabled=task_read_enabled,
        template_read_enabled=template_read_enabled,
        result_read_enabled=result_read_enabled,
        data_read_enabled=data_read_enabled,
    )
    server.run(transport="stdio")


def _validate_page(page: int, page_size: int) -> None:
    if page < 1 or not 1 <= page_size <= 500:
        raise ValueError("page 必须大于等于 1，page_size 必须在 1 到 500 之间。")


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def _env_enabled_default(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes"}


def _resolve_allowed_file(
    raw_path: str,
    roots: tuple[Path, ...],
    *,
    must_exist: bool,
) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("文件路径不在 MCP 允许目录中。")
    if must_exist and (not path.is_file()):
        raise ValueError("没有找到可读取的文件。")
    return path


if __name__ == "__main__":
    main()
