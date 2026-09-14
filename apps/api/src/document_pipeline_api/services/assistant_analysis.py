"""Bounded, deterministic analysis over the same facts as the warehouse."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import math
import re
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from document_pipeline_api.models import DataRowRecord, DataTableRecord
from document_pipeline_api.services.data_tables import table_columns


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Filter(Strict):
    field: str
    op: Literal["eq", "ne", "contains", "gt", "gte", "lt", "lte", "empty"] = "eq"
    value: str | float | bool | None = None


class Metric(Strict):
    op: Literal["count", "sum", "avg", "min", "max", "distinct", "missing", "duplicates"]
    field: str | None = None


class AnalysisRequest(Strict):
    table_id: str
    filters: list[Filter] = Field(default_factory=list, max_length=12)
    dimensions: list[str] = Field(default_factory=list, max_length=2)
    metrics: list[Metric] = Field(
        default_factory=lambda: [Metric(op="count")], min_length=1, max_length=6
    )
    time_bucket: Literal["day", "month", "year"] | None = None
    grain: Literal["auto", "row", "document"] = "auto"
    sort: str | None = None
    descending: bool = True
    limit: int = Field(default=30, ge=1, le=500)
    records: bool = False
    search: str = Field(default="", max_length=500)
    row_ids: list[int] | None = Field(default=None, max_length=2000)
    task_id: str | None = None


def number(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        result = Decimal(str(value))
        return result if result.is_finite() else None
    text = str(value).strip()
    # Refuse identifiers and ambiguous locale formats; never silently coerce to zero.
    if not re.fullmatch(r"[-+]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?", text):
        return None
    try:
        result = Decimal(text.replace(",", ""))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def matches(value, rule):
    if rule.op == "empty":
        return value is None or value == ""
    if rule.op == "contains":
        return str(rule.value or "").casefold() in str(value or "").casefold()
    if rule.op in {"eq", "ne"}:
        equal = value == rule.value or (value is not None and str(value) == str(rule.value))
        return equal if rule.op == "eq" else not equal
    left, right = number(value), number(rule.value)
    if left is None or right is None:
        # ISO dates are ordered only after successful parsing on both sides.
        try:
            left, right = (
                datetime.fromisoformat(str(value)),
                datetime.fromisoformat(str(rule.value)),
            )
            # Business date filters use the same local calendar as the dashboard.
            # An explicitly zoned timestamp must not silently fail comparison
            # against an unzoned YYYY-MM-DD range boundary.
            if left.tzinfo is not None:
                left = left.astimezone().replace(tzinfo=None)
            if right.tzinfo is not None:
                right = right.astimezone().replace(tzinfo=None)
        except (ValueError, TypeError):
            return False
    try:
        return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[
            rule.op
        ]
    except TypeError:
        return False


def document_key(row, values):
    return (
        ("task", row.task_id)
        if row.task_id
        else ("group", values["__row_group"])
        if values.get("__row_group")
        else ("row", row.id)
    )


def validate_measure_units(columns, rows, metrics):
    """Do not add amounts/quantities whose explicitly stored units disagree.

    The schema has no hidden default currency. Values remain unitless unless a
    business field records it; never infer exchange rates or convert amounts.
    """
    warnings = []
    for metric in metrics:
        if metric.op not in {"sum", "avg"} or not metric.field:
            continue
        column = columns[metric.field]
        label = (column.key + " " + column.label).casefold()
        currency = any(word in label for word in ("金额", "价款", "价税", "费用", "单价", "amount", "price", "cost"))
        quantity = any(word in label for word in ("数量", "重量", "长度", "面积", "体积", "quantity", "weight", "qty"))
        candidates = [c for c in columns.values() if (
            currency and (c.label.strip() in {"币种", "货币", "货币单位"} or c.key.rsplit(".", 1)[-1].casefold() in {"currency", "currency_code"})
        ) or (
            quantity and (c.label.strip() in {"单位", "计量单位"} or c.key.rsplit(".", 1)[-1].casefold() in {"unit", "measurement_unit"})
        )]
        if rows and currency and not candidates and not re.search(r"[（(](?:元|万元|人民币|美元|欧元|CNY|USD|EUR)[）)]", column.label, re.I):
            warnings.append(f"「{column.label}」未记录币种，仅显示数值汇总；不能作为统一币种总额。")
        if rows and quantity and not candidates and not re.search(r"[（(][^）)]+[）)]", column.label):
            warnings.append(f"「{column.label}」未记录计量单位，仅显示数值汇总；请核对是否属于同一单位。")
        for unit in candidates:
            relevant = [v for _, v in rows if number(v.get(metric.field)) is not None]
            units = {str(v.get(unit.key) or "").strip().casefold() for v in relevant}
            aliases = {"人民币": "cny", "rmb": "cny", "元": "cny", "美元": "usd", "欧元": "eur"}
            units = {aliases.get(value, value) for value in units}
            if len(units) > 1 or (units == {""} and relevant):
                raise HTTPException(422, f"「{column.label}」的{unit.label}混合或缺失，请先核对；没有合并为一个总额。")
    return warnings


def analyze(session, request: AnalysisRequest):
    table = session.get(DataTableRecord, request.table_id)
    if table is None:
        raise HTTPException(404, "没有找到这张表。")
    columns = {c.key: c for c in table_columns(session, table)}
    fields = (
        request.dimensions
        + [f.field for f in request.filters]
        + [m.field for m in request.metrics if m.field]
    )
    if any(key not in columns for key in fields):
        raise HTTPException(
            422,
            "分析字段不存在。可用业务字段："
            + "、".join(list(columns)[:40])
            + "。记录编号使用 row_ids，不能作为 filters 的业务字段。",
        )
    if any(m.op != "count" and not m.field for m in request.metrics):
        raise HTTPException(422, "除计数外，统计指标必须指定字段。")
    metric_keys = [f"{m.op}:{m.field or '*'}" for m in request.metrics]
    operations = {
        "count": "计数",
        "sum": "合计",
        "avg": "平均",
        "min": "最小",
        "max": "最大",
        "distinct": "去重数",
        "missing": "缺失数",
        "duplicates": "重复数",
    }
    metric_labels = {
        key: f"{columns[m.field].label} · {operations[m.op]}" if m.field else "记录数"
        for key, m in zip(metric_keys, request.metrics)
    }
    if request.sort and request.sort not in (
        {*columns, "row_id"} if request.records else {*metric_keys, *request.dimensions}
    ):
        raise HTTPException(422, "排序字段或指标不存在。")
    if request.time_bucket and not request.dimensions:
        raise HTTPException(422, "时间分桶需要指定日期维度。")
    query = (
        select(DataRowRecord).where(DataRowRecord.table_id == table.id).order_by(DataRowRecord.id)
    )
    if request.row_ids is not None:
        query = query.where(DataRowRecord.id.in_(request.row_ids))
    if request.task_id:
        query = query.where(DataRowRecord.task_id == request.task_id)
    # Apply the exact business predicates while streaming, before the result cap.
    # A narrow date range in a large table must not fail on unrelated old rows.
    rows = []
    for record in session.scalars(query.execution_options(yield_per=1000)):
        values = json.loads(record.row_json)
        if request.search and request.search.casefold() not in json.dumps(values, ensure_ascii=False).casefold():
            continue
        if not all(matches(values.get(f.field), f) for f in request.filters):
            continue
        rows.append((record, values))
        if len(rows) > 100000:
            raise HTTPException(422, "筛选后仍超过十万条记录，请缩小日期或资料范围；没有返回局部统计。")
    warnings = validate_measure_units(columns, rows, request.metrics)
    raw_count = len(rows)
    pending_count = sum(r.review_pending is True for r, _ in rows)
    partial_count = sum(
        bool(r.input_scope_json) and json.loads(r.input_scope_json).get("coverage") == "partial"
        for r, _ in rows
    )
    pending_documents = len({document_key(r, v) for r, v in rows if r.review_pending is True})
    if pending_count:
        warnings.append(
            f"分析时有 {pending_documents} 份来源尚待确认，涉及本次 {pending_count} 条明细；独立副本保留自身状态。"
        )
    if partial_count:
        warnings.append(f"范围内有 {partial_count} 条记录来自部分读取的文件，结果不代表完整原件。")
    # Validate shared dimensions before grouping; otherwise a conflicting document
    # could be placed into two buckets and counted twice without detection.
    shared = {k for k in fields if columns[k].section == "header"}
    if request.grain in {"auto", "document"} and shared:
        seen = {}
        for r, v in rows:
            key = document_key(r, v)
            values = {k: v.get(k) for k in shared}
            if key in seen and seen[key] != values:
                raise HTTPException(
                    422, "同一文件的公共字段值不一致，请先核对；没有选择任意一行代替。"
                )
            seen[key] = values
    if request.grain == "document":
        if any(columns[k].section != "header" for k in fields):
            raise HTTPException(422, "按文件统计不能使用明细字段；请改用明细统计。")
        unique = {}
        for r, v in rows:
            unique.setdefault(document_key(r, v), (r, v))
        rows = list(unique.values())
    operators = {"eq": "等于", "ne": "不等于", "contains": "包含", "gt": "大于", "gte": "不小于", "lt": "小于", "lte": "不大于", "empty": "未填写"}
    conditions = [f"{columns[f.field].label}{operators[f.op]}" + ("" if f.op == "empty" else str(f.value)[:160]) for f in request.filters]
    if request.search:
        conditions.append("搜索：" + request.search[:160])
    source = {
        "table_id": table.id,
        "table_name": table.name,
        "row_count": raw_count,
        "review_pending_count": pending_count,
        "review_pending_documents": pending_documents,
        "partial_input_count": partial_count,
        "document_count": len({document_key(r, v) for r, v in rows}),
        "grain": request.grain,
        "request": request.model_dump(),
        "scope_description": "筛选：" + "；".join(conditions) if conditions else None,
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    if request.records:
        if request.sort == "row_id" and "row_id" not in columns:
            rows.sort(key=lambda rv: rv[0].id, reverse=request.descending)
        elif request.sort:
            key = request.sort
            rows.sort(
                key=lambda rv: (
                    rv[1].get(key) is not None,
                    number(rv[1].get(key)) is not None,
                    number(rv[1].get(key)) or Decimal(0),
                    str(rv[1].get(key) or ""),
                ),
                reverse=request.descending,
            )
        return {
            "source": source,
            "columns": [c.model_dump() for c in columns.values()],
            "rows": [
                {"row_id": r.id, "version": r.row_version, "task_id": r.task_id, "values": v}
                for r, v in rows[: request.limit]
            ],
            "truncated": len(rows) > request.limit,
            "warnings": warnings,
        }
    if request.time_bucket:
        dimension = request.dimensions[0]
        valid_dates = 0
        invalid_dates = 0
        for _, values in rows:
            try:
                datetime.fromisoformat(str(values.get(dimension)))
                valid_dates += 1
            except (ValueError, TypeError):
                invalid_dates += 1
        if rows and not valid_dates:
            raise HTTPException(
                422,
                f"「{columns[dimension].label}」没有可识别日期，不能按时间分组。分类汇总请去掉 time_bucket；时间趋势请改用日期字段。没有生成误导性图表。",
            )
        if invalid_dates:
            warnings.append(
                f"「{columns[dimension].label}」有 {invalid_dates} 条记录缺少有效日期，单独列入无效日期组；不能当作时间趋势。"
            )
    buckets = defaultdict(list)
    for r, v in rows:
        dims = [
            json.dumps(v.get(d), ensure_ascii=False, sort_keys=True)
            if isinstance(v.get(d), (list, dict))
            else v.get(d)
            for d in request.dimensions
        ]
        if request.time_bucket and dims:
            try:
                date = datetime.fromisoformat(str(dims[0]))
                if date.tzinfo is not None:
                    date = date.astimezone()
                dims[0] = date.strftime(
                    {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[request.time_bucket]
                )
            except (ValueError, TypeError):
                dims[0] = "未填写或无效日期"
        buckets[tuple(dims)].append((r, v))
    if not request.dimensions and not buckets:
        buckets[()] = []
    invalid = defaultdict(int)
    inferred = set()

    def calculate(entries, metric):
        entries = list(entries)
        if metric.field and request.grain == "auto" and columns[metric.field].section == "header":
            if any(columns[d].section != "header" for d in request.dimensions):
                raise HTTPException(
                    422, "文件级金额不能直接按明细字段分摊；请选择明细金额或文件级维度。"
                )
            unique = {}
            for r, v in entries:
                key = document_key(r, v)
                if key in unique and unique[key][1].get(metric.field) != v.get(metric.field):
                    raise HTTPException(
                        422, "同一文件的公共字段值不一致，请先核对；没有选择任意一行代替。"
                    )
                unique[key] = (r, v)
            entries = list(unique.values())
        values = (
            [v.get(metric.field) for _, v in entries]
            if metric.field
            else [r.id for r, _ in entries]
        )
        if metric.op == "count":
            return sum(v is not None and v != "" for v in values) if metric.field else len(values)
        if metric.op == "missing":
            return sum(v is None or v == "" for v in values)
        nonempty = [v for v in values if v is not None and v != ""]
        distinct = {json.dumps(v, sort_keys=True, ensure_ascii=False) for v in nonempty}
        if metric.op == "distinct":
            return len(distinct)
        if metric.op == "duplicates":
            return len(nonempty) - len(distinct)
        nums = [number(v) for v in nonempty]
        invalid[metric.field] += sum(n is None for n in nums)
        nums = [n for n in nums if n is not None]
        if columns[metric.field].value_type != "number" and nums:
            inferred.add(metric.field)
        if not nums:
            return None
        value = {
            "sum": lambda: sum(nums),
            "avg": lambda: sum(nums) / len(nums),
            "min": lambda: min(nums),
            "max": lambda: max(nums),
        }[metric.op]()
        result = float(value)
        if not math.isfinite(result):
            raise HTTPException(422, "计算结果超出图表可表示的数值范围，请缩小范围或核对异常数值。")
        return result

    totals = {key: calculate(rows, metric) for metric, key in zip(request.metrics, metric_keys)}
    invalid.clear()
    result = []
    for dims, entries in buckets.items():
        record = dict(zip(request.dimensions, dims))
        record["label"] = " · ".join(str(v) if v is not None else "未填写" for v in dims) or "全部"
        for metric, key in zip(request.metrics, metric_keys):
            record[key] = calculate(entries, metric)
        result.append(record)
    sort = request.sort or (request.dimensions[0] if request.time_bucket else metric_keys[0])
    result.sort(
        key=lambda r: (
            r.get(sort) is not None,
            isinstance(r.get(sort), (float, int)),
            r.get(sort) if isinstance(r.get(sort), (float, int)) else str(r.get(sort) or ""),
        ),
        reverse=request.descending if request.sort or not request.time_bucket else False,
    )
    warnings += [
        f"{columns[k].label}：有 {v} 个非空值无法作为数字，已排除；未按零计算。"
        for k, v in invalid.items()
        if v
    ]
    warnings += [
        f"{columns[k].label}：本次按可解释的数值分析，未修改原字段类型。" for k in inferred
    ]
    if request.grain == "auto" and any(
        m.field and columns[m.field].section == "header" for m in request.metrics
    ):
        warnings.append("文件级指标按来源文件/导入分组计一次；无来源分组的手工行各计一次。")
    return {
        "source": source,
        "data": result[: request.limit],
        "metric_keys": metric_keys,
        "metric_labels": metric_labels,
        "dimension_labels": {d: columns[d].label for d in request.dimensions},
        "totals": totals,
        "group_count": len(result),
        "truncated": len(result) > request.limit,
        "warnings": warnings,
    }
