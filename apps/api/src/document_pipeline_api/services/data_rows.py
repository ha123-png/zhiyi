import json

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from document_pipeline_api.domain.template_rules import validate_template_rules
from document_pipeline_api.domain.validation import validate_extraction
from document_pipeline_api.models import (
    DataRowRecord,
    DataRowRevisionRecord,
    DataTableRecord,
    ExtractionRecord,
    TaskRecord,
)
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.data_tables import (
    DataRowRead,
    DataRowRevisionRead,
    DataRowUpdate,
)
from document_pipeline_api.schemas.extraction import (
    DocumentExtraction,
    DocumentKind,
    TemplateExtraction,
)
from document_pipeline_api.services.template_processing import (
    build_template_extraction_model,
)
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

    valid_keys = {column.key for column in table_columns(session, table)}
    before = json.loads(row.row_json)
    _validate_row_changes(valid_keys, before, request.changes)

    # 手动新增的行（无来源任务）：不参与任务级校验与跨行传播，只更新本行
    if row.task_id is None:
        after = dict(before)
        after.update(request.changes)
        after_json = _canonical_json(after)
        if after_json == row.row_json:
            return data_row_read(row)
        # SQLAlchemy 的 bulk UPDATE 会同步当前 Session 中的 row 对象；必须在
        # execute 前冻结旧 JSON，否则修订记录的 before/after 会被写成相同值。
        before_json = row.row_json
        changed_at = utc_now()
        next_version = request.expected_version + 1
        result = session.execute(
            update(DataRowRecord)
            .where(
                DataRowRecord.id == row.id,
                DataRowRecord.table_id == row.table_id,
                DataRowRecord.row_version == request.expected_version,
            )
            .values(row_json=after_json, row_version=next_version, updated_at=changed_at)
        )
        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="相关数据已经更新，请刷新后再修改。",
            )
        session.add(
            DataRowRevisionRecord(
                row_id=row.id,
                table_id=row.table_id,
                task_id=None,
                version=next_version,
                operation="table_edit",
                before_json=before_json,
                after_json=after_json,
                editor=request.editor,
                created_at=changed_at,
            )
        )
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
    table_column_defs = table_columns(session, table)
    custom_header_keys = {
        column.key
        for column in table_column_defs
        if column.user_defined and column.section == "header"
    }
    custom_keys = {column.key for column in table_column_defs if column.user_defined}
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

    task = session.get(TaskRecord, row.task_id)
    if task is None:
        raise HTTPException(status_code=409, detail="这行数据缺少来源任务，不能修改。")
    # 附加列只属于数据仓库，不属于提取结果；校验模板时必须剥离，避免污染
    # 模型/模板契约，也避免用户填写备注后被误判为提取字段错误。
    validation_rows = {
        row_id: {
            key: value
            for key, value in values.items()
            if key not in custom_header_keys
            and key not in custom_keys
        }
        for row_id, values in prospective.items()
    }
    issues = (
        []
        if set(request.changes).issubset(custom_keys)
        else _validate_task_rows(session, task, task_rows, validation_rows)
    )
    if task.status == "completed" and issues:
        raise HTTPException(
            status_code=422,
            detail=f"修改后未通过检查：{issues[0].message}",
        )

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


def _validate_task_rows(
    session: Session,
    task: TaskRecord,
    rows: list[DataRowRecord],
    values_by_id: dict[int, dict[str, object | None]],
):
    extraction = session.get(ExtractionRecord, task.id)
    if extraction is None:
        raise HTTPException(status_code=409, detail="这行数据缺少提取结果，不能修改。")
    values = [values_by_id[row.id] for row in rows]
    if extraction.document_kind != DocumentKind.CUSTOM.value:
        try:
            result = _builtin_result_from_rows(values)
        except ValidationError as error:
            raise _type_error(error) from error
        return validate_extraction(result, DocumentKind(extraction.document_kind))
    if extraction.template_id is None or extraction.template_version is None:
        raise HTTPException(status_code=409, detail="这行数据缺少模板版本，不能修改。")
    template = get_template_version(
        session,
        extraction.template_id,
        extraction.template_version,
    )
    header_keys = {field.key for field in template.fields if field.section == "header"}
    item_keys = {field.key for field in template.fields if field.section == "item"}
    first = values[0]
    raw_result = {
        "header": {key: first.get(key) for key in header_keys},
        "items": [
            {key: value.get(key) for key in item_keys}
            for value in values
            if value.get("item_index") is not None
        ],
    }
    try:
        dynamic_result = build_template_extraction_model(template).model_validate(
            raw_result,
            strict=True,
        )
    except ValidationError as error:
        raise _type_error(error) from error
    result = TemplateExtraction.model_validate(dynamic_result.model_dump())
    return validate_template_rules(
        result,
        template.deterministic_rules,
        template.fields,
    )


def _builtin_result_from_rows(
    rows: list[dict[str, object | None]],
) -> DocumentExtraction:
    first = rows[0]
    return DocumentExtraction.model_validate(
        {
            **{key: first.get(key) for key in BUILTIN_HEADER_KEYS},
            "items": [
                {
                    "name": row.get("name"),
                    "specification": row.get("specification"),
                    "unit": row.get("unit"),
                    "quantity": row.get("quantity"),
                    "unit_price": row.get("unit_price"),
                    "amount": row.get("item_amount"),
                    "tax_rate": row.get("tax_rate"),
                    "tax_amount": row.get("item_tax_amount"),
                }
                for row in rows
                if row.get("item_index") is not None
            ],
        },
        strict=True,
    )


def _type_error(error: ValidationError) -> HTTPException:
    first = error.errors(include_url=False)[0]
    path = ".".join(str(part) for part in first["loc"])
    return HTTPException(
        status_code=422,
        detail=f"字段 {path} 的值类型不正确。",
    )


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
        previous = before.get(key)
        if value is None or previous is None:
            continue
        if isinstance(previous, bool):
            valid_type = isinstance(value, bool)
        elif isinstance(previous, (int, float)):
            valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            valid_type = isinstance(value, type(previous))
        if not valid_type:
            raise HTTPException(
                status_code=422,
                detail=f"字段 {key} 的值类型不正确。",
            )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
