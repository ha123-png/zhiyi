from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from pydantic import ValidationError

from document_pipeline_api.models.dashboard import DashboardCard
from document_pipeline_api.models import DataTableRecord
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.schemas.dashboard import CardInput
from document_pipeline_api.services.assistant_analysis import AnalysisRequest, Filter, Metric, analyze
from document_pipeline_api.services.data_tables import table_columns

CARD_LIMIT = 4


def range_start(days):
    return (datetime.now().astimezone() - timedelta(days=max(1, min(days, 366)) - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)


def card_read(card):
    definition = json.loads(card.definition_json)
    definition.pop("field_contracts", None)
    return {**definition, "id": card.id, "name": card.name, "position": card.position,
            "created_at": card.created_at, "updated_at": card.updated_at}


def card_options(session):
    tables = session.scalars(select(DataTableRecord).order_by(DataTableRecord.name)).all()
    return {"tables": [{"id": t.id, "name": t.name,
                        "columns": [c.model_dump() for c in table_columns(session, t)]}
                       for t in tables]}


def validate_fields(session, body):
    table = session.get(DataTableRecord, body.table_id)
    if table is None:
        raise HTTPException(422, "原数据表已删除，请重新选择数据表。")
    columns = {c.key: c for c in table_columns(session, table)}
    for key in [body.metric_field, body.group_field, body.date_field]:
        if key and key not in columns:
            raise HTTPException(422, "统计字段已删除，请修改统计并重新选择字段。")
    if body.metric_field and columns[body.metric_field].value_type != "number":
        raise HTTPException(422, "数值字段的类型已变化，请重新选择数值字段。")
    if body.date_field and columns[body.date_field].value_type != "date":
        raise HTTPException(422, "范围字段需要是日期类型，请重新选择。")
    if body.time_bucket and columns[body.group_field].value_type != "date":
        raise HTTPException(422, "日期分组字段的类型已变化，请重新选择。")
    return columns


def card_result(session, body, days=7):
    try:
        validate_fields(session, body)
        filters = []
        if body.date_field and body.time_range == "dashboard" and days != "all":
            filters = [Filter(field=body.date_field, op="gte", value=range_start(days).date().isoformat()),
                       Filter(field=body.date_field, op="lt", value=(datetime.now().astimezone().date() + timedelta(days=1)).isoformat())]
        result = analyze(session, AnalysisRequest(table_id=body.table_id, filters=filters,
            dimensions=[body.group_field] if body.group_field else [],
            metrics=[Metric(op=body.metric, field=body.metric_field)], time_bucket=body.time_bucket,
            grain="auto", limit=366 if body.time_bucket else 30))
        display = body.display
        if display == "auto":
            display = "number" if not body.group_field or result["group_count"] <= 1 else "line" if body.time_bucket else "bar"
        if display == "number":
            label = result["data"][0]["label"] if body.group_field and result["group_count"] == 1 else "全部"
            result["data"] = [{"label": label, **result["totals"]}]
        if display in {"donut", "line"} and result["group_count"] == 1:
            display = "number"
            result["data"] = [{"label": result["data"][0]["label"], **result["totals"]}]
        if display == "donut" and result["source"]["row_count"]:
            key = result["metric_keys"][0]
            values = [row[key] for row in result["data"]]
            if result["truncated"] or any(v is None or v < 0 for v in values) or sum(values) <= 0:
                raise HTTPException(422, "当前数据不能表示完整的非负占比，请改用条形图。")
        if display == "line" and any(row["label"] == "未填写或无效日期" for row in result["data"]):
            raise HTTPException(422, "部分记录缺少有效日期，请先核对日期，或改用条形图。")
        if display == "donut" and result["group_count"] > 8:
            display = "bar"
            result["warnings"].append("分类超过 8 个，改用条形图以便比较；完整数据保留在明细中。")
        if result["truncated"] and display != "number":
            if body.time_bucket:
                raise HTTPException(422, "日期分组超过 366 个，请改为按月汇总或跟随仪表盘时间范围。")
            result["warnings"].append(f"仅显示数值最高的前 30 个分类，共 {result['group_count']} 个；未显示的分类没有按零处理。")
        empty = result["source"]["row_count"] == 0
        return {"status": "empty" if empty else "ready", "display": display,
                "message": "当前范围没有记录。" if empty else None, "analysis": result}
    except (HTTPException, ValidationError) as error:
        message = str(error.detail) if isinstance(error, HTTPException) else "统计配置已不兼容，请修改后保存。"
        return {"status": "invalid", "display": body.display, "message": message, "analysis": None}


def saved_result(session, identifier, days):
    card = session.get(DashboardCard, identifier)
    if card is None:
        raise HTTPException(404, "统计卡已删除。")
    try:
        definition = json.loads(card.definition_json)
        contracts = definition.pop("field_contracts", {})
        body = CardInput.model_validate({**definition, "name": card.name})
        columns = validate_fields(session, body)
        if any(key not in columns or [columns[key].value_type, columns[key].section] != contract for key, contract in contracts.items()):
            return {"status": "invalid", "display": body.display, "message": "统计字段的类型或分区已变化，请修改后重新确认。", "analysis": None}
    except (ValidationError, ValueError, TypeError):
        return {"status": "invalid", "display": "auto", "message": "统计配置已不兼容，请修改后保存。", "analysis": None}
    except HTTPException as error:
        return {"status": "invalid", "display": body.display, "message": str(error.detail), "analysis": None}
    return card_result(session, body, days)


def save_card(session, body: CardInput, identifier=None, *, days=7):
    # Acquire the SQLite writer before the count/check to keep the four-card cap
    # and edits atomic even when two browser windows submit simultaneously.
    session.execute(text("BEGIN IMMEDIATE"))
    columns = validate_fields(session, body)
    preview = card_result(session, body, days)
    if preview["status"] == "invalid":
        raise HTTPException(422, preview["message"])
    values = body.model_dump(exclude={"expected_updated_at"})
    values["field_contracts"] = {key: [columns[key].value_type, columns[key].section]
                                for key in [body.metric_field, body.group_field, body.date_field] if key}
    now = utc_now()
    if identifier:
        card = session.get(DashboardCard, identifier)
        if card is None:
            raise HTTPException(404, "统计卡已删除。")
        expected = body.expected_updated_at
        if expected.tzinfo:
            expected = expected.astimezone(timezone.utc).replace(tzinfo=None)
        if card.updated_at.replace(tzinfo=None) != expected:
            raise HTTPException(409, "这张统计已在其他窗口修改，请重新打开后再保存。")
        card.name, card.definition_json, card.updated_at = body.name, json.dumps(values, ensure_ascii=False), now
    else:
        if (session.scalar(select(func.count()).select_from(DashboardCard)) or 0) >= CARD_LIMIT:
            raise HTTPException(409, "最多保留 4 张自定义统计，请先删除不再需要的统计。")
        position = (session.scalar(select(func.max(DashboardCard.position))) or 0) + 1
        card = DashboardCard(id=str(uuid4()), name=body.name, position=position,
            definition_json=json.dumps(values, ensure_ascii=False), created_at=now, updated_at=now)
        session.add(card)
    session.commit()
    session.refresh(card)
    return card_read(card)


def reorder_cards(session, identifiers):
    session.execute(text("BEGIN IMMEDIATE"))
    current = set(session.scalars(select(DashboardCard.id)).all())
    if len(identifiers) != len(set(identifiers)) or set(identifiers) != current:
        raise HTTPException(409, "统计卡已变化，请刷新后重新排序。")
    for position, identifier in enumerate(identifiers):
        session.execute(update(DashboardCard).where(DashboardCard.id == identifier).values(position=position))
    session.commit()
    return {"items": [card_read(c) for c in session.scalars(select(DashboardCard).order_by(DashboardCard.position, DashboardCard.id))], "limit": CARD_LIMIT}
