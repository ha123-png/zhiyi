import os
import mimetypes
from functools import wraps
from inspect import iscoroutinefunction, signature
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image
from fastapi import HTTPException
from base64 import b64decode
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from starlette.datastructures import Headers, UploadFile

from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import upgrade_database
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.model_diagnostics import safe_diagnostic
from document_pipeline_api.schemas.data_tables import DataRowUpdate
from document_pipeline_api.services.integration_access import integration_task, restrict_file_to_tables
from document_pipeline_api.services.file_revisions import list_revisions, revision_details
from document_pipeline_api.services.data_rows import independent_history, update_data_row
from document_pipeline_api.services.data_tables import (
    get_data_table,
    list_data_tables,
)
from document_pipeline_api.services.data_views import get_data_view, list_data_views
from document_pipeline_api.services.task_result_reads import read_task_result_page, read_task_result_value
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
from document_pipeline_api.services.task_presentation import pending_details_for_tasks
from document_pipeline_api.services.read_snapshot import begin_read_snapshot


def create_mcp_server(
    settings: Settings,
    *,
    write_enabled: bool = False,
    task_control_enabled: bool = False,
    file_access_enabled: bool = False,
    allowed_file_roots: tuple[Path, ...] = (),
    task_read_enabled: bool = False,
    template_read_enabled: bool = False,
    result_read_enabled: bool = False,
    revision_read_enabled: bool = False,
    original_read_enabled: bool = False,
    data_read_enabled: bool = False,
    allowed_table_ids: tuple[str, ...] | None = None,
) -> FastMCP:
    resolved_roots = tuple(root.resolve() for root in allowed_file_roots)
    if file_access_enabled and not resolved_roots:
        raise RuntimeError("MCP 文件访问已启用，但没有配置允许目录。")

    from document_pipeline_api.services.dataset_identity import dataset_generation, require_dataset_available
    require_dataset_available(settings.storage_dir.parent)
    # MCP may be a long-lived stdio child on Windows. Return every SQLite
    # connection to the OS when a tool's session closes so an offline restore
    # can replace the database while this client remains connected.
    engine = build_engine(settings.database_url, poolclass=NullPool)
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

    from document_pipeline_api.services.clear_data import clear_journal_path
    from document_pipeline_api.services.file_operation_lock import file_operation_lock
    generation_path = settings.storage_dir.parent / "runtime" / "clear-data-result.json"

    def generation():
        try:
            return generation_path.read_bytes()
        except FileNotFoundError:
            return b""

    initial_generation = generation()
    initial_dataset = dataset_generation(settings.storage_dir.parent)

    from document_pipeline_api.services.integration_config import read_integration_config

    def permission_snapshot():
        # HTTP key rotation is independent of MCP. A changed MCP policy invalidates
        # the connection, so its old registered closures can never retain access.
        config = read_integration_config(settings.storage_dir.parent)
        return {key: value for key, value in config.items() if key.startswith("mcp_")}

    initial_permissions = permission_snapshot()
    permissions_invalidated = False

    def check_access():
        nonlocal permissions_invalidated
        from document_pipeline_api.business_backup import restore_journal_path
        if restore_journal_path(settings.storage_dir.parent).exists() or dataset_generation(settings.storage_dir.parent) != initial_dataset:
            engine.dispose()
            raise ValueError("本地数据已恢复、清除或正在维护，请重新连接 MCP 并确认权限。")
        if clear_journal_path(settings).exists() or generation() != initial_generation:
            raise ValueError("本地数据已清除或正在清除，请重新连接 MCP 并确认权限。")
        permissions_invalidated = permissions_invalidated or permission_snapshot() != initial_permissions
        if permissions_invalidated:
            raise ValueError("MCP 权限已变更，本次操作未执行。请重新连接 MCP 使用最新权限。")

    def guarded_tool(*, name):
        def register(function):
            def check_table(args, kwargs):
                if allowed_table_ids is None:
                    return
                table_id = signature(function).bind(*args, **kwargs).arguments.get("table_id")
                if table_id is not None and table_id not in allowed_table_ids:
                    raise ValueError("该数据表不在 MCP 授权范围内。")
            if iscoroutinefunction(function):
                @wraps(function)
                async def guarded_async(*args, **kwargs):
                    with file_operation_lock(settings.storage_dir):
                        check_access()
                        check_table(args, kwargs)
                        return await function(*args, **kwargs)
                return server.tool(name=name)(guarded_async)
            @wraps(function)
            def guarded(*args, **kwargs):
                with file_operation_lock(settings.storage_dir):
                    check_access()
                    check_table(args, kwargs)
                    return function(*args, **kwargs)
            return server.tool(name=name)(guarded)
        return register

    def check_file(session, task_id):
        begin_read_snapshot(session)
        try:
            restrict_file_to_tables(session, task_id, allowed_table_ids)
        except HTTPException as error:
            raise ValueError(error.detail) from None

    @guarded_tool(name="get_capabilities")
    def mcp_get_capabilities() -> dict[str, object]:
        """返回当前 MCP 实例实际开放的能力；未授权工具不会注册。"""
        return {
            "read": [
                *(["tasks"] if task_read_enabled else []),
                *(["templates"] if template_read_enabled else []),
                *(["results"] if result_read_enabled else []),
                *(["file_revisions"] if revision_read_enabled else []),
                *(["original_pages"] if original_read_enabled else []),
                *(
                    ["table_metadata", "table_rows", "views", "aggregate"]
                    if data_read_enabled
                    else []
                ),
            ],
            "fact_write": write_enabled,
            "task_control": task_control_enabled,
            "file_access": file_access_enabled,
            "table_export": file_access_enabled and data_read_enabled,
            "result_read": result_read_enabled,
            "data_read": data_read_enabled,
            "task_read": task_read_enabled,
            "template_read": template_read_enabled,
            "raw_file_read": False,
            "allowed_file_roots": [str(root) for root in resolved_roots],
            "data_scope": "all" if allowed_table_ids is None else "selected",
            "allowed_table_ids": None if allowed_table_ids is None else list(allowed_table_ids),
        }

    if task_read_enabled:

        @guarded_tool(name="get_task")
        def mcp_get_task(task_id: str) -> dict[str, object]:
            """按导入返回的任务 ID 查询进度与待处理事项，无需反复扫描任务列表。"""
            with session_factory() as session:
                begin_read_snapshot(session)
                task = session.get(TaskRecord, task_id)
                if task is None:
                    raise ValueError("任务不存在。")
                return integration_task(task).model_dump(mode="json")

        @guarded_tool(name="get_task_diagnostics")
        def mcp_get_task_diagnostics(task_id: str) -> dict[str, object]:
            """按需读取当前处理尝试的有界失败诊断，已遮蔽常见凭证与路径。"""
            with session_factory() as session:
                task = session.get(TaskRecord, task_id)
                if task is None:
                    raise ValueError("任务不存在。")
                return {"code": task.failure_code, "attempt": task.attempt_count,
                        "detail": safe_diagnostic(task.failure_detail or task.failure_message or "当前任务没有失败诊断。")}

        @guarded_tool(name="list_tasks")
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
                begin_read_snapshot(session)
                tasks = list_tasks(
                    session,
                    status=status,
                    search=search,
                    template_id=template_id,
                    limit=limit,
                    offset=offset,
                )
                pending = pending_details_for_tasks(session, tasks)
                return [
                    integration_task(task, pending).model_dump(mode="json")
                    for task in tasks
                ]

    if result_read_enabled:

        @guarded_tool(name="get_task_result")
        def mcp_get_task_result(
            task_id: str, page: int = 1, page_size: int = 3, field_page: int = 1,
            expected_version: int | None = None,
        ) -> dict[str, object]:
            """只读当前事实；默认3条、最多20条明细和12个字段，长值显式标记截断。用 next_page/next_field_page/next_issue_page 继续，后续传 expected_version=review_version，避免混用版本。不夹带初次结果。"""
            with session_factory() as session:
                check_file(session, task_id)
                return read_task_result_page(session, task_id, page=page, page_size=page_size,
                                             field_page=field_page, expected_version=expected_version)

        @guarded_tool(name="get_task_evidence")
        def mcp_get_task_evidence(task_id: str, page: int = 1, page_size: int = 3, field_page: int = 1) -> dict[str, object]:
            """明确需要核对时才读初次提取证据，非当前事实、非原文件；默认3条明细、12个字段，可分页。用户修订和删除不改变此证据。"""
            with session_factory() as session:
                check_file(session, task_id)
                return read_task_result_page(session, task_id, view="initial", page=page,
                                             page_size=page_size, field_page=field_page)

        @guarded_tool(name="get_task_result_value")
        def mcp_get_task_result_value(
            task_id: str, path: list[str | int], view: Literal["current", "initial"] = "current",
            offset: int = 0, limit: int = 2000, expected_version: int | None = None,
        ) -> dict[str, object]:
            """按需读单字段完整值，文本每次最多2000字符；path 使用返回的 truncated_paths，或 [header,key]/[items,从0开始的全文件位置,key]。校验字段用 [validation_issues,位置,key]。current 必须传 expected_version；initial 是历史证据。"""
            with session_factory() as session:
                check_file(session, task_id)
                return read_task_result_value(session, task_id, path, view=view, offset=offset,
                                              limit=limit, expected_version=expected_version)

    if revision_read_enabled:
        @guarded_tool(name="get_file_revisions")
        def mcp_file_revisions(task_id: str, revision_id: str | None = None,
                               page: int = 1, page_size: int = 20) -> dict[str, object]:
            """读取授权文件的修订目录。提供目录中的 revision_id 可查看对应版本的前后值。"""
            with session_factory() as session:
                check_file(session, task_id)
                try:
                    if revision_id is None:
                        return list_revisions(session, task_id, page=page, page_size=page_size)
                    return revision_details(session, task_id, revision_id, page=page, page_size=page_size)
                except HTTPException as error:
                    raise ValueError(error.detail) from None

        @guarded_tool(name="get_independent_row_revisions")
        def mcp_row_revisions(table_id: str, row_id: int | None = None,
                              page: int = 1, page_size: int = 20) -> dict[str, object]:
            """读取授权表中导入或手工记录的修订。可按行查询。"""
            if page < 1 or not 1 <= page_size <= 50 or (row_id is not None and row_id < 1):
                raise ValueError("页码和行号须为正数，每页最多读取 50 条。")
            with session_factory() as session:
                try:
                    return independent_history(session, table_id, row_id=row_id, page=page, page_size=page_size)
                except HTTPException as error:
                    raise ValueError(error.detail) from None

    if original_read_enabled:
        @guarded_tool(name="read_original_page")
        def mcp_read_original_page(task_id: str, page: int = 1, offset: int = 0, vision: bool = False):
            """读取授权原件的一页或一段文字。图片和扫描件可显式选 vision；返回单页图像。"""
            from document_pipeline_api.services.assistant_tools import OriginalPage
            from document_pipeline_api.services.assistant_originals import read_original

            body = OriginalPage(task_id=task_id, page=page, offset=offset, vision=vision)
            with session_factory() as session:
                check_file(session, task_id)
                try:
                    result = read_original(session, settings, body)
                except HTTPException as error:
                    raise ValueError(error.detail) from None
                except (OSError, ValueError):
                    raise ValueError("无法读取此原件。请在知意中查看原件状态。") from None
            image = result.pop("_image", None)
            if image:
                return [result, Image(data=b64decode(image.split(",", 1)[1]), format="jpeg")]
            return result

    if template_read_enabled:

        @guarded_tool(name="list_templates")
        def mcp_list_templates(include_inactive: bool = False) -> list[dict[str, object]]:
            """列出模板、字段和规则，供模型理解可处理的数据结构。"""
            with session_factory() as session:
                return [
                    template.model_dump(mode="json")
                    for template in list_templates(session, include_inactive=include_inactive)
                ]

    if data_read_enabled:

        @guarded_tool(name="list_data_tables")
        def mcp_list_data_tables() -> list[dict[str, object]]:
            """列出原始数据表及其行数，不返回全部行。"""
            with session_factory() as session:
                return [table.model_dump(mode="json") for table in list_data_tables(session)
                        if allowed_table_ids is None or table.id in allowed_table_ids]

        @guarded_tool(name="get_data_table")
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

        @guarded_tool(name="aggregate_data_table")
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

        @guarded_tool(name="list_data_views")
        def mcp_list_data_views(table_id: str) -> list[dict[str, object]]:
            """列出原始表已有的分 Sheet 视图。"""
            with session_factory() as session:
                return [view.model_dump(mode="json") for view in list_data_views(session, table_id)]

        @guarded_tool(name="get_data_view")
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

        @guarded_tool(name="update_data_row")
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

        @guarded_tool(name="control_task")
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
                    elif task.status == "queued" and not freeze_new_task_if_paused(session, task.id):
                        enqueue_task(task.id)
                return integration_task(task).model_dump(mode="json")

        @guarded_tool(name="select_task_template")
        def mcp_select_task_template(task_id: str, template_id: str) -> dict[str, object]:
            """为待选任务选择任意当前有效模板并重新入队。"""
            with session_factory() as session:
                task = select_task_template(session, settings, task_id, template_id)
                from document_pipeline_api.services.queue_pause import freeze_new_task_if_paused

                frozen = freeze_new_task_if_paused(session, task.id)
                if settings.queue_enabled and not frozen:
                    from document_pipeline_api.services.queueing import enqueue_task

                    enqueue_task(task.id)
                return integration_task(task).model_dump(mode="json")

    if file_access_enabled:

        @guarded_tool(name="create_task_from_file")
        async def mcp_create_task_from_file(
            file_path: str,
            template_mode: str = "smart",
            template_id: str | None = None,
            duplicate_policy: str = "reject",
        ) -> dict[str, object]:
            """从允许目录导入。重复默认拒绝；用户确认再次提取后传 duplicate_policy=allow。"""
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
                        duplicate_policy=duplicate_policy,
                    )
                    from document_pipeline_api.services.queue_pause import freeze_new_task_if_paused

                    frozen = freeze_new_task_if_paused(session, task.id)
                    if settings.queue_enabled and not frozen:
                        from document_pipeline_api.services.queueing import enqueue_task

                        enqueue_task(task.id)
                    return integration_task(task).model_dump(mode="json")
            finally:
                # FastAPI normally closes request uploads; MCP constructs this UploadFile
                # itself, so it must also release the source handle on every error path.
                await upload.close()

    if file_access_enabled and data_read_enabled:

        @guarded_tool(name="export_data_table")
        def mcp_export_data_table(
            table_id: str,
            output_path: str,
            format: str = "csv",
        ) -> dict[str, object]:
            """需要数据读取和文件访问权限，把表导出到允许目录；拒绝覆盖已有文件。"""
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
    from document_pipeline_api.services.integration_config import apply_integration_config, integration_config_path
    if integration_config_path(settings.storage_dir.parent).exists():
        apply_integration_config(settings.storage_dir.parent)
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
        task_control_enabled=task_control_enabled,
        file_access_enabled=file_access_enabled,
        allowed_file_roots=allowed_file_roots,
        task_read_enabled=task_read_enabled,
        template_read_enabled=template_read_enabled,
        result_read_enabled=result_read_enabled,
        revision_read_enabled=_env_enabled("DOCUMENT_PIPELINE_MCP_REVISION_READ_ENABLED"),
        original_read_enabled=_env_enabled("DOCUMENT_PIPELINE_MCP_ORIGINAL_READ_ENABLED"),
        data_read_enabled=data_read_enabled,
        allowed_table_ids=(tuple(item for item in os.getenv("DOCUMENT_PIPELINE_MCP_TABLE_IDS", "").split(os.pathsep) if item)
                           if os.getenv("DOCUMENT_PIPELINE_MCP_DATA_SCOPE") == "selected" else None),
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
