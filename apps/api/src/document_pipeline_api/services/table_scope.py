"""Explicit export selections; never infer a whole table from an empty selection."""
from dataclasses import dataclass

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from document_pipeline_api.models import DataRowRecord, DataTableRecord, DataViewRecord, FileCurrentDataRecord
from document_pipeline_api.services.current_data import row_search_predicate
from document_pipeline_api.services.read_snapshot import begin_read_snapshot


class TableExportScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view_id: str | None = None
    search: str = Field(default="", max_length=128)
    row_ids: list[int] | None = Field(default=None, max_length=2000)
    task_ids: list[str] | None = Field(default=None, max_length=2000)
    source_keys: list[str] | None = Field(default=None, max_length=50)


@dataclass
class ExportSelection:
    table: DataTableRecord
    predicates: list
    files: object
    suffix: str
    row_count: int
    file_count: int

    def headers(self):
        return {"X-Zhiyi-Row-Count": str(self.row_count), "X-Zhiyi-File-Count": str(self.file_count)}


def prepare_export_selection(session, table_id, scope=None):
    begin_read_snapshot(session)
    scope = scope or TableExportScope()
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(404, "没有找到这个数据表。")
    predicates = [DataRowRecord.table_id == table_id]
    parts = []
    from document_pipeline_api.services.data_groups import source_identity_expression, validate_source_keys
    keys = validate_source_keys(session, table_id, scope.source_keys)
    if keys is not None:
        predicates.append(source_identity_expression().in_(keys))
        parts.append(f"选中{len(keys)}个来源")
    if table.collection_kind == "files" and (scope.row_ids is not None or scope.view_id):
        raise HTTPException(422, "自主提取按文件导出，请选择文件，不使用明细行或统一字段分组范围。")
    if scope.view_id:
        from document_pipeline_api.services.data_views import _view_predicate
        view = session.get(DataViewRecord, scope.view_id)
        if view is None or view.table_id != table_id:
            raise HTTPException(409, "分组已变化，请重新选择。")
        predicates.append(_view_predicate(view))
        parts.append(view.name)
    if scope.row_ids is not None:
        identifiers = set(scope.row_ids)
        found = set(session.scalars(select(DataRowRecord.id).where(
            DataRowRecord.table_id == table_id, DataRowRecord.id.in_(identifiers))))
        if found != identifiers:
            raise HTTPException(409, "选中明细已变化或不属于当前表，请刷新后重新选择。")
        predicates.append(DataRowRecord.id.in_(identifiers))
    if scope.task_ids is not None:
        identifiers = set(scope.task_ids)
        found = set(session.scalars(select(FileCurrentDataRecord.task_id).where(
            FileCurrentDataRecord.table_id == table_id, FileCurrentDataRecord.has_data.is_(True),
            FileCurrentDataRecord.task_id.in_(identifiers))))
        if found != identifiers:
            raise HTTPException(409, "选中文件已变化或不属于当前表，请刷新后重新选择。")
        predicates.append(DataRowRecord.task_id.in_(identifiers))
    search = scope.search.strip()
    files = select(FileCurrentDataRecord).where(
        FileCurrentDataRecord.table_id == table_id, FileCurrentDataRecord.has_data.is_(True))
    if scope.task_ids is not None:
        files = files.where(FileCurrentDataRecord.task_id.in_(scope.task_ids))
    if keys is not None:
        files = files.where(FileCurrentDataRecord.task_id.in_(select(DataRowRecord.task_id).where(
            DataRowRecord.table_id == table_id, source_identity_expression().in_(keys))))
    if search:
        parts.append("搜索结果")
        if table.collection_kind == "files":
            # The file-card search selects files, not only the matching details.
            files = files.where(select(1).select_from(DataRowRecord).where(
                DataRowRecord.task_id == FileCurrentDataRecord.task_id, row_search_predicate(search),
            ).correlate(FileCurrentDataRecord).exists())
        else:
            predicates.append(row_search_predicate(search))
    if table.collection_kind == "files":
        predicates.append(DataRowRecord.task_id.in_(files.with_only_columns(FileCurrentDataRecord.task_id)))
    else:
        files = files.where(FileCurrentDataRecord.task_id.in_(select(DataRowRecord.task_id).where(*predicates)))
    row_count = session.scalar(select(func.count()).select_from(DataRowRecord).where(*predicates)) or 0
    file_count = session.scalar(select(func.count()).select_from(files.subquery())) or 0
    if scope.row_ids is not None:
        parts.append(f"选中{row_count}条")
    elif scope.task_ids is not None:
        parts.append(f"选中{file_count}份")
    return ExportSelection(table, predicates, files.order_by(FileCurrentDataRecord.task_id),
                           ("-" + "-".join(parts)) if parts else "", row_count, file_count)
