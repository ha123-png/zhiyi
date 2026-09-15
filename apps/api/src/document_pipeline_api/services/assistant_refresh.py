"""Refresh saved analysis requests without asking a model to reconstruct them."""

import json
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from document_pipeline_api.services.assistant_analysis import AnalysisRequest, analyze
from document_pipeline_api.services.assistant_tools import check_context, execute_tool, scoped_analysis


def canonical_request(request):
    value = request.model_dump()
    if value["row_ids"] is not None:
        value["row_ids"] = sorted(set(value["row_ids"]))
    value["filters"] = sorted(
        {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value["filters"]}
    )
    return value


def refreshed_result(session, call, context):
    saved = json.loads(call.result_json)
    snapshot = saved.get("analysis", saved)
    if call.status != "completed" or not snapshot.get("analysis_id") or not snapshot.get("source"):
        raise HTTPException(422, "这条结果没有可复用的统计口径，请重新提问。")
    try:
        query = AnalysisRequest.model_validate(snapshot["source"]["request"])
    except (KeyError, ValidationError):
        raise HTTPException(422, "历史统计口径不完整，请重新提问。") from None
    check_context(session, context)
    scoped = scoped_analysis(session, context, query.model_copy(deep=True))
    if canonical_request(scoped) != canonical_request(query):
        raise HTTPException(409, "当前资料范围与原统计口径不同，请恢复原范围后刷新；没有缩小范围或改写条件。")
    result = analyze(session, query)
    if query.records and snapshot.get("columns"):
        fields = {column["key"] for column in snapshot["columns"]}
        if not fields.issubset({column["key"] for column in result["columns"]}):
            raise HTTPException(409, "原来读取的字段已变化，请重新选择字段。")
        result["columns"] = [column for column in result["columns"] if column["key"] in fields]
        for row in result["rows"]:
            row["values"] = {key: value for key, value in row["values"].items() if key in fields}
    result["analysis_id"] = str(uuid4())
    if saved.get("chart"):
        chart = {**saved["chart"], "analysis_id": result["analysis_id"]}
        try:
            result = execute_tool(session, context, "render_chart", chart, {result["analysis_id"]: result}, None)
        except HTTPException as error:
            if error.status_code != 422:
                raise
            result["warnings"].append(f"当前数据不能沿用原图表，已展示数据表：{error.detail}")
            result = {"analysis": result, "chart": {**chart, "type": "table"},
                      "actual_chart_type": "table", "notice": str(error.detail)}
    return {**result, "refreshed_from": call.id}
