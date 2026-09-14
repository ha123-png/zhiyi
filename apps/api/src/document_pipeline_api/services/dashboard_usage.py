"""Usage totals preserve missing reports and distinguish inference from product quality."""
from collections import defaultdict
import json

from sqlalchemy import func, select

from document_pipeline_api.models import AssistantRun
from document_pipeline_api.models.dashboard import ModelUsageRecord
from document_pipeline_api.services.model_usage import normalize_usage


def empty_usage():
    return {"calls": 0, "completed": 0, "failed": 0, "interrupted": 0, "running": 0,
            "elapsed_ms": 0, "elapsed_sample_count": 0, "average_elapsed_ms": None,
            "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
            "calls_with_usage": 0, "calls_with_cache_usage": 0, "cached_tokens": None,
            "cache_measured_prompt_tokens": 0, "cache_hit_ratio": None,
            "cache_creation_input_tokens": None}


def add_call(target, call):
    target["calls"] += 1
    status = call.get("status", "interrupted")
    target[status if status in {"completed", "failed", "running"} else "interrupted"] += 1
    elapsed = call.get("elapsed_ms")
    if type(elapsed) is int and elapsed >= 0:
        target["elapsed_ms"] += elapsed
        target["elapsed_sample_count"] += 1
        target["average_elapsed_ms"] = target["elapsed_ms"] / target["elapsed_sample_count"]
    usage = normalize_usage(call)
    if "prompt_tokens" in usage:
        target["calls_with_usage"] += 1
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cache_creation_input_tokens"):
        if key in usage:
            target[key] = (target[key] or 0) + usage[key]
    if "cached_tokens" in usage:
        target["calls_with_cache_usage"] += 1
        target["cache_measured_prompt_tokens"] += usage["prompt_tokens"]
    denominator = target["cache_measured_prompt_tokens"]
    target["cache_hit_ratio"] = target["cached_tokens"] / denominator if denominator else None


def usage_summary(session, start):
    total, groups = empty_usage(), defaultdict(empty_usage)
    oldest = session.scalar(select(func.min(ModelUsageRecord.created_at)))
    usage_query = select(ModelUsageRecord)
    if start is not None:
        usage_query = usage_query.where(ModelUsageRecord.created_at >= start)
    for row in session.scalars(usage_query.execution_options(yield_per=500)):
        try:
            usage = json.loads(row.usage_json)
        except (TypeError, ValueError):
            usage = {}
        call = {**usage, "status": row.status, "elapsed_ms": row.elapsed_ms}
        add_call(total, call)
        add_call(groups[(row.purpose, row.model, row.provider)], call)
    legacy_runs = 0
    query = select(AssistantRun.model, AssistantRun.provider, AssistantRun.status, AssistantRun.usage_json).execution_options(yield_per=500)
    if start is not None:
        query = query.where(AssistantRun.created_at >= start)
    for model, provider, status, encoded in session.execute(query):
        try:
            usage = json.loads(encoded)
        except (TypeError, ValueError):
            usage = {}
        calls = usage.get("calls")
        if not isinstance(calls, list):
            # Older runs did not record individual attempts: their invocation
            # count/tokens cannot safely be reconstructed from a run's status.
            legacy_runs += 1
            continue
        for raw in calls:
            if not isinstance(raw, dict):
                continue
            call = dict(raw)
            if call.get("status") in {"running", "interrupted"} and status == "failed":
                call["status"] = "failed"
            elif call.get("status") == "running" and status not in {"running", "waiting", "cancelling"}:
                call["status"] = "interrupted"
            add_call(total, call)
            add_call(groups[("assistant", model, provider)], call)
    return {**total,
            "by_purpose": [{"purpose": purpose, "model": model, "provider": provider, **values}
                           for (purpose, model, provider), values in sorted(groups.items(), key=lambda pair: -pair[1]["calls"])],
            "coverage_start": oldest, "legacy_unmeasured_runs": legacy_runs,
            "history_note": "提取、匹配、模板生成从 0.4.0 启用后记录；问知意采用已有逐次调用记录。未上报 Token 或缓存不按零计算，历史缺失不补算；不估算费用。"}
