from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from document_pipeline_api.schemas.templates import TemplatePresentation
from document_pipeline_api.schemas.input_scope import InputScope


class ConfirmRequest(BaseModel):
    filename: str | None = Field(default=None, max_length=512)
    expected_review_version: int = Field(ge=0)
    # 指定目标表；不传则用任务绑定的目标表，仍无则回退默认模板唯一表。
    target_table_id: str | None = None


class ConfirmationRead(BaseModel):
    task_id: str
    table_id: str
    table_name: str
    review_version: int
    row_count: int
    confirmed_at: datetime


class DataTableRead(BaseModel):
    id: str
    name: str
    template_key: str
    template_version: str
    document_kind: str
    row_count: int
    created_at: datetime


class DataTableCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    template_key: str = Field(min_length=1, max_length=128)


class TableRename(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class RowCreate(BaseModel):
    values: dict[str, object | None] = Field(default_factory=dict)
    editor: str = Field(default="local-user", min_length=1, max_length=64)


class RowsDelete(BaseModel):
    row_ids: list[int] = Field(min_length=1)


class MergeTablesRequest(BaseModel):
    table_ids: list[str] = Field(min_length=2, max_length=8)
    name: str = Field(min_length=1, max_length=128)


class DataRowRead(BaseModel):
    review_pending: bool | None = None
    input_scope: InputScope | None = None
    id: int
    task_id: str | None
    item_index: int
    version: int
    values: dict[str, object | None]
    created_at: datetime
    updated_at: datetime


class ColumnDef(BaseModel):
    key: str
    label: str
    value_type: str = "text"
    section: str | None = None
    user_defined: bool = False


class CustomColumnCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    section: str = Field(pattern="^(header|item)$")


class DataTableDetail(DataTableRead):
    presentation: TemplatePresentation = Field(default_factory=TemplatePresentation)
    columns: list[ColumnDef]
    page: int
    page_size: int
    rows: list[DataRowRead]


class DataRowUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    changes: dict[str, object | None] = Field(min_length=1)
    editor: str = Field(default="local-user", min_length=1, max_length=64)


class DataRowRevisionRead(BaseModel):
    id: int
    row_id: int
    version: int
    operation: str
    before: dict[str, object | None] | None
    after: dict[str, object | None] | None
    editor: str
    created_at: datetime


class SplitViewsRequest(BaseModel):
    field_key: str = Field(min_length=1, max_length=128)
    group_by: Literal["field", "file"] = "field"


class DataViewRead(BaseModel):
    id: str
    table_id: str
    name: str
    field_key: str
    field_value: object
    row_count: int
    created_at: datetime
    updated_at: datetime


class DataViewDetail(DataViewRead):
    presentation: TemplatePresentation = Field(default_factory=TemplatePresentation)
    source_table_name: str
    columns: list[ColumnDef]
    page: int
    page_size: int
    rows: list[DataRowRead]
