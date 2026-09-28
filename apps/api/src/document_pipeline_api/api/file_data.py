"""File revisions are read-only history; editing sessions are an internal boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field

from document_pipeline_api.db import get_session
from document_pipeline_api.services.current_data import create_edit_session, seal_edit_session
from document_pipeline_api.api.data_tables import ExportScopeDependency


router = APIRouter(tags=["file-data"])
SessionDependency = Annotated[Session, Depends(get_session)]


class CurrentFilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    header_changes: dict = Field(default_factory=dict)
    detail_changes: dict[int, dict] = Field(default_factory=dict)
    deleted_ids: list[int] = Field(default_factory=list, max_length=1000)
    clear: bool = False
    edit_session_id: str | None = Field(default=None, max_length=36)


class SourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=2048)
    version: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceClear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[SourceSelection] = Field(min_length=1, max_length=50)
    edit_session_id: str | None = Field(default=None, max_length=36)


@router.patch("/tasks/{task_id}/current")
def edit_current_file(task_id: str, body: CurrentFilePatch, session: SessionDependency):
    from document_pipeline_api.models import DataRowRecord
    from document_pipeline_api.services.current_data import revise_current
    if body.clear and (body.header_changes or body.detail_changes or body.deleted_ids):
        raise HTTPException(422, "清空文件数据不能同时提交其他修改。")
    deleted = list(session.scalars(select(DataRowRecord.id).where(DataRowRecord.task_id == task_id))) if body.clear else body.deleted_ids
    current = revise_current(session, task_id, expected_version=body.expected_version,
        header_changes=body.header_changes, detail_changes=body.detail_changes, deleted_ids=deleted,
        edit_session_id=body.edit_session_id, operation="clear" if body.clear else "edit")
    session.commit()
    return {"task_id": task_id, "version": current.current_version, "has_data": current.has_data}


@router.get("/tables/{table_id}/files")
def current_files(table_id: str, session: SessionDependency,
                  page: Annotated[int, Query(ge=1)] = 1,
                  page_size: Annotated[int, Query(ge=1, le=50)] = 12,
                  search: str = ""):
    from document_pipeline_api.services.file_collections import list_files
    return list_files(session, table_id, page=page, page_size=page_size, search=search)


@router.get("/tasks/{task_id}/current-details")
def current_details(task_id: str, session: SessionDependency,
                    page: Annotated[int, Query(ge=1)] = 1,
                    page_size: Annotated[int, Query(ge=1, le=50)] = 50):
    from document_pipeline_api.services.file_collections import file_details
    return file_details(session, task_id, page=page, page_size=page_size)


@router.get("/tables/{table_id}/source-cards")
def current_source_cards(table_id: str, session: SessionDependency, scope: ExportScopeDependency,
                         page: Annotated[int, Query(ge=1)] = 1,
                         page_size: Annotated[int, Query(ge=1, le=24)] = 6):
    from document_pipeline_api.services.source_cards import list_source_cards
    return list_source_cards(session, table_id, scope=scope, page=page, page_size=page_size)


@router.get("/tables/{table_id}/source-details")
def current_source_details(table_id: str, session: SessionDependency, scope: ExportScopeDependency,
                           source_key: Annotated[str, Query(max_length=2048)],
                           page: Annotated[int, Query(ge=1)] = 1,
                           page_size: Annotated[int, Query(ge=1, le=50)] = 20):
    from document_pipeline_api.services.source_cards import source_card_details
    return source_card_details(session, table_id, source_key, scope=scope, page=page, page_size=page_size)


@router.post("/tables/edit-sessions", status_code=201)
def begin_editing(request: Request, session: SessionDependency):
    editing = create_edit_session(session, owner=request.app.state.edit_session_owner)
    session.commit()
    return {"id": editing.id}


@router.post("/tables/{table_id}/source-cards/clear")
def clear_sources(table_id: str, body: SourceClear, session: SessionDependency, scope: ExportScopeDependency):
    from document_pipeline_api.services.source_cards import clear_source_cards
    return clear_source_cards(session, table_id, [source.model_dump() for source in body.sources],
                              scope=scope, edit_session_id=body.edit_session_id)


@router.post("/tables/edit-sessions/{session_id}/seal", status_code=204)
def finish_editing(session_id: str, session: SessionDependency):
    seal_edit_session(session, session_id)
    session.commit()


@router.get("/tasks/{task_id}/revisions")
def file_revisions(task_id: str, session: SessionDependency,
                   page: Annotated[int, Query(ge=1)] = 1,
                   page_size: Annotated[int, Query(ge=1, le=50)] = 20):
    from document_pipeline_api.services.file_revisions import list_revisions
    return list_revisions(session, task_id, page=page, page_size=page_size)


@router.get("/tasks/{task_id}/revisions/{revision_id}")
def file_revision_details(task_id: str, revision_id: str, session: SessionDependency,
                          page: Annotated[int, Query(ge=1)] = 1,
                          page_size: Annotated[int, Query(ge=1, le=50)] = 20):
    from document_pipeline_api.services.file_revisions import revision_details
    return revision_details(session, task_id, revision_id, page=page, page_size=page_size)
