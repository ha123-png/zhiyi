from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from document_pipeline_api.db import get_session
from document_pipeline_api.config import Settings
from document_pipeline_api.models import DataTableRecord
from document_pipeline_api.public_errors import public_error_message
from document_pipeline_api.schemas.data_tables import (
    DataRowRead,
    DataRowRevisionRead,
    DataRowUpdate,
    DataTableCreate,
    DataTableDetail,
    DataTableRead,
    DataViewDetail,
    DataViewRead,
    ColumnDef,
    CustomColumnCreate,
    MergeTablesRequest,
    RowCreate,
    RowsDelete,
    SplitViewsRequest,
    TableRename,
)
from document_pipeline_api.services.data_rows import (
    list_row_revisions,
    update_data_row,
)
from document_pipeline_api.services.data_tables import (
    add_row,
    create_table,
    delete_rows,
    delete_table,
    export_data_table,
    export_data_table_views,
    export_data_table_csv,
    export_data_table_json,
    get_data_table,
    import_table_rows,
    ImportFileError,
    list_data_tables,
    merge_tables,
    parse_import_rows,
    rename_table,
    _table_read,
    add_custom_column,
    create_table_from_import,
    delete_custom_column,
)
from document_pipeline_api.services.data_views import (
    create_split_views,
    get_data_view,
    list_data_views,
)


router = APIRouter(prefix="/tables", tags=["tables"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[DataTableRead])
def tables(session: SessionDependency) -> list[DataTableRead]:
    return list_data_tables(session)


@router.post("", response_model=DataTableRead, status_code=201)
def create_new_table(
    request: DataTableCreate,
    session: SessionDependency,
) -> DataTableRead:
    return create_table(session, request.name, request.template_key)


@router.post("/merge", response_model=DataTableRead, status_code=201)
def merge(
    request: MergeTablesRequest,
    session: SessionDependency,
) -> DataTableRead:
    return merge_tables(session, request.table_ids, request.name)


@router.post("/import-new", response_model=DataTableRead, status_code=201)
def import_new_table(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    session: SessionDependency,
) -> DataTableRead:
    filename = file.filename or "导入数据.xlsx"
    if not filename.lower().endswith((".xlsx", ".csv")):
        raise HTTPException(status_code=422, detail="只支持 .xlsx 或 .csv 文件。")
    settings: Settings = request.app.state.settings
    content = file.file.read(settings.max_import_bytes + 1)
    if len(content) > settings.max_import_bytes:
        raise HTTPException(status_code=413, detail="导入文件超过当前允许的大小。")
    try:
        rows = parse_import_rows(content, filename, max_rows=settings.max_import_rows, max_columns=settings.max_import_columns, max_uncompressed_bytes=settings.max_import_uncompressed_bytes)
    except ImportFileError as error:
        raise HTTPException(
            status_code=422,
            detail=public_error_message(error, "无法读取导入文件，请确认文件未损坏且表头结构正确。"),
        ) from error
    if not rows:
        raise HTTPException(status_code=422, detail="导入文件没有可用数据行。")
    table = create_table_from_import(session, Path(filename).stem[:128], rows)
    return _table_read(session, table)


@router.get("/{table_id}", response_model=DataTableDetail)
def table(
    table_id: str,
    session: SessionDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
    search: Annotated[str | None, Query(max_length=128)] = None,
) -> DataTableDetail:
    return get_data_table(
        session,
        table_id,
        page=page,
        page_size=page_size,
        search=search,
    )


@router.patch("/{table_id}", response_model=DataTableRead)
def rename(
    table_id: str,
    request: TableRename,
    session: SessionDependency,
) -> DataTableRead:
    return rename_table(session, table_id, request.name)


@router.delete("/{table_id}", status_code=204)
def remove_table(table_id: str, session: SessionDependency) -> None:
    delete_table(session, table_id)


@router.post("/{table_id}/columns", response_model=ColumnDef, status_code=201)
def create_custom_column(
    table_id: str,
    request: CustomColumnCreate,
    session: SessionDependency,
) -> ColumnDef:
    return add_custom_column(session, table_id, request.label, request.section)


@router.delete("/{table_id}/columns/{column_key}", status_code=204)
def remove_custom_column(
    table_id: str,
    column_key: str,
    session: SessionDependency,
) -> None:
    delete_custom_column(session, table_id, column_key)


@router.patch("/{table_id}/rows/{row_id}", response_model=DataRowRead)
def edit_row(
    table_id: str,
    row_id: int,
    request: DataRowUpdate,
    session: SessionDependency,
) -> DataRowRead:
    return update_data_row(session, table_id, row_id, request)


@router.post("/{table_id}/rows", response_model=DataRowRead, status_code=201)
def create_row(
    table_id: str,
    request: RowCreate,
    session: SessionDependency,
) -> DataRowRead:
    return add_row(session, table_id, request.values, request.editor)


@router.post("/{table_id}/import", response_model=DataTableRead)
def import_table(
    table_id: str,
    request: Request,
    file: Annotated[UploadFile, File(...)],
    session: SessionDependency,
) -> DataTableRead:
    filename = file.filename or "import.xlsx"
    if not filename.lower().endswith((".xlsx", ".csv")):
        raise HTTPException(
            status_code=422,
            detail="只支持 .xlsx 或 .csv 文件。",
        )
    settings: Settings = request.app.state.settings
    content = file.file.read(settings.max_import_bytes + 1)
    if len(content) > settings.max_import_bytes:
        raise HTTPException(status_code=413, detail="导入文件超过当前允许的大小。")
    try:
        rows = parse_import_rows(
            content,
            filename,
            max_rows=settings.max_import_rows,
            max_columns=settings.max_import_columns,
            max_uncompressed_bytes=settings.max_import_uncompressed_bytes,
        )
    except ImportFileError as error:
        raise HTTPException(
            status_code=422,
            detail=public_error_message(error, "无法读取导入文件，请确认文件未损坏且表头结构正确。"),
        ) from error
    import_table_rows(session, table_id, rows)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    return _table_read(session, table)


@router.delete("/{table_id}/rows", status_code=204)
def remove_rows(
    table_id: str,
    request: RowsDelete,
    session: SessionDependency,
) -> None:
    delete_rows(session, table_id, request.row_ids)


@router.get(
    "/{table_id}/rows/{row_id}/revisions",
    response_model=list[DataRowRevisionRead],
)
def row_revisions(
    table_id: str,
    row_id: int,
    session: SessionDependency,
) -> list[DataRowRevisionRead]:
    return list_row_revisions(session, table_id, row_id)


@router.post("/{table_id}/split", response_model=list[DataViewRead])
def split_table(
    table_id: str,
    request: SplitViewsRequest,
    session: SessionDependency,
) -> list[DataViewRead]:
    return create_split_views(session, table_id, request.field_key, by_file=request.group_by == "file")


@router.get("/{table_id}/views", response_model=list[DataViewRead])
def views(table_id: str, session: SessionDependency) -> list[DataViewRead]:
    return list_data_views(session, table_id)


@router.get("/{table_id}/views/{view_id}", response_model=DataViewDetail)
def view(
    table_id: str,
    view_id: str,
    session: SessionDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
    search: str | None = None,
) -> DataViewDetail:
    return get_data_view(
        session,
        table_id,
        view_id,
        page=page,
        page_size=page_size,
        search=search,
    )


@router.get("/{table_id}/export.xlsx", response_class=StreamingResponse)
def export(table_id: str, session: SessionDependency) -> StreamingResponse:
    return export_data_table(session, table_id)


@router.get("/{table_id}/export-views.xlsx", response_class=StreamingResponse)
def export_views(table_id: str, session: SessionDependency) -> StreamingResponse:
    return export_data_table_views(session, table_id)


@router.get("/{table_id}/export.csv", response_class=StreamingResponse)
def export_csv(table_id: str, session: SessionDependency) -> StreamingResponse:
    return export_data_table_csv(session, table_id)


@router.get("/{table_id}/export.json", response_class=StreamingResponse)
def export_json(table_id: str, session: SessionDependency) -> StreamingResponse:
    return export_data_table_json(session, table_id)
