"""Inference metadata only. Flush after the business transaction releases its lock."""

from contextlib import contextmanager
import json
import logging
import time
from uuid import uuid4

from sqlalchemy import event, insert
from sqlalchemy.orm import Session

from document_pipeline_api.models.dashboard import ModelUsageRecord
from document_pipeline_api.models.task import utc_now

logger = logging.getLogger(__name__)
PENDING = "zhiyi_pending_model_usage"


def normalize_usage(raw):
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if type(raw.get(key)) is int and raw[key] >= 0:
            result[key] = raw[key]
    details = raw.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    cached = details.get("cached_tokens", raw.get("cached_tokens", raw.get("prompt_cache_hit_tokens")))
    if type(cached) is int and 0 <= cached <= result.get("prompt_tokens", -1):
        result["cached_tokens"] = cached
    created = details.get("cache_creation_input_tokens", raw.get("cache_creation_input_tokens"))
    if type(created) is int and created >= 0:
        result["cache_creation_input_tokens"] = created
    return result


@contextmanager
def model_call(session, provider, purpose, provider_name):
    # Start a transaction even for read-only template generation, so Session.close
    # reliably triggers the same flush used by success and rollback paths.
    session.connection()
    started, created_at, status = time.monotonic(), utc_now(), "completed"
    try:
        yield
    except BaseException as error:
        status = "interrupted" if isinstance(error, (GeneratorExit, KeyboardInterrupt, InterruptedError)) else "failed"
        raise
    finally:
        usage = normalize_usage(getattr(provider, "last_usage", None))
        session.info.setdefault(PENDING, []).append({
            "id": str(uuid4()), "purpose": purpose,
            "model": str(provider.model_name)[:256], "provider": provider_name[:64],
            "status": status, "elapsed_ms": round((time.monotonic() - started) * 1000),
            "usage_json": json.dumps(usage), "created_at": created_at,
        })


@event.listens_for(Session, "after_transaction_end")
def flush_usage(session, transaction):
    if transaction.parent is not None:
        return
    records = session.info.pop(PENDING, [])
    if not records:
        return
    try:
        with session.get_bind().begin() as connection:
            connection.execute(insert(ModelUsageRecord), records)
    except Exception:
        # Usage must never turn a successfully preserved document into a failure.
        # Metadata stays bounded and contains neither prompts nor credentials.
        logger.warning("模型用量记录暂时无法保存，业务结果不受影响。", exc_info=False)
