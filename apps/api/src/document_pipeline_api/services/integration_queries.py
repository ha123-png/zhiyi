import csv
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.models import DataRowRecord, DataTableRecord
from document_pipeline_api.services.data_tables import get_data_table, table_columns, _csv_safe
from document_pipeline_api.services.export_scope import SCOPE_LABEL, csv_scope_value, export_row_values, table_has_input_scopes


def aggregate_data_table(
    session: Session,
    table_id: str,
    *,
    value_field: str | None = None,
    group_by: str | None = None,
    search: str | None = None,
) -> dict[str, object]:
    """按表列契约执行安全聚合；字段不是任意 SQL，最多返回 100 个分组。"""
    table = session.get(DataTableRecord, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="没有找到这个数据表。")
    allowed = {column.key for column in table_columns(session, table)}
    for field in (value_field, group_by):
        if field is not None and field not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"字段不在数据表列契约中：{field}",
            )
    filters = [DataRowRecord.table_id == table_id]
    if search:
        filters.append(DataRowRecord.row_json.ilike(f"%{search}%"))
    if group_by is None:
        count = int(
            session.scalar(select(func.count(DataRowRecord.id)).where(*filters)) or 0
        )
        total = None
        if value_field:
            value_expr = func.json_extract(
                DataRowRecord.row_json,
                f'$."{value_field}"',
            )
            total = float(
                session.scalar(
                    select(func.coalesce(func.sum(value_expr), 0)).where(*filters)
                )
                or 0
            )
        return {"count": count, "sum": total, "groups": []}
    group_expr = func.json_extract(
        DataRowRecord.row_json,
        f'$."{group_by}"',
    )
    columns = [group_expr.label("group_value"), func.count(DataRowRecord.id)]
    if value_field:
        columns.append(
            func.coalesce(
                func.sum(
                    func.json_extract(
                        DataRowRecord.row_json,
                        f'$."{value_field}"',
                    )
                ),
                0,
            )
        )
    rows = session.execute(
        select(*columns)
        .where(*filters)
        .group_by(group_expr)
        .order_by(func.count(DataRowRecord.id).desc())
        .limit(100)
    ).all()
    return {
        "count": sum(int(row[1]) for row in rows),
        "sum": sum(float(row[2] or 0) for row in rows) if value_field else None,
        "groups": [
            {
                "value": row[0],
                "count": int(row[1]),
                "sum": float(row[2] or 0) if value_field else None,
            }
            for row in rows
        ],
    }


def export_data_table_file(
    session: Session,
    table_id: str,
    path: Path,
    output_format: Literal["csv", "json"],
) -> int:
    """流式导出事实表到新文件；调用者负责路径授权并保证不覆盖。"""
    detail = get_data_table(session, table_id, page=1, page_size=1)
    columns = [column.key for column in detail.columns]
    scoped = table_has_input_scopes(session, table_id)
    if scoped and SCOPE_LABEL in [column.label for column in detail.columns]:
        raise ValueError("业务列名与知意范围说明列冲突，请重命名后导出。")
    if output_format == "csv":
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([column.label for column in detail.columns] + ([SCOPE_LABEL] if scoped else []))
            for values in _iter_table_values(session, table_id):
                writer.writerow([_csv_safe(values.get(column)) for column in columns] + ([csv_scope_value(values)] if scoped else []))
    else:
        with path.open("w", encoding="utf-8") as stream:
            stream.write("[")
            first = True
            for values in _iter_table_values(session, table_id):
                if not first:
                    stream.write(",")
                stream.write(json.dumps(values, ensure_ascii=False))
                first = False
            stream.write("]")
    return detail.row_count


def _iter_table_values(session: Session, table_id: str):
    row_jsons = session.scalars(
        select(DataRowRecord)
        .where(DataRowRecord.table_id == table_id)
        .order_by(DataRowRecord.id)
        .execution_options(yield_per=1000)
    )
    for row_json in row_jsons:
        yield export_row_values(row_json)
