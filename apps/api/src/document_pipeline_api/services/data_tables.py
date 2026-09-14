from io import BytesIO, StringIO
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
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from document_pipeline_api.domain.tasks import TaskState, TaskStatus
from document_pipeline_api.models import (
    TemplateRecord,
    ConfirmedDocumentRecord,
    DataRowRecord,
    DataRowRevisionRecord,
    DataTableRecord,
    DataViewRecord,
    ExtractionRecord,
    ReviewRevisionRecord,
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
from document_pipeline_api.services.data_rows import (
    BUILTIN_HEADER_KEYS,
    data_row_read,
)
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
    session: Session,
    task_id: str,
    expected_review_version: int,
    *,
    expected_lease_token: str | None = None,
    target_table_id: str | None = None,
    filename: str | None = None,
) -> ConfirmationRead:
    existing = session.get(ConfirmedDocumentRecord, task_id)
    if existing is not None and expected_lease_token is not None:
        session.rollback()
        session.expire_all()
        raise TaskLeaseLostError("任务已由其他处理者完成，拒绝迟到的自动确认。")

    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if existing is None:
        expected_status = (
            TaskStatus.VALIDATING.value
            if expected_lease_token is not None
            else TaskStatus.NEEDS_REVIEW.value
        )
        if expected_lease_token is not None:
            if task.status != expected_status:
                raise HTTPException(
                    status_code=409,
                    detail="只有持有当前处理租约的任务可以自动入表。",
                )
        elif task.status not in {
            TaskStatus.NEEDS_REVIEW.value,
            TaskStatus.COMPLETED.value,
            TaskStatus.WAITING_FOR_TEMPLATE.value,
        }:
            # 已完成任务可能在目标表被删除后（确认记录级联删除）重新保存到表
            raise HTTPException(
                status_code=409,
                detail="只有待确认或已完成任务可以手动保存到表。",
            )
    else:
        # 手动"重新保存到表"：任务已完成/待确认都可，镜像同步覆盖现有行
        if task.status not in {
            TaskStatus.COMPLETED.value,
            TaskStatus.NEEDS_REVIEW.value,
        }:
            raise HTTPException(
                status_code=409,
                detail="这个任务当前不能保存到表。",
            )

    extraction = session.get(ExtractionRecord, task_id)
    if extraction is None:
        raise HTTPException(status_code=404, detail="这个任务还没有提取结果。")
    if target_table_id is not None:
        target = session.get(DataTableRecord, target_table_id)
        if target is None:
            raise HTTPException(status_code=404, detail="没有找到指定的数据表。")
        expected_key = (
            extraction.template_id
            if extraction.document_kind == DocumentKind.CUSTOM.value
            else extraction.document_kind
        )
        expected_version = (
            str(extraction.template_version)
            if extraction.document_kind == DocumentKind.CUSTOM.value
            else BUILTIN_TEMPLATE_VERSION
        )
        if target.template_key != expected_key or target.template_version != expected_version:
            raise HTTPException(
                status_code=409,
                detail="提取出字段与指定表不符，请重新选择指定表。",
            )
    review = session.scalar(
        select(ReviewRevisionRecord)
        .where(ReviewRevisionRecord.task_id == task_id)
        .order_by(ReviewRevisionRecord.version.desc())
        .limit(1)
    )
    current_version = review.version if review is not None else 0
    if existing is None and expected_review_version != current_version:
        raise HTTPException(
            status_code=409,
            detail="审核结果已经更新，请刷新后再确认。",
        )

    validation_json = review.validation_json if review is not None else extraction.validation_json
    issues = json.loads(validation_json)
    if any(issue.get("severity") == "error" and not issue.get("ignored") for issue in issues):
        raise HTTPException(
            status_code=422,
            detail="结果仍有阻断性错误，请修正或忽略后再确认。",
        )
    result_json = review.result_json if review is not None else extraction.result_json
    result = (
        TemplateExtraction.model_validate_json(result_json)
        if extraction.document_kind == DocumentKind.CUSTOM.value
        else DocumentExtraction.model_validate_json(result_json)
    )
    confirmed_at = utc_now()
    if target_table_id is not None:
        # 保存到表时显式指定目标表：记住用户的表选择，后续重新保存也进入该表
        task.target_table_id = target_table_id
    if expected_lease_token is not None:
        fenced_transition = session.execute(
            update(TaskRecord)
            .where(
                TaskRecord.id == task_id,
                TaskRecord.status == TaskStatus.VALIDATING.value,
                TaskRecord.lease_token == expected_lease_token,
            )
            .values(
                status=TaskStatus.COMPLETED.value,
                completed_at=confirmed_at,
                lease_token=None,
                lease_expires_at=None,
                failure_code=None,
                failure_message=None,
                updated_at=confirmed_at,
            )
        )
        if fenced_transition.rowcount != 1:
            session.rollback()
            session.expire_all()
            raise TaskLeaseLostError("任务租约已被替换，拒绝迟到的自动确认。")

    table, rows = _materialize_rows(session, task, extraction, result)
    session.execute(update(DataRowRecord).where(DataRowRecord.task_id == task.id).values(review_pending=False))
    if existing is None:
        confirmation = ConfirmedDocumentRecord(
            task_id=task_id,
            table_id=table.id,
            review_version=current_version,
            result_json=result_json,
            confirmed_at=confirmed_at,
        )
        session.add(confirmation)
    else:
        existing.table_id = table.id
        existing.review_version = current_version
        existing.result_json = result_json
        existing.confirmed_at = confirmed_at
    if expected_lease_token is None and existing is None:
        if task.status != TaskStatus.COMPLETED.value:
            task.status = (
                TaskState(status=TaskStatus(task.status))
                .transition_to(TaskStatus.COMPLETED)
                .status.value
            )
            # 任务完成时间：仅在实际进入 completed 时写入；
            # 已完成任务重新保存到表（镜像同步）保留首次完成时间。
            task.completed_at = confirmed_at
        task.lease_token = None
        task.lease_expires_at = None
        task.failure_code = None
        task.failure_message = None
        task.updated_at = confirmed_at
    from document_pipeline_api.services.file_names import confirm_file_name
    confirm_file_name(task, filename)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        existing = session.get(ConfirmedDocumentRecord, task_id)
        if existing is not None:
            return _confirmation_read(session, existing)
        raise HTTPException(status_code=409, detail="确认冲突，请刷新后重试。") from error
    return ConfirmationRead(
        task_id=task_id,
        table_id=table.id,
        table_name=table.name,
        review_version=current_version,
        row_count=len(rows),
        confirmed_at=confirmed_at,
    )


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
    confirmation = session.get(ConfirmedDocumentRecord, task_id)
    if confirmation is None:
        raise HTTPException(status_code=404, detail="这个任务还没有确认入表。")
    return _confirmation_read(session, confirmation)


def list_data_tables(session: Session) -> list[DataTableRead]:
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
    return [
        DataTableRead(
            id=table.id,
            name=table.name,
            template_key=table.template_key,
            template_version=table.template_version,
            document_kind=table.document_kind,
            row_count=row_count,
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
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    row_query = select(DataRowRecord).where(DataRowRecord.table_id == table_id)
    count_query = select(func.count(DataRowRecord.id)).where(DataRowRecord.table_id == table_id)
    if search:
        pattern = f"%{search}%"
        row_query = row_query.where(DataRowRecord.row_json.ilike(pattern))
        count_query = count_query.where(DataRowRecord.row_json.ilike(pattern))
    rows = session.scalars(
        row_query.order_by(*_row_order()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    row_count = session.scalar(count_query)
    return DataTableDetail(
        presentation=table_presentation(session, table),
        id=table.id,
        name=table.name,
        template_key=table.template_key,
        template_version=table.template_version,
        document_kind=table.document_kind,
        row_count=row_count or 0,
        created_at=table.created_at,
        columns=table_columns(session, table),
        page=page,
        page_size=page_size,
        rows=[data_row_read(row) for row in rows],
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
                section="header" if key in BUILTIN_HEADER_KEYS else "item",
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
    if record is None or record.is_system:
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
) -> ColumnDef:
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    normalized = label.strip()
    columns = _persisted_columns(session, table)
    identity = " ".join(normalized.split()).casefold()
    if any(" ".join(column.label.split()).casefold() == identity for column in columns):
        raise HTTPException(status_code=409, detail="列名已存在，请换一个名称。")
    column = ColumnDef(
        key=f"custom_{uuid4().hex[:12]}",
        label=normalized,
        value_type="text",
        section=section,
        user_defined=True,
    )
    columns.append(column)
    table.columns_json = json.dumps([item.model_dump() for item in columns], ensure_ascii=False)
    session.commit()
    return column


def delete_custom_column(session: Session, table_id: str, column_key: str) -> None:
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    columns = _persisted_columns(session, table)
    target = next((column for column in columns if column.key == column_key), None)
    if target is None:
        raise HTTPException(status_code=404, detail="没有找到这一列。")
    if not target.user_defined:
        raise HTTPException(status_code=409, detail="模板字段不能删除。")
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
            row.row_version += 1
            row.updated_at = utc_now()
    session.commit()


def _present_row_keys(session: Session, table_id: str) -> set[str]:
    """在 SQLite 内聚合 JSON 对象键，Python 侧内存只随列数增长。"""
    return set(
        session.execute(
            text(
                "SELECT DISTINCT json_each.key "
                "FROM data_rows, json_each(data_rows.row_json) "
                "WHERE data_rows.table_id = :table_id"
            ),
            {"table_id": table_id},
        ).scalars()
    )


def _row_order() -> list[object]:
    """数据行显示顺序：同任务明细按 item_index 原位排列（镜像同步保证删行重存回原位），
    无溯源（手动行/任务已删）的行排最后。"""
    return [
        DataRowRecord.task_id.is_(None),
        DataRowRecord.task_id,
        DataRowRecord.item_index,
        DataRowRecord.id,
    ]


def export_data_table(session: Session, table_id: str) -> StreamingResponse:
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _unique_sheet_name(table.name, set())
    keys, labels = _export_columns(session, table)
    from document_pipeline_api.services.export_scope import WorkbookInputScopes
    scopes = WorkbookInputScopes()
    source_header_keys = {column.key for column in table_columns(session, table) if column.section == "header"}
    # 与数据仓库淡绿表头保持一致：#f0fdf4 / #065f46 / #a7f3d0。
    header_fill = PatternFill("solid", fgColor="F0FDF4")
    for index, key in enumerate(keys, start=1):
        cell = sheet.cell(row=1, column=index, value=labels[key])
        cell.data_type = "s"
        cell.font = Font(bold=True, color="065F46")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    row_count = 1
    rows = session.scalars(
        select(DataRowRecord)
        .where(DataRowRecord.table_id == table_id)
        .order_by(*_row_order())
        .execution_options(yield_per=1000)
    )
    max_widths = [_display_width(labels[key]) for key in keys]
    task_spans: dict[str, list[int]] = {}
    for record in rows:
        row = json.loads(record.row_json)
        values = [_excel_safe(row.get(key)) for key in keys]
        sheet.append(values)
        row_count += 1
        scopes.record(sheet, row_count, record, keys, source_header_keys)
        for index, value in enumerate(values):
            max_widths[index] = max(max_widths[index], _display_width(value))
        group_key = record.task_id or row.get("__row_group")
        if group_key:
            task_spans.setdefault(str(group_key), []).append(row_count)
    sheet.freeze_panes = "A2"
    if keys:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(keys))}{row_count}"
    column_defs = {column.key: column for column in table_columns(session, table)}
    header_keys = {
        key for key in keys if key in column_defs and column_defs[key].section == "header"
    }
    # 数据仓库把同一单据的所有抬头字段纵向合并；XLSX 必须保持所见即所得，
    # 不只合并用户新增的抬头列。
    for key in header_keys:
        column_index = keys.index(key) + 1
        for span in task_spans.values():
            if len(span) > 1:
                sheet.merge_cells(
                    start_row=span[0],
                    start_column=column_index,
                    end_row=span[-1],
                    end_column=column_index,
                )
                sheet.cell(span[0], column_index).alignment = Alignment(
                    vertical="center", wrap_text=True
                )
    for index, key in enumerate(keys, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = min(
            max(max_widths[index - 1] + 2, 10),
            48,
        )

    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    scopes.finish(workbook)
    workbook.save(output)
    output.seek(0)
    filename = f"{table.name}-{table.template_version}.xlsx"
    disposition = f"attachment; filename=table-{table.id}.xlsx; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        output,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": disposition},
        background=BackgroundTask(output.close),
    )


def export_data_table_views(session: Session, table_id: str) -> StreamingResponse:
    """把当前表的全部分 Sheet 视图导出为一个多工作表 XLSX。"""
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    views = session.scalars(
        select(DataViewRecord)
        .where(DataViewRecord.table_id == table_id)
        .order_by(DataViewRecord.created_at, DataViewRecord.name)
    ).all()
    if not views:
        raise HTTPException(status_code=422, detail="当前数据表还没有分 Sheet 视图。")
    all_rows = session.scalars(
        select(DataRowRecord)
        .where(DataRowRecord.table_id == table_id)
        .order_by(*_row_order())
    ).all()
    keys, labels = _export_columns(session, table)
    column_defs = {column.key: column for column in table_columns(session, table)}
    workbook = Workbook()
    workbook.remove(workbook.active)
    from document_pipeline_api.services.export_scope import WorkbookInputScopes
    scopes = WorkbookInputScopes()
    used_names: set[str] = set()
    matched_ids: set[int] = set()
    for view in views:
        expected = json.loads(view.field_value_json)
        rows = [
            row for row in all_rows
            if (row.task_id if view.field_key == "" else json.loads(row.row_json).get(view.field_key)) == expected
        ]
        matched_ids.update(row.id for row in rows)
        sheet = workbook.create_sheet(_unique_sheet_name(view.name, used_names))
        _populate_export_sheet(sheet, rows, keys, labels, column_defs, scopes)
    unmatched = [row for row in all_rows if row.id not in matched_ids]
    if unmatched:
        sheet = workbook.create_sheet(_unique_sheet_name("未分组", used_names))
        _populate_export_sheet(sheet, unmatched, keys, labels, column_defs, scopes)
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    scopes.finish(workbook)
    workbook.save(output)
    output.seek(0)
    filename = f"{table.name}-分Sheet.xlsx"
    disposition = f"attachment; filename=table-{table.id}-views.xlsx; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
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


def _populate_export_sheet(sheet, rows, keys, labels, column_defs, scopes) -> None:
    header_fill = PatternFill("solid", fgColor="F0FDF4")
    for index, key in enumerate(keys, start=1):
        cell = sheet.cell(row=1, column=index, value=labels[key])
        cell.data_type = "s"
        cell.font = Font(bold=True, color="065F46")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    max_widths = [_display_width(labels[key]) for key in keys]
    task_spans: dict[str, list[int]] = {}
    row_number = 1
    for record in rows:
        values_map = json.loads(record.row_json)
        values = [_excel_safe(values_map.get(key)) for key in keys]
        sheet.append(values)
        row_number += 1
        scopes.record(sheet, row_number, record, keys, {key for key, column in column_defs.items() if column.section == "header"})
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
            if len(span) > 1:
                sheet.merge_cells(start_row=span[0], start_column=column_index, end_row=span[-1], end_column=column_index)
                sheet.cell(span[0], column_index).alignment = Alignment(vertical="center", wrap_text=True)
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
    # 指定目标表：存在则直接使用（任意表，行数据按字段并集写入，显示按表契约裁剪）；
    # 指定表已被删除则回退到模板唯一表（重建），保证"保存到表 = 数据重新存在"。
    if target_table_id:
        target = session.get(DataTableRecord, target_table_id)
        if target is not None:
            return target
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
    session: Session,
    task: TaskRecord,
    extraction: ExtractionRecord,
    result: DocumentExtraction | TemplateExtraction,
) -> tuple[DataTableRecord, list[dict[str, object | None]]]:
    table = _get_or_create_table(
        session,
        extraction,
        target_table_id=task.target_table_id,
    )
    rows = _flatten_result(task.filename, result)
    existing = {
        row.item_index: row
        for row in session.scalars(
            select(DataRowRecord).where(DataRowRecord.task_id == task.id)
        ).all()
    }
    user_edited_row_ids = set(
        session.scalars(
            select(DataRowRevisionRecord.row_id)
            .where(
                DataRowRevisionRecord.task_id == task.id,
                DataRowRevisionRecord.operation == "table_edit",
            )
            .distinct()
        ).all()
    )
    changed_at = utc_now()
    for values in rows:
        after_json = _canonical_json(values)
        index = values.get("item_index")
        if not isinstance(index, int):
            raise RuntimeError("提取结果缺少明细序号，无法写入数据表。")
        current = existing.pop(index, None)
        if current is None:
            current = DataRowRecord(
                table_id=table.id,
                task_id=task.id,
                item_index=index,
                row_json=after_json,
                input_scope_json=extraction.input_scope_json,
                review_pending=task.status != TaskStatus.COMPLETED.value,
                row_version=1,
                created_at=changed_at,
                updated_at=changed_at,
            )
            session.add(current)
            session.flush()
            session.add(
                DataRowRevisionRecord(
                    row_id=current.id,
                    table_id=table.id,
                    task_id=task.id,
                    version=1,
                    operation="materialize_create",
                    before_json=None,
                    after_json=after_json,
                    editor="system",
                    created_at=changed_at,
                )
            )
            continue
        current.review_pending = task.status != TaskStatus.COMPLETED.value
        if current.id in user_edited_row_ids:
            # 用户改过的事实值优先，重新物化不得覆盖；但“保存到另一张表”是明确的
            # 路由操作，仍必须移动该行，否则确认记录指向新表、用户行却滞留旧表。
            if current.table_id != table.id:
                preserved_json = current.row_json
                current.table_id = table.id
                current.row_version += 1
                current.updated_at = changed_at
                session.add(
                    DataRowRevisionRecord(
                        row_id=current.id,
                        table_id=table.id,
                        task_id=current.task_id,
                        version=current.row_version,
                        operation="materialize_move",
                        before_json=preserved_json,
                        after_json=preserved_json,
                        editor="system",
                        created_at=changed_at,
                    )
                )
            continue
        current.input_scope_json = extraction.input_scope_json
        if current.row_json == after_json and current.table_id == table.id:
            continue
        before_json = current.row_json
        current.table_id = table.id
        current.row_json = after_json
        current.row_version += 1
        current.updated_at = changed_at
        session.add(
            DataRowRevisionRecord(
                row_id=current.id,
                table_id=table.id,
                task_id=task.id,
                version=current.row_version,
                operation="materialize_update",
                before_json=before_json,
                after_json=after_json,
                editor="system",
                created_at=changed_at,
            )
        )
    for removed in existing.values():
        if removed.id in user_edited_row_ids:
            continue
        session.add(
            DataRowRevisionRecord(
                row_id=removed.id,
                table_id=removed.table_id,
                task_id=removed.task_id,
                version=removed.row_version + 1,
                operation="materialize_delete",
                before_json=removed.row_json,
                after_json=None,
                editor="system",
                created_at=changed_at,
            )
        )
        session.delete(removed)
    session.flush()
    return table, rows


def _flatten_result(
    filename: str,
    result: DocumentExtraction | TemplateExtraction,
) -> list[dict[str, object | None]]:
    if isinstance(result, TemplateExtraction):
        items = result.items or [None]
        return [
            {
                "source_filename": filename,
                **result.header,
                "item_index": index if item is not None else 1,
                **(item or {}),
            }
            for index, item in enumerate(items, start=1)
        ]
    header = result.model_dump(exclude={"items"})
    items = result.items or [None]
    rows: list[dict[str, object | None]] = []
    for index, item in enumerate(items, start=1):
        item_values = item.model_dump() if item is not None else {}
        rows.append(
            {
                "source_filename": filename,
                **header,
                "item_index": index if item is not None else 1,
                "name": item_values.get("name"),
                "specification": item_values.get("specification"),
                "unit": item_values.get("unit"),
                "quantity": item_values.get("quantity"),
                "unit_price": item_values.get("unit_price"),
                "item_amount": item_values.get("amount"),
                "tax_rate": item_values.get("tax_rate"),
                "item_tax_amount": item_values.get("tax_amount"),
                "item_remarks": item_values.get("remarks"),
            }
        )
    return rows


def _export_columns(
    session: Session,
    table: DataTableRecord,
) -> tuple[list[str], dict[str, str]]:
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


def _confirmation_read(
    session: Session,
    confirmation: ConfirmedDocumentRecord,
) -> ConfirmationRead:
    table = session.get(DataTableRecord, confirmation.table_id)
    if table is None:
        raise RuntimeError("已确认文档引用的数据表不存在。")
    row_count = session.scalar(
        select(func.count(DataRowRecord.id)).where(DataRowRecord.task_id == confirmation.task_id)
    )
    return ConfirmationRead(
        task_id=confirmation.task_id,
        table_id=confirmation.table_id,
        table_name=table.name,
        review_version=confirmation.review_version,
        row_count=row_count or 0,
        confirmed_at=confirmation.confirmed_at,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _excel_safe(value: object | None) -> object | None:
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
                select(func.count(DataRowRecord.id)).where(DataRowRecord.table_id == table.id)
            )
            or 0
        )
    return DataTableRead(
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
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    row_ids = session.scalars(
        select(DataRowRecord.id).where(DataRowRecord.table_id == table_id)
    ).all()
    if row_ids:
        session.execute(
            delete(DataRowRevisionRecord).where(DataRowRevisionRecord.row_id.in_(row_ids))
        )
        session.execute(delete(DataRowRecord).where(DataRowRecord.table_id == table_id))
    session.execute(delete(DataViewRecord).where(DataViewRecord.table_id == table_id))
    session.execute(
        delete(ConfirmedDocumentRecord).where(ConfirmedDocumentRecord.table_id == table_id)
    )
    session.delete(table)
    session.commit()


def create_table(session: Session, name: str, template_key: str) -> DataTableRead:
    """从指定模板创建一张空表。

    设计决定：手动新建表必须基于模板——空白表没有字段定义，前端没有列名、
    新增行也无处填写，没有实际用途。列契约快照进 columns_json，因此
    表名/列名都可显示中文且与模板后续改动解耦。
    """
    template = get_template(session, template_key)
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
    # 与 update_data_row 一致：只允许写入列契约内的字段，契约外字段（如被裁剪的幽灵列）
    # 静默丢弃，避免手动行携带未知键污染列裁剪与导出。
    valid_keys = {column.key for column in table_columns(session, table)}
    cleaned: dict[str, object | None] = {}
    for key, value in values.items():
        if key not in valid_keys:
            continue
        if isinstance(value, (dict, list)):
            raise HTTPException(
                status_code=422,
                detail=f"字段 {key} 只能保存文字、数字、真假值或空值。",
            )
        cleaned[key] = value
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


def delete_rows(session: Session, table_id: str, row_ids: list[int]) -> int:
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    rows = session.scalars(
        select(DataRowRecord).where(
            DataRowRecord.table_id == table_id,
            DataRowRecord.id.in_(row_ids),
        )
    ).all()
    found = {row.id for row in rows}
    missing = set(row_ids) - found
    if missing:
        raise HTTPException(
            status_code=404,
            detail="部分数据行不存在或不属于这张表。",
        )
    for row in rows:
        session.execute(delete(DataRowRevisionRecord).where(DataRowRevisionRecord.row_id == row.id))
        session.delete(row)
    session.commit()
    return len(rows)


def merge_tables(
    session: Session,
    table_ids: list[str],
    name: str,
) -> DataTableRead:
    """把多张表的行合并成一张新表。

    列契约取各表列定义的并集（先出现的列优先）；行按来源表顺序依次追加，
    保留原 task_id 以便溯源，item_index 重新编号。合并表是独立表，
    template_key 用唯一标记，列契约快照进 columns_json。
    """
    tables: list[DataTableRecord] = []
    for table_id in table_ids:
        table = session.get(DataTableRecord, table_id)
        if table is None:
            raise HTTPException(status_code=404, detail="没有找到数据表。")
        tables.append(table)
    # 字段并集按用户看到的列名判断，而不是内部 key，也不按“第几次出现”
    # 再拆列。同名字段无论来自内置模板、导入表还是历史表，都只能成为一列。
    columns_by_label: dict[str, ColumnDef] = {}
    source_key_maps: dict[str, dict[str, str]] = {}
    for table in tables:
        key_map: dict[str, str] = {}
        source_labels: set[str] = set()
        for column in table_columns(session, table):
            normalized_label = " ".join(column.label.split()).casefold()
            if normalized_label in source_labels:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"数据表“{table.name}”存在重复列名“{column.label}”，"
                        "为避免丢失信息，请先重命名后再合并。"
                    ),
                )
            source_labels.add(normalized_label)
            canonical = columns_by_label.get(normalized_label)
            if canonical is None:
                # 合并表拥有自己的列身份。不同来源表可能碰巧复用同一内部 key，
                # 若直接沿用就会让两个不同列互相串值。
                canonical = column.model_copy(
                    update={"key": f"merged_{uuid4().hex[:12]}"}
                )
                columns_by_label[normalized_label] = canonical
            key_map[column.key] = canonical.key
        source_key_maps[table.id] = key_map
    columns = list(columns_by_label.values())
    merged = DataTableRecord(
        id=str(uuid4()),
        name=name,
        template_key=f"merged-{uuid4().hex[:12]}",
        template_version="1",
        document_kind=DocumentKind.CUSTOM.value,
        columns_json=json.dumps(
            [column.model_dump() for column in columns],
            ensure_ascii=False,
        ),
    )
    session.add(merged)
    session.flush()
    index = 1
    for table in tables:
        for row in session.scalars(
            select(DataRowRecord)
            .where(DataRowRecord.table_id == table.id)
            .order_by(DataRowRecord.id)
        ).all():
            source_values = json.loads(row.row_json)
            source_group = row.task_id or source_values.get("__row_group") or f"row-{row.id}"
            merged_row: dict[str, object | None] = {}
            # 只复制这张来源表列契约内的值。历史行 JSON 可能残留旧模板内部键，
            # 它们不是用户看到的列，绝不能越过“列名并集”混入合并结果。
            for source_key, canonical_key in source_key_maps[table.id].items():
                if source_key in source_values:
                    merged_row[canonical_key] = source_values[source_key]
            created = DataRowRecord(
                table_id=merged.id,
                # 合并表是来源行的独立副本。继续复用 task_id 会违反
                # uq_data_row_task_item；用内部行组保留界面/XLSX 合并语义。
                task_id=None,
                item_index=index,
                row_json=json.dumps(merged_row, ensure_ascii=False),
                input_scope_json=row.input_scope_json,
                review_pending=row.review_pending,
                row_version=1,
            )
            merged_values = json.loads(created.row_json)
            merged_values["__row_group"] = f"{table.id}:{source_group}"
            created.row_json = json.dumps(merged_values, ensure_ascii=False)
            session.add(created)
            session.flush()
            session.add(
                DataRowRevisionRecord(
                    row_id=created.id,
                    table_id=merged.id,
                    task_id=created.task_id,
                    version=1,
                    operation="merge_create",
                    before_json=None,
                    after_json=created.row_json,
                    editor="merge",
                )
            )
            index += 1
    session.commit()
    return _table_read(session, merged)


def export_data_table_csv(session: Session, table_id: str) -> StreamingResponse:
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    keys, labels = _export_columns(session, table)
    scoped = table_has_input_scopes(session, table_id)
    pending = table_has_pending_reviews(session, table_id)
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
        .where(DataRowRecord.table_id == table_id)
        .order_by(*_row_order())
        .execution_options(yield_per=1000)
    )
    for row_json in row_jsons:
        row = export_row_values(row_json)
        write_csv_row([_csv_safe(row.get(key)) for key in keys] + ([csv_scope_value(row)] if scoped else []) + (["待核对" if row.get(REVIEW_KEY) else ""] if pending else []))
    output.seek(0)
    filename = f"{table.name}.csv"
    disposition = f"attachment; filename=table-{table.id}.csv; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        output,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition},
        background=BackgroundTask(output.close),
    )


def export_data_table_json(session: Session, table_id: str) -> StreamingResponse:
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    output.write(b"[")
    first = True
    row_jsons = session.scalars(
        select(DataRowRecord)
        .where(DataRowRecord.table_id == table_id)
        .order_by(*_row_order())
        .execution_options(yield_per=1000)
    )
    for row_json in row_jsons:
        if not first:
            output.write(b",")
        output.write(json.dumps(export_row_values(row_json), ensure_ascii=False).encode("utf-8"))
        first = False
    output.write(b"]")
    output.seek(0)
    filename = f"{table.name}.json"
    disposition = f"attachment; filename=table-{table.id}.json; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        output,
        media_type="application/json",
        headers={"Content-Disposition": disposition},
        background=BackgroundTask(output.close),
    )


def _csv_safe(value: object | None) -> object | None:
    return _excel_safe(value)


class ImportFileError(ValueError):
    """导入文件不安全、损坏或超出结构限制。"""


def parse_import_rows(
    content: bytes,
    filename: str,
    *,
    max_rows: int = 100_000,
    max_columns: int = 512,
    max_uncompressed_bytes: int = 256 * 1024 * 1024,
) -> list[dict[str, object]]:
    """把 xlsx/csv 内容解析成行字典（第一行为表头）。空行跳过。"""
    if filename.lower().endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
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
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            expanded_size = sum(info.file_size for info in archive.infolist())
            if expanded_size > max_uncompressed_bytes:
                raise ImportFileError("Excel 文件解压后过大，已拒绝导入。")
        workbook = load_workbook(BytesIO(content), read_only=False, data_only=True)
        try:
            sheet = workbook.active
            from document_pipeline_api.services.export_scope import read_workbook_scopes
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
        if len(row) > max_columns:
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
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
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
        for raw_header in rows[0]:
            header = str(raw_header).strip()
            if header.startswith("__"):
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
                        value_type="text",
                        section="header" if base_label in imported_header_labels else "item",
                        user_defined=True,
                    )
                )
            header_to_key[header] = key
            if base_label in imported_header_labels:
                for index, column in enumerate(columns):
                    if column.key == key and column.section != "header":
                        columns[index] = column.model_copy(update={"section": "header"})
        table.columns_json = json.dumps(
            [column.model_dump() for column in columns], ensure_ascii=False
        )
    normalized: list[tuple[dict[str, object | None], str | None, bool | None]] = []
    for raw in rows:
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
                if value:
                    values[header] = value
                continue
            if header not in header_to_key:
                raise HTTPException(422, "导入文件包含无法识别的内部字段，请检查表头。")
            key = header_to_key[header]
            if isinstance(value, str) and value.strip() == "":
                value = None
            if isinstance(value, (dict, list)):
                continue
            values[key] = value
        if values:
            normalized.append((values, imported_scope, review_pending))
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
    headers = [key for key in rows[0].keys() if not key.startswith("__")] if rows else []
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
                "value_type": "text",
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
