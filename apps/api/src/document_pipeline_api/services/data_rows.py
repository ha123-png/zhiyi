import json

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from document_pipeline_api.models import (
    DataRowRecord,
    DataRowRevisionRecord,
    DataTableRecord,
    ExtractionRecord,
)
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.data_tables import (
    DataRowRead,
    DataRowRevisionRead,
    DataRowUpdate,
)
from document_pipeline_api.schemas.extraction import DocumentKind
from document_pipeline_api.schemas.input_scope import InputScope
from document_pipeline_api.services.templates import get_template_version


BUILTIN_HEADER_KEYS = {
    "document_type",
    "seller_name",
    "buyer_name",
    "seller_tax_id",
    "buyer_tax_id",
    "seller_contact",
    "buyer_contact",
    "buyer_address",
    "document_number",
    "document_date",
    "amount_before_tax",
    "tax_amount",
    "total_amount",
    "total_quantity",
    "remarks",
}
BUILTIN_ITEM_KEYS = {
    "name",
    "specification",
    "unit",
    "quantity",
    "unit_price",
    "item_amount",
    "tax_rate",
    "item_tax_amount",
    "item_remarks",
}


def data_row_read(row: DataRowRecord) -> DataRowRead:
    return DataRowRead(
        review_pending=row.review_pending,
        input_scope=InputScope.model_validate_json(row.input_scope_json) if row.input_scope_json else None,
        id=row.id,
        task_id=row.task_id,
        item_index=row.item_index,
        version=row.row_version,
        values=json.loads(row.row_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def update_data_row(
    session: Session,
    table_id: str,
    row_id: int,
    request: DataRowUpdate,
) -> DataRowRead:
    row = session.scalar(
        select(DataRowRecord).where(
            DataRowRecord.id == row_id,
            DataRowRecord.table_id == table_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="没有找到这行数据。")
    if request.expected_version != row.row_version:
        raise HTTPException(status_code=409, detail="这行数据已经更新，请刷新后再修改。")

    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    # 合法字段集 = 表的列契约（与前端可编辑列一致），而不是该行现有字段：
    # 新增的空行或原本缺失字段的行，在编辑缺失字段时不应被误判为"表里没有该字段"。
    from document_pipeline_api.services.data_tables import table_columns

    table_column_defs = table_columns(session, table)
    valid_keys = {column.key for column in table_column_defs}
    before = json.loads(row.row_json)
    _validate_row_changes(valid_keys, before, request.changes)

    # 合并表没有 task_id，但用 __row_group 表示同一份原文件。表头在界面上是
    # 跨明细合并单元格，因此编辑时也必须同步到同组所有行并分别留下修订；
    # 否则选择同组第二条明细查看历史时，会误以为表头从未修改。
    if row.task_id is None:
        header_keys = {
            column.key for column in table_column_defs if column.section == "header"
        }
        row_group = before.get("__row_group")
        propagated = header_keys & request.changes.keys()
        related_rows = [row]
        if row_group and propagated:
            related_rows = session.scalars(
                select(DataRowRecord).where(
                    DataRowRecord.table_id == table_id,
                    func.json_extract(DataRowRecord.row_json, "$.__row_group") == row_group,
                )
            ).all()
        changed_at = utc_now()
        for current in related_rows:
            before_json = current.row_json
            current_before = json.loads(before_json)
            changes = (
                request.changes
                if current.id == row.id
                else {key: request.changes[key] for key in propagated}
            )
            current_after = {**current_before, **changes}
            after_json = _canonical_json(current_after)
            if after_json == current.row_json:
                continue
            expected_version = (
                request.expected_version if current.id == row.id else current.row_version
            )
            next_version = expected_version + 1
            result = session.execute(
                update(DataRowRecord)
                .where(
                    DataRowRecord.id == current.id,
                    DataRowRecord.table_id == current.table_id,
                    DataRowRecord.row_version == expected_version,
                )
                .values(
                    row_json=after_json,
                    row_version=next_version,
                    updated_at=changed_at,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="相关数据已经更新，请刷新后再修改。",
                )
            session.add(DataRowRevisionRecord(
                row_id=current.id,
                table_id=current.table_id,
                task_id=None,
                version=next_version,
                operation="table_edit",
                before_json=before_json,
                after_json=after_json,
                editor=request.editor,
                created_at=changed_at,
            ))
        session.commit()
        updated_row = session.get(DataRowRecord, row.id)
        if updated_row is None:
            raise RuntimeError("更新后的数据行不存在。")
        return data_row_read(updated_row)

    task_rows = session.scalars(
        select(DataRowRecord)
        .where(DataRowRecord.task_id == row.task_id)
        .order_by(DataRowRecord.item_index)
    ).all()
    custom_header_keys = {
        column.key
        for column in table_column_defs
        if column.user_defined and column.section == "header"
    }
    header_keys = _header_keys_for_task(session, row.task_id) | custom_header_keys
    propagated = header_keys & request.changes.keys()
    prospective: dict[int, dict[str, object | None]] = {
        current.id: json.loads(current.row_json) for current in task_rows
    }
    prospective[row.id].update(request.changes)
    for current in task_rows:
        if current.id == row.id:
            continue
        prospective[current.id].update(
            {key: request.changes[key] for key in propagated}
        )

    # 数据仓库是确认结果的下游副本。这里的编辑只改变数据表，不回写提取结果、
    # 文件历史或任务校验；否则用户整理已入库数据会被旧模板规则反向阻断。

    changed_at = utc_now()
    for current in task_rows:
        after_json = _canonical_json(prospective[current.id])
        if after_json == current.row_json:
            continue
        before_json = current.row_json
        expected_version = (
            request.expected_version if current.id == row.id else current.row_version
        )
        next_version = expected_version + 1
        result = session.execute(
            update(DataRowRecord)
            .where(
                DataRowRecord.id == current.id,
                DataRowRecord.table_id == current.table_id,
                DataRowRecord.row_version == expected_version,
            )
            .values(
                row_json=after_json,
                row_version=next_version,
                updated_at=changed_at,
            )
        )
        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="相关数据已经更新，请刷新后再修改。",
            )
        session.add(
            DataRowRevisionRecord(
                row_id=current.id,
                table_id=current.table_id,
                task_id=current.task_id,
                version=next_version,
                operation="table_edit",
                before_json=before_json,
                after_json=after_json,
                editor=request.editor,
                created_at=changed_at,
            )
        )
    session.commit()
    updated_row = session.get(DataRowRecord, row_id)
    if updated_row is None:
        raise RuntimeError("更新后的数据行不存在。")
    return data_row_read(updated_row)


def _header_keys_for_task(session: Session, task_id: str) -> set[str]:
    extraction = session.get(ExtractionRecord, task_id)
    if extraction is None or extraction.document_kind != DocumentKind.CUSTOM.value:
        return BUILTIN_HEADER_KEYS
    if extraction.template_id is None or extraction.template_version is None:
        raise HTTPException(status_code=409, detail="这行数据缺少模板版本，不能修改。")
    template = get_template_version(
        session,
        extraction.template_id,
        extraction.template_version,
    )
    return {field.key for field in template.fields if field.section == "header"}


def list_row_revisions(
    session: Session,
    table_id: str,
    row_id: int,
) -> list[DataRowRevisionRead]:
    row = session.scalar(
        select(DataRowRecord.id).where(
            DataRowRecord.id == row_id,
            DataRowRecord.table_id == table_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="没有找到这行数据。")
    revisions = session.scalars(
        select(DataRowRevisionRecord)
        .where(
            DataRowRevisionRecord.row_id == row_id,
            DataRowRevisionRecord.table_id == table_id,
        )
        .order_by(DataRowRevisionRecord.version)
    ).all()
    return [
        DataRowRevisionRead(
            id=revision.id,
            row_id=revision.row_id,
            version=revision.version,
            operation=revision.operation,
            before=(
                json.loads(revision.before_json)
                if revision.before_json is not None
                else None
            ),
            after=(
                json.loads(revision.after_json)
                if revision.after_json is not None
                else None
            ),
            editor=revision.editor,
            created_at=revision.created_at,
        )
        for revision in revisions
    ]


def _validate_row_changes(
    valid_keys: set[str],
    before: dict[str, object | None],
    changes: dict[str, object | None],
) -> None:
    immutable = {"source_filename", "item_index"}
    blocked = immutable & changes.keys()
    if blocked:
        raise HTTPException(
            status_code=422,
            detail=f"不能修改来源身份字段：{', '.join(sorted(blocked))}。",
        )
    unknown = changes.keys() - valid_keys
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"数据表中不存在字段：{', '.join(sorted(unknown))}。",
        )
    for key, value in changes.items():
        if isinstance(value, (dict, list)):
            raise HTTPException(
                status_code=422,
                detail=f"字段 {key} 只能保存文字、数字、真假值或空值。",
            )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
