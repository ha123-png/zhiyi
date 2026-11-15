"""Structured conversational completion; aggregate values come from durable tools."""

import json
import math
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import select

from document_pipeline_api.models import AssistantToolCall
from document_pipeline_api.services.assistant_analysis import Strict


class FinishAnswer(Strict):
    kind: Literal["general", "analysis", "limitation"]
    text: str | None = Field(default=None, min_length=1, max_length=60000)
    analysis_id: str | None = Field(default=None, min_length=1, max_length=100)
    metric_keys: list[str] = Field(default_factory=list, max_length=6)
    include_permissions: bool = False

    @model_validator(mode="after")
    def require_matching_fields(self):
        if self.kind == "analysis":
            if not self.analysis_id or self.text is not None:
                raise ValueError("统计回答只能引用本轮真实统计编号，不能传入模型编写的金额或正文。")
        elif not self.text or not self.text.strip() or self.analysis_id or self.metric_keys:
            raise ValueError("一般说明或限制说明需要正文，不能附带统计编号或指标。")
        if len(set(self.metric_keys)) != len(self.metric_keys):
            raise ValueError("统计指标不能重复。")
        return self


ANSWER_DESCRIPTION = (
    "完成本轮回答，必须作为唯一工具调用。普通问候、一般说明用 kind=general 与自然语言 text；"
    "没有获得所需依据时用 kind=limitation 与 text，不宣称任务已完成。"
    "当前资料的金额、汇总、统计结果必须先实际调用 analyze_data_table，再用 kind=analysis、"
    "本轮返回的 analysis_id 和需要呈现的 metric_keys（省略表示全部指标）完成回答；"
    "该分支不接受 text 或金额，知意会呈现真实口径、行数、totals 和当前权限。"
    "read_data_rows、历史快照或失败调用不能作为本轮统计依据。"
    "include_permissions=true 可附加知意根据当前模式生成的权限说明。"
    "general 是一般说明，不是可核验的业务统计，不能用它代替用户要求的统计。"
)


def permission_summary(context):
    if context.mode == "read":
        return "当前为只读分析：可读取、分析本次授权资料，不能修改、删除或归档。"
    if context.mode == "assist":
        return "当前为协助模式：业务更改仍需你确认，并受本次资料范围和已允许能力限制。"
    return "当前为委托模式：操作仍受本次资料范围和已允许能力限制，实际变更以操作记录为准。"


def render_answer(session, run_id, context, answer):
    """Return display text and proof metadata without trusting model totals.

    General prose is not semantically classified here and is never certified as
    an aggregate. Only the analysis branch gets a durable, current-run proof.
    The caller checks current run authorization before invoking this function.
    """
    evidence = None
    if answer.kind != "analysis":
        display = answer.text.strip()
    else:
        proof = None
        result = None
        for call in session.scalars(select(AssistantToolCall).where(
            AssistantToolCall.run_id == run_id,
            AssistantToolCall.name == "analyze_data_table",
            AssistantToolCall.status == "completed",
        )):
            candidate = json.loads(call.result_json)
            if candidate.get("analysis_id") == answer.analysis_id:
                proof, result = call, candidate
                break
        if proof is None:
            raise ValueError("没有找到本轮已完成的真实统计；请先调用数据统计，不能使用明细或历史快照代替。")
        source = result.get("source", {})
        request = source.get("request", {})
        totals = result.get("totals")
        expected_keys = [f"{metric.get('op')}:{metric.get('field') or '*'}"
                         for metric in request.get("metrics", [])]
        if (request.get("records") is not False or not isinstance(totals, dict)
                or not expected_keys or set(expected_keys) != set(result.get("metric_keys", []))
                or set(expected_keys) != set(totals)):
            raise ValueError("本轮统计记录缺少可核对的聚合口径或结果，请重新统计。")
        keys = answer.metric_keys or expected_keys
        if any(key not in expected_keys for key in keys):
            raise ValueError("本轮统计没有包含所请求的指标，请先计算该指标。")
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                     or isinstance(value, float) and not math.isfinite(value))
               for value in (totals[key] for key in keys)):
            raise ValueError("本轮统计结果不是有效数值，未展示无法核对的合计。")
        grain = {"auto": "自动区分文件级与明细级指标", "row": "按明细", "document": "按文件"}.get(source.get("grain"))
        display = f"已统计「{source['table_name']}」本次授权范围内的 {source['row_count']} 条明细。"
        if grain:
            display += "\n\n计算口径：" + grain + "。"
        if source.get("scope_description"):
            display += "\n" + source["scope_description"]
        labels = result.get("metric_labels", {})
        display += "\n\n" + "\n".join(
            f"- {labels.get(key, key)}：" + ("无可计算数值" if totals[key] is None else json.dumps(totals[key], allow_nan=False))
            for key in keys
        )
        if result.get("warnings"):
            display += "\n\n" + "\n".join(result["warnings"])
        evidence = {"tool_id": proof.id, "analysis_id": answer.analysis_id, "metric_keys": keys}
    if answer.kind == "analysis" or answer.include_permissions:
        display += "\n\n" + permission_summary(context)
    return display, evidence
