from io import BytesIO, StringIO
from itertools import groupby
import csv
import json
import re
from tempfile import SpooledTemporaryFile
import zipfile
from collections.abc import Iterable, Sequence
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from starlette.background import BackgroundTask
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from document_pipeline_api.services.current_data import row_values, row_values_expression, update_current_schema
from document_pipeline_api.services.cell_values import import_cell, infer_import_type
from document_pipeline_api.services.excel_values import safe_excel_value
from document_pipeline_api.services.table_scope import prepare_export_selection
from document_pipeline_api.services.merged_tables import (
    export_records, merge_columns, merge_order, merged_values, require_writable, table_membership,
)
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import (
    TemplateRecord,
    ConfirmedDocumentRecord,
    DataRowRecord,
    DataRowRevisionRecord,
    DataTableRecord,
    DataViewRecord,
    ExtractionRecord,
    FileCurrentDataRecord,
    TaskRecord,
)
from document_pipeline_api.services.export_scope import SCOPE_KEY, SCOPE_LABEL, csv_scope_value, export_row_values, table_has_input_scopes
from document_pipeline_api.services.export_scope import REVIEW_KEY, REVIEW_LABEL, imported_pending_review, table_has_pending_reviews
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.data_tables import (
    ColumnDef,
    ConfirmationRead,
    DataRowRead,
    DataTableDetail,
    DataTableRead,
)
from document_pipeline_api.schemas.extraction import DocumentExtraction
from document_pipeline_api.schemas.extraction import (
    DocumentKind,
    TemplateExtraction,
)
from document_pipeline_api.services.data_rows import data_row_read
from document_pipeline_api.services.templates import (
    get_template,
    get_template_version,
)
from document_pipeline_api.services.task_leases import TaskLeaseLostError


BUILTIN_TEMPLATE_VERSION = "builtin-v1"
TABLE_NAMES = {
    "invoice": "发票",
    "delivery": "送货单",
}
_BUILTIN_HEADER_KEYS = {
    "document_type", "seller_name", "buyer_name", "seller_tax_id", "buyer_tax_id",
    "seller_contact", "buyer_contact", "buyer_address", "document_number", "document_date",
    "amount_before_tax", "tax_amount", "total_amount", "total_quantity", "remarks",
}
# 内置表行数据用发票系存储键，而模板 item 字段用 amount/tax_amount；
# 展示/导出时把模板 item 键别名到真实存储键，避免"模板说金额、行里存 item_amount"对不上。
_BUILTIN_ITEM_KEY_ALIAS = {
    "amount": "item_amount",
    "tax_amount": "item_tax_amount",
    "remarks": "item_remarks",
}


def _builtin_template_columns(
    session: Session,
    document_kind: str,
) -> list[ColumnDef] | None:
    """内置表列契约按对应内置模板的字段生成（中文列名与模板一致）；
    模板缺失时返回 None，由调用方回退到固定映射。"""
    if session.get(TemplateRecord, f"builtin-{document_kind}") is None:
        return None
    template = get_template(session, f"builtin-{document_kind}")
    if template is None or not template.fields:
        return None
    return [
        ColumnDef(
            key=(
                _BUILTIN_ITEM_KEY_ALIAS.get(field.key, field.key)
                if field.section == "item"
                else field.key
            ),
            label=template.output_mapping.get(field.key, field.label),
            value_type=field.value_type,
            section=field.section,
        )
        for field in template.fields
    ]


COLUMN_LABELS = {
    "source_filename": "来源文件",
    "document_type": "单据类型",
    "seller_name": "销售方",
    "buyer_name": "购买方",
    "seller_tax_id": "销售方统一社会信用代码/纳税人识别号",
    "buyer_tax_id": "购买方统一社会信用代码/纳税人识别号",
    "seller_contact": "供方联系电话",
    "buyer_contact": "需方联系电话",
    "buyer_address": "收货地址",
    "document_number": "发票号码",
    "document_date": "开票日期",
    "amount_before_tax": "不含税金额",
    "tax_amount": "整单税额",
    "total_amount": "价税合计",
    "total_quantity": "总数量",
    "remarks": "备注",
    "item_index": "明细序号",
    "name": "商品名称",
    "specification": "规格型号",
    "unit": "单位",
    "quantity": "数量",
    "unit_price": "单价",
    "item_amount": "明细金额",
    "tax_rate": "税率",
    "item_tax_amount": "明细税额",
    "item_remarks": "明细备注",
}


def confirm_task(
    session: Session, task_id: str, expected_review_version: int, *,
    expected_lease_token: str | None = None, target_table_id: str | None = None,
    filename: str | None = None,
) -> ConfirmationRead:
    from document_pipeline_api.models import FileCurrentDataRecord
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot

    # Keep the status/version/validation checks and confirmation on one write
    # snapshot; a concurrent edit must not be approved from this older read.
    begin_write_snapshot(session)
    task = session.get(TaskRecord, task_id)
    extraction = session.get(ExtractionRecord, task_id)
    if task is None or extraction is None:
        raise HTTPException(404, "没有找到这份文件的提取结果。")
    if expected_lease_token is not None and (task.status != TaskStatus.VALIDATING.value or task.lease_token != expected_lease_token):
        raise TaskLeaseLostError("任务租约已被替换，拒绝迟到的自动确认。")
    if expected_lease_token is None and task.status not in {"needs_review", "completed", "waiting_for_template"}:
        raise HTTPException(409, "这份文件当前不能确认。")
    current = session.get(FileCurrentDataRecord, task_id)
    if current is None:
        raise HTTPException(409, "这份文件尚未建立当前数据，请先完成提取；不能用历史快照重新创建当前数据。")
    if not current.has_data or current.table_id is None:
        raise HTTPException(409, "当前数据已清空，不能从旧提取结果恢复当前数据。")
    if current.current_version != expected_review_version:
        raise HTTPException(409, "当前数据已经更新，请刷新后再确认。")
    if target_table_id is not None and target_table_id != current.table_id:
        raise HTTPException(409, "文件已有唯一当前数据，确认不能将旧结果复制到另一张表。")
    issues = json.loads(current.validation_json)
    if any(issue.get("severity") == "error" and not issue.get("ignored") for issue in issues):
        raise HTTPException(422, "结果仍有校验问题，请修正或忽略后再确认。")
    confirmed_at = utc_now()
    if expected_lease_token is not None:
        fence = session.execute(update(TaskRecord).where(
            TaskRecord.id == task_id, TaskRecord.status == TaskStatus.VALIDATING.value,
            TaskRecord.lease_token == expected_lease_token,
        ).values(status="completed", completed_at=confirmed_at, lease_token=None,
                 lease_expires_at=None, failure_code=None, failure_message=None, updated_at=confirmed_at))
        if fence.rowcount != 1:
            session.rollback()
            raise TaskLeaseLostError("任务租约已被替换，拒绝迟到的自动确认。")
    else:
        task.status = "completed"
        task.completed_at = task.completed_at or confirmed_at
        task.lease_token = None
        task.lease_expires_at = None
        task.failure_code = None
        task.failure_message = None
        task.updated_at = confirmed_at
    session.execute(update(DataRowRecord).where(DataRowRecord.task_id == task_id).values(review_pending=False))
    from document_pipeline_api.services.file_names import confirm_file_name
    confirm_file_name(task, filename)
    confirmation = get_confirmation(session, task_id)
    session.commit()
    return confirmation


def materialize_task_result(
    session: Session,
    task_id: str,
    *,
    result_json: str | None = None,
) -> tuple[DataTableRecord, int]:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    extraction = session.get(ExtractionRecord, task_id)
    if extraction is None:
        raise HTTPException(status_code=404, detail="这个任务还没有提取结果。")
    serialized = result_json or extraction.result_json
    result = (
        TemplateExtraction.model_validate_json(serialized)
        if extraction.document_kind == DocumentKind.CUSTOM.value
        else DocumentExtraction.model_validate_json(serialized)
    )
    table, rows = _materialize_rows(session, task, extraction, result)
    return table, len(rows)


def get_confirmation(session: Session, task_id: str) -> ConfirmationRead:
    from document_pipeline_api.services.current_data import get_current
    current = get_current(session, task_id)
    task = session.get(TaskRecord, task_id)
    table = session.get(DataTableRecord, current.table_id) if current.table_id else None
    if not current.has_data or table is None:
        raise HTTPException(404, "这份文件当前没有数据。")
    return ConfirmationRead(task_id=task_id, table_id=table.id, table_name=table.name,
                            review_version=current.current_version,
                            row_count=session.scalar(select(func.count(DataRowRecord.id)).where(DataRowRecord.task_id == task_id)) or 0,
                            confirmed_at=task.completed_at or current.updated_at)


def list_data_tables(session: Session) -> list[DataTableRead]:
    file_counts = dict(session.execute(select(FileCurrentDataRecord.table_id, func.count()).where(
        FileCurrentDataRecord.has_data.is_(True)).group_by(FileCurrentDataRecord.table_id)).all())
    row_counts = (
        select(DataRowRecord.table_id, func.count(DataRowRecord.id).label("row_count"))
        .group_by(DataRowRecord.table_id)
        .subquery()
    )
    records = session.execute(
        select(DataTableRecord, func.coalesce(row_counts.c.row_count, 0))
        .outerjoin(row_counts, DataTableRecord.id == row_counts.c.table_id)
        .order_by(DataTableRecord.created_at.desc())
    ).all()
    counts = {table.id: count for table, count in records}
    return [
        DataTableRead(
            grouping_fields=json.loads(table.grouping_json or '[]'),
            collection_kind=table.collection_kind,
            file_count=file_counts.get(table.id, 0),
            id=table.id,
            name=table.name,
            template_key=table.template_key,
            template_version=table.template_version,
            document_kind=table.document_kind,
            row_count=sum(counts.get(source, 0) for source in table.merge_sources) if table.merge_sources is not None else row_count,
            merge_sources=table.merge_sources,
            created_at=table.created_at,
        )
        for table, row_count in records
    ]


def get_data_table(
    session: Session,
    table_id: str,
    *,
    page: int = 1,
    page_size: int = 100,
    search: str | None = None,
) -> DataTableDetail:
    from document_pipeline_api.services.read_snapshot import begin_read_snapshot
    from document_pipeline_api.services.data_rows import data_rows_read
    begin_read_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    membership = table_membership(table)
    row_query = select(DataRowRecord).where(membership)
    count_query = select(func.count(DataRowRecord.id)).where(membership)
    if search:
        from document_pipeline_api.services.current_data import row_search_predicate
        row_query = row_query.where(row_search_predicate(search))
        count_query = count_query.where(row_search_predicate(search))
    rows = [] if table.collection_kind == "files" else session.scalars(
        row_query.order_by(*merge_order(table), *_row_order()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    row_count = session.scalar(count_query)
    sources = table.merge_sources
    if sources is not None:
        columns, key_maps = merge_columns(session, sources)
        result_rows = []
        for source_id, source_group in groupby(rows, key=lambda row: row.table_id):
            source_rows = list(source_group)
            source = session.get(DataTableRecord, source_id)
            key_map = key_maps[source_id]
            for row, source_read in zip(source_rows, data_rows_read(session, source, source_rows), strict=True):
                result_rows.append(source_read.model_copy(update={
                    "source_table_id": source_id,
                    "values": merged_values(row, key_maps),
                    "shared_header_keys": [key_map[key] for key in source_read.shared_header_keys if key in key_map],
                }))
    else:
        columns = table_columns(session, table)
        result_rows = data_rows_read(session, table, rows)
    return DataTableDetail(
        merge_sources=sources,
        grouping_fields=json.loads(table.grouping_json or '[]'),
        collection_kind=table.collection_kind,
        file_count=session.scalar(select(func.count()).select_from(FileCurrentDataRecord).where(
            FileCurrentDataRecord.table_id == table_id, FileCurrentDataRecord.has_data.is_(True))) or 0,
        presentation=table_presentation(session, table),
        id=table.id,
        name=table.name,
        template_key=table.template_key,
        template_version=table.template_version,
        document_kind=table.document_kind,
        row_count=row_count or 0,
        created_at=table.created_at,
        columns=columns,
        page=page,
        page_size=page_size,
        rows=result_rows,
    )


def table_presentation(session: Session, table: DataTableRecord):
    from document_pipeline_api.schemas.templates import TemplatePresentation

    if table.presentation_json:
        return TemplatePresentation.model_validate_json(table.presentation_json)
    if table.document_kind != DocumentKind.CUSTOM.value:
        return TemplatePresentation()
    # Hand-made and merged tables have no versioned source template.
    try:
        version = int(table.template_version)
    except (ValueError, TypeError):
        return TemplatePresentation()
    if session.get(TemplateRecord, table.template_key) is None:
        return TemplatePresentation()
    return get_template_version(session, table.template_key, version).behavior.presentation


def table_columns(session: Session, table: DataTableRecord) -> list[ColumnDef]:
    """表的列契约（中文列名 + 分区）。手动/合并表读 columns_json；内置表用固定映射；
    模板表用模板字段。身份字段（来源文件/明细序号）不进入列契约，
    前端默认不显示，导出也不包含。
    columns_json 是表的结构契约，不得根据当前行是否有值裁剪。模板创建的字段即使
    暂时全空也必须保留，否则用户只填写字段 1 后字段 2 会从界面消失。"""
    if table.collection_kind == "files":
        return []
    if table.merge_sources_json is not None:
        return merge_columns(session, table.merge_sources)[0]
    if table.columns_json:
        return _current_field_order(session, table, [ColumnDef(**column) for column in json.loads(table.columns_json)])
    if table.document_kind != DocumentKind.CUSTOM.value:
        builtin_columns = _builtin_template_columns(session, table.document_kind)
        if builtin_columns is not None:
            return builtin_columns
        return [
            ColumnDef(
                key=key,
                label=label,
                section="header" if key in _BUILTIN_HEADER_KEYS else "item",
            )
            for key, label in COLUMN_LABELS.items()
            if key not in ("source_filename", "item_index")
        ]
    template = get_template_version(
        session,
        table.template_key,
        int(table.template_version),
    )
    return _current_field_order(session, table, [
        ColumnDef(
            key=field.key,
            label=template.output_mapping.get(field.key, field.label),
            value_type=field.value_type,
            section=field.section,
        )
        for field in template.fields
    ])


def _current_field_order(session: Session, table: DataTableRecord, columns: list[ColumnDef]) -> list[ColumnDef]:
    """Project current reading order without rewriting the table's historical schema.

    Only matching key/section/type identities participate. Old or manually added
    columns retain their relative order after those fields. Values, labels and
    template/task snapshots remain unchanged.
    """
    record = session.get(TemplateRecord, table.template_key)
    if record is None or record.is_system or record.is_deleted:
        return columns
    latest = get_template(session, record.id)
    ranks = {(field.key, field.section, field.value_type): index for index, field in enumerate(latest.fields)}
    return sorted(columns, key=lambda column: ranks.get((column.key, column.section, column.value_type), len(ranks)))


def _persisted_columns(session: Session, table: DataTableRecord) -> list[ColumnDef]:
    if table.columns_json:
        return [ColumnDef(**column) for column in json.loads(table.columns_json)]
    return table_columns(session, table)


def add_custom_column(
    session: Session,
    table_id: str,
    label: str,
    section: str,
    value_type: str = "text",
) -> ColumnDef:
    if value_type not in {"text", "number", "boolean", "date"}:
        raise HTTPException(422, "不支持的字段类型。")
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    from document_pipeline_api.services.file_collections import require_tabular
    require_tabular(table)
    require_writable(table)
    normalized = label.strip()
    columns = _persisted_columns(session, table)
    identity = " ".join(normalized.split()).casefold()
    if any(" ".join(column.label.split()).casefold() == identity for column in columns):
        raise HTTPException(status_code=409, detail="列名已存在，请换一个名称。")
    column = ColumnDef(
        key=f"custom_{uuid4().hex[:12]}",
        label=normalized,
        value_type=value_type,
        section=section,
        user_defined=True,
    )
    columns.append(column)
    table.columns_json = json.dumps([item.model_dump() for item in columns], ensure_ascii=False)
    from document_pipeline_api.models import FileCurrentDataRecord
    for current in session.scalars(select(FileCurrentDataRecord).where(FileCurrentDataRecord.table_id == table_id)):
        update_current_schema(session, current, [*json.loads(current.schema_json), column.model_dump()])
    session.commit()
    return column


def delete_custom_column(session: Session, table_id: str, column_key: str) -> None:
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    from document_pipeline_api.services.file_collections import require_tabular
    require_tabular(table)
    require_writable(table)
    columns = _persisted_columns(session, table)
    target = next((column for column in columns if column.key == column_key), None)
    if target is None:
        raise HTTPException(status_code=404, detail="没有找到这一列。")
    if not target.user_defined:
        raise HTTPException(status_code=409, detail="模板字段不能删除。")
    # Inspect the complete current scope, including hidden pages. 0 and false
    # are values; neither a UI selection nor old history changes this rule.
    path = '$."' + column_key.replace('"', '\\"') + '"'
    values = session.scalars(select(func.json_extract(row_values_expression(), path))
                              .where(DataRowRecord.table_id == table_id).execution_options(yield_per=500))
    if any(value is not None and (not isinstance(value, str) or value.strip()) for value in values):
        raise HTTPException(409, "这一列仍有当前数据，不能删除。请先处理完整数据表中的值。")
    if column_key in json.loads(table.grouping_json or '[]') or session.scalar(select(DataViewRecord.id).where(
        DataViewRecord.table_id == table_id, DataViewRecord.field_key == column_key,
    ).limit(1)):
        raise HTTPException(409, "这一列仍用于分组，请先移除对应分组后再删除。")
    from document_pipeline_api.models.dashboard import DashboardCard
    for card in session.scalars(select(DashboardCard)):
        definition = json.loads(card.definition_json)
        if definition.get("table_id") == table_id and column_key in {
            definition.get("metric_field"), definition.get("group_field"), definition.get("date_field"),
        }:
            raise HTTPException(409, f"这一列仍用于仪表盘“{card.name}”，请先调整该统计卡片。")
    table.columns_json = json.dumps(
        [column.model_dump() for column in columns if column.key != column_key],
        ensure_ascii=False,
    )
    rows = session.scalars(select(DataRowRecord).where(DataRowRecord.table_id == table_id)).all()
    for row in rows:
        values = json.loads(row.row_json)
        if column_key in values:
            values.pop(column_key)
            row.row_json = json.dumps(values, ensure_ascii=False, sort_keys=True)
            if row.task_id is None:
                row.row_version += 1
            row.updated_at = utc_now()
    from document_pipeline_api.models import FileCurrentDataRecord
    for current in session.scalars(select(FileCurrentDataRecord).where(FileCurrentDataRecord.table_id == table_id)):
        update_current_schema(session, current, [column for column in json.loads(current.schema_json) if column["key"] != column_key])
        header = json.loads(current.header_json)
        header.pop(column_key, None)
        current.header_json = json.dumps(header, ensure_ascii=False)
    session.commit()


def _present_row_keys(session: Session, table_id: str) -> set[str]:
    """在 SQLite 内聚合 JSON 对象键，Python 侧内存只随列数增长。"""
    return set(
        session.execute(
            text(
                "SELECT DISTINCT json_each.key "
                "FROM data_rows LEFT JOIN file_current_data ON file_current_data.task_id = data_rows.task_id, json_each(json_patch(coalesce(file_current_data.header_json, '{}'), data_rows.row_json)) "
                "WHERE data_rows.table_id = :table_id"
            ),
            {"table_id": table_id},
        ).scalars()
    )


def _row_order() -> list[object]:
    """文件明细按稳定序号排列，独立数据保留自己的行顺序。"""
    return [
        DataRowRecord.task_id.is_(None),
        DataRowRecord.task_id,
        DataRowRecord.item_index,
        DataRowRecord.id,
    ]


def export_data_table(session: Session, table_id: str, *, scope=None, columns=None) -> StreamingResponse:
    selection = prepare_export_selection(session, table_id, scope)
    table = selection.table
    if table.collection_kind == "files":
        from document_pipeline_api.services.file_exports import export_files_excel
        return export_files_excel(session, table, selection=selection)
    from document_pipeline_api.services.file_collections import require_tabular
    require_tabular(table)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _unique_sheet_name(table.name, set())
    keys, labels = _excel_columns(session, table, columns)
    rows = session.scalars(
        select(DataRowRecord).where(*selection.predicates)
        .order_by(*merge_order(table), *_row_order()).execution_options(yield_per=1000)
    )
    column_defs = {column.key: column for column in table_columns(session, table)}
    _populate_export_sheet(sheet, export_records(session, table, rows, include_table_name="__zhiyi_source_table" in keys), keys, labels, column_defs)

    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    workbook.save(output)
    output.seek(0)
    filename = f"{table.name}{selection.suffix}.xlsx"
    disposition = f"attachment; filename=table-{table.id}.xlsx; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        output,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": disposition, **selection.headers()},
        background=BackgroundTask(output.close),
    )


def export_data_table_views(session: Session, table_id: str, *, scope=None, columns=None) -> StreamingResponse:
    """Apply the current selection inside each saved grouping, including unmatched rows."""
    from document_pipeline_api.services.data_views import refresh_data_views
    refresh_data_views(session, table_id)
    selection = prepare_export_selection(session, table_id, scope)
    table = selection.table
    from document_pipeline_api.services.file_collections import require_tabular
    require_tabular(table)
    require_writable(table)
    views = session.scalars(
        select(DataViewRecord)
        .where(DataViewRecord.table_id == table_id)
        .order_by(DataViewRecord.created_at, DataViewRecord.name)
    ).all()
    if not views:
        raise HTTPException(status_code=422, detail="当前数据表还没有分 Sheet 视图。")
    if scope is not None and scope.view_id:
        views = [view for view in views if view.id == scope.view_id]
    keys, labels = _excel_columns(session, table, columns)
    column_defs = {column.key: column for column in table_columns(session, table)}
    workbook = Workbook()
    workbook.remove(workbook.active)
    used_names: set[str] = set()
    matching = []
    for view in views:
        from document_pipeline_api.services.data_views import _view_predicate
        predicate = _view_predicate(view)
        if not session.scalar(select(DataRowRecord.id).where(*selection.predicates, predicate).limit(1)):
            continue
        matching.append(predicate)
        if len(matching) > 100:
            raise HTTPException(422, "当前范围超过 100 个分组，请选择具体分组导出，或导出单张 Excel、CSV、JSON。")
        rows = session.scalars(select(DataRowRecord)
            .where(*selection.predicates, predicate)
            .order_by(*_row_order()).execution_options(yield_per=1000))
        sheet = workbook.create_sheet(_unique_sheet_name(view.name, used_names))
        _populate_export_sheet(sheet, rows, keys, labels, column_defs)
    unmatched_predicate = ~func.coalesce(or_(*matching), False) if matching else True
    if session.scalar(select(DataRowRecord.id).where(*selection.predicates, unmatched_predicate).limit(1)):
        unmatched = session.scalars(select(DataRowRecord).where(*selection.predicates, unmatched_predicate)
                                  .order_by(*_row_order()).execution_options(yield_per=1000))
        sheet = workbook.create_sheet(_unique_sheet_name("未分组", used_names))
        _populate_export_sheet(sheet, unmatched, keys, labels, column_defs)
    if not workbook.worksheets:
        _populate_export_sheet(workbook.create_sheet("无匹配数据"), [], keys, labels, column_defs)
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    workbook.save(output)
    output.seek(0)
    filename = f"{table.name}{selection.suffix}-分Sheet.xlsx"
    disposition = f"attachment; filename=table-{table.id}-views.xlsx; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition, **selection.headers()},
        background=BackgroundTask(output.close),
    )


def _unique_sheet_name(raw: str, used: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "_", raw).strip()[:31] or "Sheet"
    name = base
    index = 2
    while name.casefold() in used:
        suffix = f"-{index}"
        name = f"{base[:31-len(suffix)]}{suffix}"
        index += 1
    used.add(name.casefold())
    return name


def _excel_columns(session, table, columns):
    if columns is None:
        return _export_columns(session, table)
    labels = {column.key: column.label for column in table_columns(session, table)}
    if table.merge_sources is not None:
        labels["__zhiyi_source_table"] = "原表"
    if any(key not in labels for key in columns):
        raise HTTPException(409, "导出列已变化，请刷新数据表后重试。")
    return list(dict.fromkeys(columns)), labels


def _populate_export_sheet(sheet, rows, keys, labels, column_defs) -> None:
    header_fill = PatternFill("solid", fgColor="F0FDF4")
    for index, key in enumerate(keys, start=1):
        cell = sheet.cell(row=1, column=index, value=safe_excel_value(labels[key]))
        cell.data_type = "s"
        cell.font = Font(bold=True, color="065F46")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    max_widths = [_display_width(labels[key]) for key in keys]
    task_spans: dict[str, list[int]] = {}
    row_number = 1
    for record in rows:
        values_map = row_values(record)
        values = [safe_excel_value(values_map.get(key)) for key in keys]
        sheet.append(values)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                # Preserve literal error-looking text without adding an apostrophe.
                cell.data_type = "s"
        row_number += 1
        for index, value in enumerate(values):
            max_widths[index] = max(max_widths[index], _display_width(value))
        group_key = record.task_id or values_map.get("__row_group")
        if group_key:
            task_spans.setdefault(str(group_key), []).append(row_number)
    sheet.freeze_panes = "A2"
    if keys and row_number > 1:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(keys))}{row_number}"
    header_keys = {key for key in keys if key in column_defs and column_defs[key].section == "header"}
    for key in header_keys:
        column_index = keys.index(key) + 1
        for span in task_spans.values():
            # A/B/A must never merge across B. Preserve differing header values
            # even if a legacy independent group contains inconsistent cells.
            start = previous = span[0]
            for current in [*span[1:], None]:
                contiguous = current is not None and current == previous + 1
                same = contiguous and _canonical_json(sheet.cell(current, column_index).value) == _canonical_json(sheet.cell(start, column_index).value)
                if not same:
                    if previous > start:
                        sheet.merge_cells(start_row=start, start_column=column_index, end_row=previous, end_column=column_index)
                        sheet.cell(start, column_index).alignment = Alignment(vertical="center", wrap_text=True)
                    start = current
                previous = current
    for index in range(1, len(keys) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = min(max(max_widths[index - 1] + 2, 10), 48)


def _display_width(value: object) -> int:
    text_value = "" if value is None else str(value)
    return max(
        (sum(2 if ord(char) > 255 else 1 for char in line) for line in text_value.splitlines()),
        default=0,
    )


def _get_or_create_table(
    session: Session,
    extraction: ExtractionRecord,
    *,
    target_table_id: str | None = None,
) -> DataTableRecord:
    from document_pipeline_api.services.template_contract import destination_for_extraction
    bound = destination_for_extraction(session,extraction,target_table_id)
    if bound is not None:
        return bound
    document_kind = extraction.document_kind
    is_custom = document_kind == DocumentKind.CUSTOM.value
    template_key = extraction.template_id if is_custom else document_kind
    template_version = str(extraction.template_version) if is_custom else BUILTIN_TEMPLATE_VERSION
    if template_key is None:
        raise RuntimeError("自定义提取结果缺少模板。")
    table = session.scalar(
        select(DataTableRecord).where(
            DataTableRecord.template_key == template_key,
            DataTableRecord.template_version == template_version,
        )
    )
    if table is not None:
        return table
    table_name = TABLE_NAMES.get(document_kind, "提取结果")
    if is_custom and extraction.template_version is not None:
        table_name = get_template_version(
            session,
            template_key,
            extraction.template_version,
        ).name
    table = DataTableRecord(
        id=str(uuid4()),
        name=table_name,
        template_key=template_key,
        template_version=template_version,
        document_kind=document_kind,
    )
    session.add(table)
    session.flush()
    return table


def _materialize_rows(
    session: Session, task: TaskRecord, extraction: ExtractionRecord,
    result: DocumentExtraction | TemplateExtraction,
) -> tuple[DataTableRecord, list[dict[str, object | None]]]:
    from document_pipeline_api.models import FileCurrentDataRecord
    from document_pipeline_api.services.current_data import initialize_current
    from document_pipeline_api.services.data_baseline_audit import split_legacy_result

    current = session.get(FileCurrentDataRecord, task.id)
    if current is not None:
        table = session.get(DataTableRecord, current.table_id) if current.table_id else None
        if table is None or not current.has_data:
            raise HTTPException(409, "当前数据已清空或所属表已删除，不能重新保存旧提取结果。")
        rows = session.scalars(select(DataRowRecord).where(DataRowRecord.task_id == task.id)
                               .order_by(DataRowRecord.item_index)).all()
        return table, [row_values(row) for row in rows]
    table = _get_or_create_table(session, extraction, target_table_id=task.target_table_id)
    header, items = split_legacy_result(result.model_dump_json(), custom=extraction.document_kind == "custom")
    columns = (json.loads(extraction.output_schema_json) if extraction.output_schema_json is not None
               else file_data_schema(session, table, header, items, legacy=extraction.document_kind != "custom"))
    if table.collection_kind == "files" and not columns:
        raise HTTPException(409, "这份自主提取结果缺少文件字段结构，不能建立当前数据。")
    initialize_current(session, task_id=task.id, table_id=table.id, header=header, items=items,
                       schema=columns, validation_json=extraction.validation_json)
    session.execute(update(DataRowRecord).where(DataRowRecord.task_id == task.id).values(
        input_scope_json=extraction.input_scope_json, review_pending=task.status != TaskStatus.COMPLETED.value,
    ))
    rows = session.scalars(select(DataRowRecord).where(DataRowRecord.task_id == task.id)
                           .order_by(DataRowRecord.item_index)).all()
    return table, [row_values(row) for row in rows]


def file_data_schema(session: Session, table: DataTableRecord, header: dict, items: list[dict], *, legacy: bool) -> list[dict]:
    columns = [column.model_dump() for column in table_columns(session, table)]
    if legacy:
        # Old built-in extraction contracts include optional fields not selected
        # in the starter template. Preserve their identities during migration.
        known = {column["key"] for column in columns}
        numeric = {"amount_before_tax", "tax_amount", "total_amount", "total_quantity",
                   "quantity", "unit_price", "item_amount", "item_tax_amount"}
        for section, keys in (("header", set(header)), ("item", {key for item in items for key in item})):
            for key in sorted(keys - known):
                columns.append({"key": key, "label": COLUMN_LABELS.get(key, key), "section": section,
                                "value_type": "number" if key in numeric else "date" if key == "document_date" else "text"})
        for column in columns:
            if column["key"] in numeric:
                column["value_type"] = "number"
            elif column["key"] == "document_date":
                column["value_type"] = "date"
    return columns


def _export_columns(
    session: Session,
    table: DataTableRecord,
) -> tuple[list[str], dict[str, str]]:
    if table.merge_sources_json is not None:
        columns = table_columns(session, table)
        return [column.key for column in columns], {column.key: column.label for column in columns}
    present = _present_row_keys(session, table.id)
    if table.columns_json:
        columns = json.loads(table.columns_json)
        labels = {column["key"]: column["label"] for column in columns}
        keys = [column["key"] for column in columns]
        custom = {column["key"] for column in columns if column.get("user_defined")}
        keys = [key for key in keys if key in present or key in custom] or keys
        return keys, labels
    if table.document_kind != DocumentKind.CUSTOM.value:
        builtin_columns = _builtin_template_columns(session, table.document_kind)
        if builtin_columns is not None:
            keys = [column.key for column in builtin_columns]
            labels = {column.key: column.label for column in builtin_columns}
            keys = [key for key in keys if key in present] or keys
            return keys, labels
        keys = [key for key in COLUMN_LABELS if key not in ("source_filename", "item_index")]
        return keys, {key: COLUMN_LABELS[key] for key in keys}
    version = int(table.template_version)
    template = get_template_version(session, table.template_key, version)
    keys = [
        *[field.key for field in template.fields if field.section == "header"],
        *[field.key for field in template.fields if field.section == "item"],
    ]
    keys = [key for key in keys if key in present] or keys
    labels = {
        field.key: template.output_mapping.get(field.key, field.label) for field in template.fields
    }
    return keys, labels


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _excel_safe(value: object | None) -> object | None:
    """CSV formula guard retained for legacy callers; XLSX uses safe_excel_value."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _table_read(
    session: Session,
    table: DataTableRecord,
    row_count: int | None = None,
) -> DataTableRead:
    if row_count is None:
        row_count = (
            session.scalar(
                select(func.count(DataRowRecord.id)).where(table_membership(table))
            )
            or 0
        )
    return DataTableRead(
        merge_sources=table.merge_sources,
        grouping_fields=json.loads(table.grouping_json or '[]'),
        collection_kind=table.collection_kind,
        file_count=session.scalar(select(func.count()).select_from(FileCurrentDataRecord).where(
            FileCurrentDataRecord.table_id == table.id, FileCurrentDataRecord.has_data.is_(True),
        )) or 0,
        id=table.id,
        name=table.name,
        template_key=table.template_key,
        template_version=table.template_version,
        document_kind=table.document_kind,
        row_count=row_count,
        created_at=table.created_at,
    )


def rename_table(session: Session, table_id: str, name: str) -> DataTableRead:
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    table.name = name
    session.commit()
    return _table_read(session, table)


def delete_table(session: Session, table_id: str) -> None:
    from document_pipeline_api.models import FileCurrentDataRecord
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    # The complete row inventory belongs to the same write snapshot as its
    # removal; a new file must not arrive between enumeration and deletion.
    begin_write_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        session.rollback()
        raise HTTPException(404, "没有找到这个数据表。")
    row_ids = session.scalars(select(DataRowRecord.id).where(DataRowRecord.table_id == table_id)).all()
    if row_ids:
        delete_rows(session, table_id, list(row_ids), commit=False)
    # Deleting the data container preserves evidence and file revisions. Empty
    # current parents are tombstones: reopening cannot replay an old snapshot.
    session.execute(update(FileCurrentDataRecord).where(FileCurrentDataRecord.table_id == table_id).values(table_id=None))
    session.execute(delete(DataRowRevisionRecord).where(DataRowRevisionRecord.table_id == table_id, DataRowRevisionRecord.task_id.is_(None)))
    session.execute(delete(DataViewRecord).where(DataViewRecord.table_id == table_id))
    session.execute(delete(ConfirmedDocumentRecord).where(ConfirmedDocumentRecord.table_id == table_id))
    from document_pipeline_api.models import TemplateTableBindingRecord
    session.execute(delete(TemplateTableBindingRecord).where(TemplateTableBindingRecord.table_id==table_id))
    session.delete(table)
    session.commit()


def create_table(session: Session, name: str, template_key: str) -> DataTableRead:
    """从指定模板创建一张空表。

    设计决定：手动新建表必须基于模板——空白表没有字段定义，前端没有列名、
    新增行也无处填写，没有实际用途。列契约快照进 columns_json，因此
    表名/列名都可显示中文且与模板后续改动解耦。
    """
    template = get_template(session, template_key)
    if template.engine_kind == "autonomous":
        raise HTTPException(409, "自主提取按文件决定字段，不能用来创建空白数据表。")
    columns = [
        {
            "key": field.key,
            "label": field.label,
            "value_type": field.value_type,
            "section": field.section,
        }
        for field in template.fields
    ]
    table = DataTableRecord(
        id=str(uuid4()),
        name=name,
        template_key=f"manual-{uuid4().hex[:12]}",
        template_version="1",
        document_kind=DocumentKind.CUSTOM.value,
        columns_json=json.dumps(columns, ensure_ascii=False),
        presentation_json=template.behavior.presentation.model_dump_json(),
    )
    session.add(table)
    session.commit()
    return _table_read(session, table)


def add_row(
    session: Session,
    table_id: str,
    values: dict[str, object | None],
    editor: str,
) -> DataRowRead:
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    from document_pipeline_api.services.file_collections import require_tabular
    require_tabular(table)
    require_writable(table)
    from document_pipeline_api.services.current_data import check_column_values
    check_column_values(values, [column.model_dump() for column in table_columns(session, table)])
    cleaned = dict(values)
    max_index = session.scalar(
        select(func.max(DataRowRecord.item_index)).where(DataRowRecord.table_id == table_id)
    )
    row = DataRowRecord(
        table_id=table_id,
        task_id=None,
        item_index=(max_index or 0) + 1,
        row_json=json.dumps(cleaned, ensure_ascii=False),
        row_version=1,
    )
    session.add(row)
    session.flush()
    session.add(
        DataRowRevisionRecord(
            row_id=row.id,
            table_id=table_id,
            task_id=None,
            version=1,
            operation="manual_create",
            before_json=None,
            after_json=row.row_json,
            editor=editor,
        )
    )
    session.commit()
    created = session.get(DataRowRecord, row.id)
    if created is None:
        raise RuntimeError("新增的数据行不存在。")
    return data_row_read(created)


def delete_rows(session: Session, table_id: str, row_ids: list[int], *,
                edit_session_id: str | None = None, commit: bool = True, expected_versions: dict[int, int] | None = None) -> int:
    from document_pipeline_api.services.current_data import get_current, revise_current
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(404, "没有找到这个数据表。")
    require_writable(table)
    rows = session.scalars(select(DataRowRecord).where(DataRowRecord.table_id == table_id, DataRowRecord.id.in_(row_ids))).all()
    if set(row_ids) != {row.id for row in rows}:
        raise HTTPException(404, "部分数据行不存在或不属于这张表。")
    if expected_versions is not None and (set(expected_versions) != set(row_ids) or any(
            expected_versions.get(row.id) != row.row_version for row in rows)):
        raise HTTPException(409, "选中明细已经变化，请重新查看后再删除。")
    files: dict[str, list[int]] = {}
    for row in rows:
        if row.task_id is not None:
            files.setdefault(row.task_id, []).append(row.id)
        else:
            session.add(DataRowRevisionRecord(row_id=row.id, table_id=table_id, task_id=None,
                        version=row.row_version + 1, operation="delete", before_json=row.row_json,
                        after_json=None, editor="user"))
            session.delete(row)
    for task_id, ids in files.items():
        current = get_current(session, task_id)
        revise_current(session, task_id, expected_version=current.current_version,
                       deleted_ids=ids, edit_session_id=edit_session_id, operation="delete")
    if commit:
        session.commit()
    else:
        session.flush()
    return len(rows)


def merge_tables(
    session: Session,
    table_ids: list[str],
    name: str,
) -> DataTableRead:
    """Create an independent snapshot with a fixed, compatible column union."""
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    if len(set(table_ids)) != len(table_ids):
        raise HTTPException(422, "合并时同一张表只能选择一次。")
    from document_pipeline_api.services.file_collections import require_tabular
    for table_id in table_ids:
        table = session.get(DataTableRecord, table_id)
        if table is None:
            raise HTTPException(404, "没有找到数据表。")
        require_tabular(table)
        require_writable(table)
    merge_columns(session, table_ids, validate=True)
    identifier = str(uuid4())
    merged = DataTableRecord(
        id=identifier, name=name, template_key=f"merged-{identifier}",
        template_version="1", document_kind=DocumentKind.CUSTOM.value,
        merge_sources_json=json.dumps(table_ids),
    )
    session.add(merged)
    session.commit()
    return _table_read(session, merged)


def export_data_table_csv(session: Session, table_id: str, *, scope=None) -> StreamingResponse:
    selection = prepare_export_selection(session, table_id, scope)
    table = selection.table
    from document_pipeline_api.services.file_collections import require_tabular
    require_tabular(table)
    keys, labels = _export_columns(session, table)
    scoped = table_has_input_scopes(session, table_id, predicates=selection.predicates, include_sources=bool(table.merge_sources))
    pending = table_has_pending_reviews(session, table_id, predicates=selection.predicates, include_sources=bool(table.merge_sources))
    if pending and REVIEW_LABEL in labels.values():
        raise HTTPException(422, "业务列名与知意来源状态说明列冲突，请重命名该业务列后导出。")
    if scoped and SCOPE_LABEL in labels.values():
        raise HTTPException(422, "业务列名与知意范围说明列冲突，请重命名该业务列后导出。")
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    line = StringIO()
    writer = csv.writer(line)

    def write_csv_row(values: list[object | None]) -> None:
        line.seek(0)
        line.truncate(0)
        writer.writerow(values)
        output.write(line.getvalue().encode("utf-8"))

    output.write(b"\xef\xbb\xbf")
    write_csv_row([_csv_safe(labels[key]) for key in keys] + ([SCOPE_LABEL] if scoped else []) + ([REVIEW_LABEL] if pending else []))
    row_jsons = session.scalars(
        select(DataRowRecord)
        .where(*selection.predicates)
        .order_by(*merge_order(table), *_row_order())
        .execution_options(yield_per=1000)
    )
    for row_json in export_records(session, table, row_jsons):
        row = export_row_values(row_json)
        write_csv_row([_csv_safe(row.get(key)) for key in keys] + ([csv_scope_value(row)] if scoped else []) + (["待核对" if row.get(REVIEW_KEY) else ""] if pending else []))
    output.seek(0)
    filename = f"{table.name}{selection.suffix}.csv"
    disposition = f"attachment; filename=table-{table.id}.csv; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        output,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition, **selection.headers()},
        background=BackgroundTask(output.close),
    )


def export_data_table_json(session: Session, table_id: str, *, scope=None) -> StreamingResponse:
    selection = prepare_export_selection(session, table_id, scope)
    table = selection.table
    if table.collection_kind == "files":
        from document_pipeline_api.services.file_exports import export_files_json
        return export_files_json(session, table, selection=selection)
    from document_pipeline_api.services.file_collections import require_tabular
    require_tabular(table)
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    output.write(b"[")
    first = True
    row_jsons = session.scalars(
        select(DataRowRecord)
        .where(*selection.predicates)
        .order_by(*merge_order(table), *_row_order())
        .execution_options(yield_per=1000)
    )
    for row_json in export_records(session, table, row_jsons):
        if not first:
            output.write(b",")
        output.write(json.dumps(export_row_values(row_json), ensure_ascii=False).encode("utf-8"))
        first = False
    output.write(b"]")
    output.seek(0)
    filename = f"{table.name}{selection.suffix}.json"
    disposition = f"attachment; filename=table-{table.id}.json; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        output,
        media_type="application/json",
        headers={"Content-Disposition": disposition, **selection.headers()},
        background=BackgroundTask(output.close),
    )


def _csv_safe(value: object | None) -> object | None:
    return _excel_safe(value)


class ImportFileError(ValueError):
    """导入文件不安全、损坏或超出结构限制。"""


class ImportSheetSelection(ImportFileError):
    def __init__(self, sheets):
        super().__init__("工作簿有多个业务工作表，请选择本次导入的一页。")
        self.sheets = sheets


def parse_import_rows(
    content: bytes,
    filename: str,
    *,
    max_rows: int = 100_000,
    max_columns: int = 512,
    max_uncompressed_bytes: int = 256 * 1024 * 1024,
    sheet_name: str | None = None,
) -> list[dict[str, object]]:
    """把 xlsx/csv 内容解析成行字典（第一行为表头）。空行跳过。"""
    if filename.lower().endswith(".csv"):
        if sheet_name is not None:
            raise ImportFileError("CSV 不包含工作表，无需指定 Sheet。")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = content.decode("gb18030")
            except UnicodeDecodeError as error:
                raise ImportFileError("CSV 编码无法识别，请另存为 UTF-8 或 GB18030（GBK）后导入。") from error
        if "\x00" in text:
            raise ImportFileError("CSV 包含无效文字，请另存为 UTF-8 CSV 后导入。")
        try:
            rows_iter = csv.reader(StringIO(text))
            header_row = next(rows_iter, None)
            if header_row is None:
                return []
            headers = _validate_import_headers(header_row, max_columns=max_columns)
            return _collect_import_rows(
                rows_iter,
                headers,
                max_rows=max_rows,
                max_columns=max_columns,
            )
        except csv.Error as error:
            raise ImportFileError("CSV 单元格内容超过当前读取限制，请拆分或缩短后重试。") from error
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            expanded_size = sum(info.file_size for info in archive.infolist())
            if expanded_size > max_uncompressed_bytes:
                raise ImportFileError("Excel 文件解压后过大，已拒绝导入。")
            from document_pipeline_api.services.xlsx_budget import check_workbook_budget
            try:
                check_workbook_budget(archive, max_rows=max_rows, max_columns=max_columns)
            except ValueError as error:
                raise ImportFileError(str(error)) from error
        workbook = load_workbook(BytesIO(content), read_only=False, data_only=True)
        formula_workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
        try:
            if workbook.properties.subject == "zhiyi:per-file-export:v1":
                raise ImportFileError("这是按文件阅读的导出工作簿，不能作为统一字段表直接导入。请先整理需要的数据为单行表头的普通工作表。")
            from document_pipeline_api.services.export_scope import read_workbook_scopes, workbook_business_sheets
            sheets = workbook_business_sheets(workbook)
            if sheet_name is None and len(sheets) > 1:
                raise ImportSheetSelection(sheets)
            if sheet_name is not None and sheet_name not in sheets:
                raise ImportFileError("指定的业务工作表不存在，或属于来源说明页，请重新选择。")
            if not sheets:
                return []
            sheet = workbook[sheet_name or sheets[0]]
            # Normal mode renames duplicate titles; read-only mode preserves
            # them. Both worksheet lists exclude chartsheets and retain order.
            if len(workbook.worksheets) != len(formula_workbook.worksheets):
                raise ImportFileError("Excel 工作表结构不一致，无法核对原始数值。")
            formula_sheet = formula_workbook.worksheets[workbook.worksheets.index(sheet)]
            from document_pipeline_api.services.xlsx_numbers import XlsxNumericError, validate_xlsx_numbers
            try:
                validate_xlsx_numbers(formula_sheet)
            except XlsxNumericError as error:
                raise ImportFileError(str(error)) from error
            formula_sheet.reset_dimensions()
            for row in formula_sheet.iter_rows():
                for formula_cell in row:
                    if formula_cell.data_type == "f" and sheet[formula_cell.coordinate].value is None:
                        raise ImportFileError(
                            "Excel 包含尚无计算结果缓存的公式；知意不会自行执行公式，请先在表格软件中计算并另存为值后导入。"
                        )
            try:
                row_scopes = read_workbook_scopes(workbook, sheet)
            except ValueError as error:
                raise ImportFileError(f"无法恢复 Excel 来源范围：{error}") from error
            if any(merged.min_row == 1 for merged in sheet.merged_cells.ranges):
                raise ImportFileError(
                    "Excel 第一行包含合并表头，无法确定唯一字段名；请整理为单行表头后再导入。"
                )
            merged_values: dict[tuple[int, int], object] = {}
            merged_header_columns: set[int] = set()
            row_groups: dict[int, str] = {}
            for merged_range in sheet.merged_cells.ranges:
                value = sheet.cell(merged_range.min_row, merged_range.min_col).value
                for row_index in range(merged_range.min_row, merged_range.max_row + 1):
                    for column_index in range(merged_range.min_col, merged_range.max_col + 1):
                        merged_values[(row_index, column_index)] = value
                if merged_range.min_row >= 2 and merged_range.max_row > merged_range.min_row:
                    merged_header_columns.add(merged_range.min_col)
                    group = f"xlsx:{merged_range.coord}"
                    for row_index in range(merged_range.min_row, merged_range.max_row + 1):
                        row_groups.setdefault(row_index, group)
            rows_iter = (
                tuple(merged_values.get((cell.row, cell.column), cell.value) for cell in row)
                + (row_groups.get(row[0].row),)
                + ((row_scopes.get(row[0].row),) if row_scopes else ())
                for row in sheet.iter_rows()
            )
            header_row = next(rows_iter, None)
            if header_row is None:
                return []
            headers = _validate_import_headers(header_row[:-(2 if row_scopes else 1)], max_columns=max_columns)
            headers.append("__row_group")
            if row_scopes:
                headers.append(SCOPE_KEY)
            if merged_header_columns:
                header_labels = [headers[index - 1] for index in sorted(merged_header_columns)]
                rows_iter = (tuple(row) + (header_labels,) for row in rows_iter)
                headers.append("__header_labels")
            return _collect_import_rows(
                rows_iter,
                headers,
                max_rows=max_rows,
                max_columns=max_columns,
            )
        finally:
            workbook.close()
            formula_workbook.close()
    except ImportFileError:
        raise
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise ImportFileError("Excel 文件损坏或无法读取。") from error


def _validate_import_headers(
    header_row: Sequence[object],
    *,
    max_columns: int,
) -> list[str]:
    headers = [str(header).strip() if header is not None else "" for header in header_row]
    headers = [SCOPE_KEY if header == SCOPE_LABEL else REVIEW_KEY if header == REVIEW_LABEL else header for header in headers]
    if len(headers) > max_columns:
        raise ImportFileError(f"导入文件不能超过 {max_columns} 列。")
    if not headers or any(not header for header in headers):
        raise ImportFileError("导入文件的表头不能为空。")
    if any(header in {"__row_group", "__header_labels"} for header in headers):
        raise ImportFileError("导入表头使用了知意内部字段名，请重命名该业务列后再导入。")
    # Excel 允许视觉上重复的表头。内部为每次出现分配稳定后缀，避免 dict
    # 静默覆盖；写入既有模板表时首列按原字段映射，其余同名列保留为独立输入。
    seen: dict[str, int] = {}
    unique_headers: list[str] = []
    for header in headers:
        seen[header] = seen.get(header, 0) + 1
        unique_headers.append(header if seen[header] == 1 else f"{header}__重复{seen[header]}")
    return unique_headers


def _collect_import_rows(
    rows_iter: Iterable[Sequence[object]],
    headers: list[str],
    *,
    max_rows: int,
    max_columns: int,
) -> list[dict[str, object]]:
    parsed: list[dict[str, object]] = []
    for row in rows_iter:
        business_cell_count = sum(
            1 for header in headers[:len(row)]
            if header not in {SCOPE_KEY, REVIEW_KEY, "__row_group", "__header_labels"}
        )
        if business_cell_count > max_columns:
            raise ImportFileError(f"导入文件不能超过 {max_columns} 列。")
        if len(row) > len(headers):
            raise ImportFileError("数据列数超过表头列数，请整理表头后再导入。")
        if all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        if len(parsed) >= max_rows:
            raise ImportFileError(f"单次导入不能超过 {max_rows} 行。")
        parsed.append(dict(zip(headers, row)))
    return parsed


def import_table_rows(
    session: Session,
    table_id: str,
    rows: list[dict[str, object]],
) -> int:
    """按字段并集追加导入行；新字段扩展当前表列契约，不丢弃。"""
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    from document_pipeline_api.services.file_collections import require_tabular
    require_tabular(table)
    require_writable(table)
    columns = table_columns(session, table)
    imported_header_labels = set(rows[0].get("__header_labels") or []) if rows else set()
    duplicate_headers = [
        str(key) for key in (rows[0] if rows else {}) if re.search(r"__重复\d+$", str(key))
    ]
    if duplicate_headers:
        labels = sorted({re.sub(r"__重复\d+$", "", key) for key in duplicate_headers})
        raise HTTPException(
            status_code=422,
            detail=f"导入文件存在重复列名：{'、'.join(labels)}。请先重命名后再导入。",
        )
    label_to_key: dict[str, str] = {}
    for column in columns:
        label_to_key.setdefault(column.label, column.key)
    # 兼容旧版内置模板及用户已经导出的工作簿，升级字段文案后重新导入仍落回原字段。
    for old_label, new_label in {
        "销售方": "销售方名称",
        "购买方": "购买方名称",
        "供货方": "供货单位名称",
        "收货方": "收货单位名称",
        "商品名称": "项目名称",
        "货品名称": "货物名称",
    }.items():
        if new_label in label_to_key:
            label_to_key.setdefault(old_label, label_to_key[new_label])
    key_set = {column.key for column in columns}
    header_to_key: dict[str, str] = {}
    if rows:
        raw_headers = dict.fromkeys(header for row in rows for header in row)
        mapped_headers: dict[str, str] = {}
        for raw_header in raw_headers:
            header = str(raw_header).strip()
            if header in {SCOPE_KEY, REVIEW_KEY, "__row_group", "__header_labels"}:
                continue
            base_label = re.sub(r"__重复\d+$", "", header)
            # 第一列同名字段复用现有契约；重复出现或全新字段新增独立列。
            key = label_to_key.get(header, header if header in key_set else None)
            if key is None or (header != base_label and base_label in label_to_key):
                key = f"import_{uuid4().hex[:12]}"
                columns.append(
                    ColumnDef(
                        key=key,
                        label=base_label,
                        value_type=infer_import_type(row.get(raw_header) for row in rows),
                        section="header" if base_label in imported_header_labels else "item",
                        user_defined=True,
                    )
                )
            header_to_key[header] = key
            prior_header = mapped_headers.get(key)
            if prior_header is not None and prior_header != header:
                raise HTTPException(
                    422,
                    f"导入列“{prior_header}”和“{header}”映射到同一目标字段，请保留一列后重试。",
                )
            mapped_headers[key] = header
            if base_label in imported_header_labels and any(column.key == key and column.section != "header" for column in columns):
                raise HTTPException(422, f"字段“{base_label}”在目标表中是明细字段，导入不能将它改成文件级字段。")
    column_by_key = {column.key: column for column in columns}
    normalized: list[tuple[dict[str, object | None], str | None, bool | None]] = []
    import_batch = uuid4().hex
    for row_index, raw in enumerate(rows, 1):
        from document_pipeline_api.schemas.input_scope import InputScope
        imported_scope = None
        try:
            review_pending = imported_pending_review(raw.get(REVIEW_KEY))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if raw.get(SCOPE_KEY):
            try:
                scope = (InputScope.model_validate(raw[SCOPE_KEY]) if isinstance(raw[SCOPE_KEY], dict)
                         else InputScope.model_validate_json(str(raw[SCOPE_KEY])))
                note = "此读取范围随数据文件导入，未重新读取或核验原件；不恢复原文件关联。"
                if note not in scope.notes:
                    scope.notes.append(note)
                imported_scope = scope.model_dump_json()
            except ValueError as error:
                raise HTTPException(422, "导入文件中的知意输入范围说明损坏，未导入。请保留原导出说明或明确移除该说明列后再试。") from error
        values: dict[str, object | None] = {}
        for header, value in raw.items():
            header = str(header).strip()
            if header in {SCOPE_KEY, REVIEW_KEY}:
                continue
            if header in {"__row_group", "__header_labels"}:
                continue
            if header not in header_to_key:
                raise HTTPException(422, "导入文件包含无法识别的内部字段，请检查表头。")
            key = header_to_key[header]
            values[key] = import_cell(value, column_by_key[key].value_type, label=column_by_key[key].label, row_index=row_index)
        if values:
            group = raw.get("__row_group")
            if group is not None and not isinstance(group, str):
                raise HTTPException(422, "导入文件的来源分组无效。")
            values["__row_group"] = f"import:{import_batch}:group:{group}" if group else f"import:{import_batch}:row:{row_index}"
            normalized.append((values, imported_scope, review_pending))
    table.columns_json = json.dumps([column.model_dump() for column in columns], ensure_ascii=False)
    from document_pipeline_api.services.template_contract import _update_file_schemas
    _update_file_schemas(session, table_id, [column.model_dump() for column in columns])
    max_index = (
        session.scalar(
            select(func.max(DataRowRecord.item_index)).where(DataRowRecord.table_id == table_id)
        )
        or 0
    )
    now = utc_now()
    for values, imported_scope, review_pending in normalized:
        max_index += 1
        row = DataRowRecord(
            table_id=table_id,
            task_id=None,
            item_index=max_index,
            row_json=json.dumps(values, ensure_ascii=False),
            input_scope_json=imported_scope,
            review_pending=review_pending,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        session.add(
            DataRowRevisionRecord(
                row_id=row.id,
                table_id=table_id,
                task_id=None,
                version=1,
                operation="import_create",
                before_json=None,
                after_json=row.row_json,
                editor="import",
                created_at=now,
            )
        )
    session.commit()
    return len(normalized)


def create_table_from_import(
    session: Session,
    name: str,
    rows: list[dict[str, object]],
) -> DataTableRecord:
    """从文件表头创建独立表并导入；重复显示名必须先由用户重命名。"""
    internal_keys = {SCOPE_KEY, REVIEW_KEY, "__row_group", "__header_labels"}
    headers = list(dict.fromkeys(
        key for row in rows for key in row if key not in internal_keys
    )) if rows else []
    duplicate_headers = [header for header in headers if re.search(r"__重复\d+$", header)]
    if duplicate_headers:
        labels = sorted({re.sub(r"__重复\d+$", "", key) for key in duplicate_headers})
        raise HTTPException(
            status_code=422,
            detail=f"导入文件存在重复列名：{'、'.join(labels)}。请先重命名后再导入。",
        )
    header_labels = set(rows[0].get("__header_labels") or []) if rows else set()
    columns = []
    for index, header in enumerate(headers, start=1):
        label = re.sub(r"__重复\d+$", "", header)
        columns.append(
            {
                "key": f"import_{index}",
                "label": label,
                "value_type": infer_import_type(row.get(header) for row in rows),
                "section": "header" if header in header_labels else "item",
            }
        )
    table = DataTableRecord(
        id=str(uuid4()),
        name=name,
        template_key=f"import-{uuid4().hex[:12]}",
        template_version="1",
        document_kind=DocumentKind.CUSTOM.value,
        columns_json=json.dumps(columns, ensure_ascii=False),
    )
    session.add(table)
    session.flush()
    key_rows = [
        {
            **{f"import_{index}": raw.get(header) for index, header in enumerate(headers, start=1)},
            **({"__row_group": raw.get("__row_group")} if raw.get("__row_group") else {}),
            **({SCOPE_KEY: raw[SCOPE_KEY]} if raw.get(SCOPE_KEY) else {}),
            **({REVIEW_KEY: raw[REVIEW_KEY]} if raw.get(REVIEW_KEY) else {}),
        }
        for raw in rows
    ]
    import_table_rows(session, table.id, key_rows)
    return table
