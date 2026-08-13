from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
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
from document_pipeline_api.schemas.tasks import TaskRead
from document_pipeline_api.services.data_rows import update_data_row
from document_pipeline_api.services.data_tables import get_data_table, list_data_tables
from document_pipeline_api.services.data_views import get_data_view, list_data_views
from document_pipeline_api.services.extraction import get_extraction
from document_pipeline_api.services.queue_pause import freeze_new_task_if_paused
from document_pipeline_api.services.tasks import create_task_from_upload, list_tasks


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
        read=["tasks", "results", "tables", "views"],
        write=(
            ["upload_task", "update_table_row"]
            if principal.scope == "write"
            else []
        ),
    )


@router.get("/tasks", response_model=list[TaskRead])
def tasks(
    session: SessionDependency,
    _principal: ReadAccess,
) -> list[TaskRead]:
    return [TaskRead.model_validate(task) for task in list_tasks(session)]


@router.get("/tasks/{task_id}/result", response_model=ExtractionRead)
def task_result(
    task_id: str,
    session: SessionDependency,
    _principal: ReadAccess,
) -> ExtractionRead:
    return get_extraction(session, task_id)


@router.post(
    "/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_task(
    request: Request,
    session: SessionDependency,
    _principal: WriteAccess,
    file: Annotated[UploadFile, File()],
    template_mode: Annotated[str, Form()] = "smart",
    template_id: Annotated[str | None, Form()] = None,
) -> TaskRead:
    settings: Settings = request.app.state.settings
    task = await create_task_from_upload(
        session,
        settings,
        file,
        template_mode,
        template_id,
    )
    frozen = freeze_new_task_if_paused(session, task.id)
    if settings.queue_enabled and not frozen:
        from document_pipeline_api.services.queueing import enqueue_task

        enqueue_task(task.id)
    return TaskRead.model_validate(task)


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
) -> DataRowRead:
    trusted_update = DataRowUpdate(
        expected_version=body.expected_version,
        changes=body.changes,
        editor="integration-api",
    )
    return update_data_row(session, table_id, row_id, trusted_update)
