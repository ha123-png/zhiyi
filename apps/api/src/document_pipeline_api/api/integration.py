from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from document_pipeline_api.api.integration_auth import (
    IntegrationPrincipal,
    require_integration_read,
    require_integration_write,
)
from document_pipeline_api.config import Settings
from document_pipeline_api.db import get_session
from document_pipeline_api.schemas.data_tables import (
    DataRowRead,
    DataRowUpdate,
    DataTableDetail,
    DataTableRead,
    DataViewDetail,
    DataViewRead,
)
from document_pipeline_api.schemas.extraction import ExtractionRead
from document_pipeline_api.api.tasks import original_file
from document_pipeline_api.services.file_revisions import list_revisions, revision_details
from document_pipeline_api.services.integration_access import IntegrationTaskRead, integration_task, restrict_file_to_tables
from document_pipeline_api.services.data_rows import independent_history, update_data_row
from document_pipeline_api.services.data_tables import get_data_table, list_data_tables
from document_pipeline_api.services.data_views import get_data_view, list_data_views
from document_pipeline_api.services.extraction import get_extraction
from document_pipeline_api.services.queue_pause import freeze_new_task_if_paused
from document_pipeline_api.services.tasks import create_task_from_upload, list_tasks
from document_pipeline_api.services.task_presentation import pending_details_for_tasks
from document_pipeline_api.services.read_snapshot import begin_read_snapshot


from document_pipeline_api.api.numeric_body import validate_numeric_body


router = APIRouter(prefix="/integration/v1", tags=["integration"])
SessionDependency = Annotated[Session, Depends(get_session)]
ReadAccess = Annotated[IntegrationPrincipal, Depends(require_integration_read)]
WriteAccess = Annotated[IntegrationPrincipal, Depends(require_integration_write)]


class IntegrationCapabilities(BaseModel):
    scope: str
    read: list[str]
    write: list[str]


@router.get("/capabilities", response_model=IntegrationCapabilities)
def capabilities(principal: ReadAccess) -> IntegrationCapabilities:
    return IntegrationCapabilities(
        scope=principal.scope,
        read=["tasks", "results", "tables", "views",
              *(["file_revisions"] if principal.revision_read else []),
              *(["original_file"] if principal.original_read else [])],
        write=(
            ["upload_task", "update_table_row"]
            if principal.scope == "write"
            else []
        ),
    )


@router.get("/tasks", response_model=list[IntegrationTaskRead])
def tasks(
    session: SessionDependency,
    _principal: ReadAccess,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query(max_length=512)] = None,
    task_status: Annotated[str | None, Query(alias="status", max_length=120)] = None,
) -> list[IntegrationTaskRead]:
    begin_read_snapshot(session)
    tasks = list_tasks(session, limit=limit, offset=offset, search=search, status=task_status)
    pending = pending_details_for_tasks(session, tasks)
    return [integration_task(task, pending) for task in tasks]


@router.get("/tasks/{task_id}/result", response_model=ExtractionRead)
def task_result(
    task_id: str,
    session: SessionDependency,
    _principal: ReadAccess,
) -> ExtractionRead:
    return get_extraction(session, task_id)


def _authorize_file(session, task_id, principal, enabled):
    if not enabled:
        raise HTTPException(403, "此旧密钥没有这项读取权限，请在接口页重新生成。")
    if principal.table_ids is not None:
        begin_read_snapshot(session)
        restrict_file_to_tables(session, task_id, principal.table_ids)


@router.get("/tasks/{task_id}/file", response_class=FileResponse)
def task_original(task_id: str, request: Request, session: SessionDependency, principal: ReadAccess):
    _authorize_file(session, task_id, principal, principal.original_read)
    response = original_file(task_id, request, session)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/tasks/{task_id}/revisions")
def task_revisions(task_id: str, session: SessionDependency, principal: ReadAccess, page: int = 1, page_size: int = 20):
    _authorize_file(session, task_id, principal, principal.revision_read)
    return list_revisions(session, task_id, page=page, page_size=page_size)


@router.get("/tasks/{task_id}/revisions/{revision_id}")
def task_revision(task_id: str, revision_id: str, session: SessionDependency, principal: ReadAccess,
                  page: int = 1, page_size: int = 20):
    _authorize_file(session, task_id, principal, principal.revision_read)
    return revision_details(session, task_id, revision_id, page=page, page_size=page_size)


@router.get("/tables/{table_id}/revisions")
def table_revisions(table_id: str, session: SessionDependency, principal: ReadAccess,
                    row_id: Annotated[int | None, Query(ge=1)] = None,
                    page: Annotated[int, Query(ge=1)] = 1,
                    page_size: Annotated[int, Query(ge=1, le=50)] = 20):
    if not principal.revision_read:
        raise HTTPException(403, "此旧密钥没有这项读取权限，请在接口页重新生成。")
    if principal.table_ids is not None and table_id not in principal.table_ids:
        raise HTTPException(404, "授权范围内没有这张表。")
    return independent_history(session, table_id, row_id=row_id, page=page, page_size=page_size)


@router.post(
    "/tasks",
    response_model=IntegrationTaskRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_task(
    request: Request,
    session: SessionDependency,
    _principal: WriteAccess,
    file: Annotated[UploadFile, File()],
    template_mode: Annotated[str, Form()] = "smart",
    template_id: Annotated[str | None, Form()] = None,
    duplicate_policy: Annotated[Literal["reject", "allow"], Form()] = "reject",
) -> IntegrationTaskRead:
    settings: Settings = request.app.state.settings
    task = await create_task_from_upload(
        session,
        settings,
        file,
        template_mode,
        template_id,
        duplicate_policy=duplicate_policy,
    )
    frozen = freeze_new_task_if_paused(session, task.id)
    if settings.queue_enabled and not frozen:
        from document_pipeline_api.services.queueing import enqueue_task

        enqueue_task(task.id)
    return integration_task(task)


@router.get("/tables", response_model=list[DataTableRead])
def tables(
    session: SessionDependency,
    _principal: ReadAccess,
) -> list[DataTableRead]:
    return list_data_tables(session)


@router.get("/tables/{table_id}", response_model=DataTableDetail)
def table(
    table_id: str,
    session: SessionDependency,
    _principal: ReadAccess,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DataTableDetail:
    return get_data_table(session, table_id, page=page, page_size=page_size)


@router.get("/tables/{table_id}/views", response_model=list[DataViewRead])
def views(
    table_id: str,
    session: SessionDependency,
    _principal: ReadAccess,
) -> list[DataViewRead]:
    return list_data_views(session, table_id)


@router.get(
    "/tables/{table_id}/views/{view_id}",
    response_model=DataViewDetail,
)
def view(
    table_id: str,
    view_id: str,
    session: SessionDependency,
    _principal: ReadAccess,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DataViewDetail:
    return get_data_view(
        session,
        table_id,
        view_id,
        page=page,
        page_size=page_size,
    )


@router.patch("/tables/{table_id}/rows/{row_id}", response_model=DataRowRead)
def edit_row(
    table_id: str,
    row_id: int,
    body: DataRowUpdate,
    session: SessionDependency,
    _principal: WriteAccess,
    _numeric_body: Annotated[None, Depends(validate_numeric_body)],
) -> DataRowRead:
    trusted_update = DataRowUpdate(
        expected_version=body.expected_version,
        changes=body.changes,
        editor="integration-api",
    )
    return update_data_row(session, table_id, row_id, trusted_update)
