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
from document_pipeline_api.services.current_data import row_values, get_current, revise_current
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
        is_header_only=row.is_header_only,
        review_pending=row.review_pending,
        input_scope=InputScope.model_validate_json(row.input_scope_json) if row.input_scope_json else None,
        id=row.id,
        task_id=row.task_id,
        item_index=row.item_index,
        version=row.row_version,
        values=row_values(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def data_rows_read(session, table, rows):
    from document_pipeline_api.services.data_groups import row_source_key, shared_header_values
    from document_pipeline_api.services.data_tables import table_columns
    columns = table_columns(session, table)
    shared = shared_header_values(session, table.id, [row_source_key(row) for row in rows if not row.task_id], columns)
    file_keys = [column.key for column in columns if column.section == 'header']
    return [data_row_read(row).model_copy(update={'shared_header_keys': file_keys if row.task_id
            else list(shared.get(row_source_key(row), {}))}) for row in rows]


def update_data_row(
    session: Session,
    table_id: str,
    row_id: int,
    request: DataRowUpdate,
) -> DataRowRead:
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    begin_write_snapshot(session)
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

    if table.collection_kind == "files" and row.task_id:
        from document_pipeline_api.schemas.data_tables import ColumnDef
        table_column_defs = [ColumnDef(**column) for column in json.loads(get_current(session, row.task_id).schema_json)]
    else:
        table_column_defs = table_columns(session, table)
    valid_keys = {column.key for column in table_column_defs}
    before = json.loads(row.row_json)
    _validate_row_changes(valid_keys, before, request.changes)
    from document_pipeline_api.services.current_data import check_column_values
    check_column_values(request.changes, [column.model_dump() for column in table_column_defs])

    # Independent groups retain shared fields only while all typed values agree.
    # A historically varied field is a per-row value, never a first-row override.
    if row.task_id is None:
        from document_pipeline_api.services.data_groups import effective_header_keys
        header_keys = effective_header_keys(session, row, table_column_defs)
        if request.expected_shared_header_keys is not None and (set(request.expected_shared_header_keys) & request.changes.keys()) != (header_keys & request.changes.keys()):
            raise HTTPException(409, "共享字段的范围已经变化，请刷新后再修改。")
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

    current = get_current(session, row.task_id)
    header_keys = {column["key"] for column in json.loads(current.schema_json) if column.get("section") == "header"}
    item_changes = {key: value for key, value in request.changes.items() if key not in header_keys}
    revise_current(
        session, row.task_id, expected_version=current.current_version,
        header_changes={key: value for key, value in request.changes.items() if key in header_keys},
        detail_changes={row.id: item_changes} if item_changes else {},
        editor=request.editor, edit_session_id=request.edit_session_id,
    )
    session.commit()
    session.refresh(row)
    return data_row_read(row)


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
    *, page: int = 1, page_size: int = 50,
) -> list[DataRowRevisionRead]:
    from document_pipeline_api.services.read_snapshot import begin_read_snapshot
    begin_read_snapshot(session)
    exists = session.scalar(select(DataRowRecord.id).where(DataRowRecord.id == row_id, DataRowRecord.table_id == table_id))
    past = session.scalar(select(DataRowRevisionRecord.id).where(DataRowRevisionRecord.row_id == row_id,
                           DataRowRevisionRecord.table_id == table_id,
                           DataRowRevisionRecord.task_id.is_(None)).limit(1))
    if session.get(DataTableRecord, table_id) is None or exists is None and past is None:
        raise HTTPException(status_code=404, detail="没有找到这行数据。")
    revisions = session.scalars(
        select(DataRowRevisionRecord)
        .where(
            DataRowRevisionRecord.row_id == row_id,
            DataRowRevisionRecord.table_id == table_id,
            # File-linked rows use the file revision contract. Pre-baseline
            # row copies must not reappear through this independent-row API.
            DataRowRevisionRecord.task_id.is_(None),
        )
        .order_by(DataRowRevisionRecord.version, DataRowRevisionRecord.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_read_revision(revision) for revision in revisions]


def _read_revision(revision):
    def values(raw):
        return {key: value for key, value in json.loads(raw).items() if not key.startswith('__')} if raw is not None else None
    return DataRowRevisionRead(
            id=revision.id,
            row_id=revision.row_id,
            version=revision.version,
            operation=revision.operation,
            before=values(revision.before_json),
            after=values(revision.after_json),
            editor=revision.editor,
            created_at=revision.created_at,
        )


def independent_history(session, table_id, *, row_id=None, page=1, page_size=20):
    from document_pipeline_api.services.read_snapshot import begin_read_snapshot
    begin_read_snapshot(session)
    if session.get(DataTableRecord, table_id) is None:
        raise HTTPException(404, '没有找到这个数据表。')
    predicates = [DataRowRevisionRecord.table_id == table_id, DataRowRevisionRecord.task_id.is_(None)]
    if row_id is not None:
        predicates.append(DataRowRevisionRecord.row_id == row_id)
    total = session.scalar(select(func.count()).select_from(DataRowRevisionRecord).where(*predicates)) or 0
    revisions = session.scalars(select(DataRowRevisionRecord).where(*predicates).order_by(
        DataRowRevisionRecord.created_at.desc(), DataRowRevisionRecord.id.desc(),
    ).offset((page - 1) * page_size).limit(page_size))
    return {'page': page, 'page_size': page_size, 'total': total,
            'revisions': [_read_revision(revision).model_dump(mode='json') for revision in revisions]}


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
