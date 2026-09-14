"""Account for every inference, including failed/repair attempts, without storing prompts."""

import time


def tracked_stream(usage, provider, messages, specs):
    calls = usage.setdefault("calls", [])
    call = {"index": len(calls) + 1, "status": "running"}
    calls.append(call)
    started = time.monotonic()
    observed = {}
    try:
        for event in provider.stream(messages, specs):
            if event["type"] == "usage":
                # Streaming usage is a snapshot, not an incremental token delta.
                observed.update(event["usage"])
            yield event
        call["status"] = "completed"
    except BaseException:
        call["status"] = "interrupted"
        raise
    finally:
        call["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance(observed.get(key), int) and observed[key] >= 0:
                call[key] = observed[key]
        if observed.get("reasoning_requested") in ("off", "on", "provider"):
            call["reasoning_requested"] = observed["reasoning_requested"]
        if isinstance(observed.get("reasoning_observed"), bool):
            call["reasoning_observed"] = observed["reasoning_observed"]
        characters = observed.get("reasoning_characters")
        if isinstance(characters, int) and not isinstance(characters, bool) and characters >= 0:
            call["reasoning_characters"] = characters
        details = observed.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens")
        created = details.get("cache_creation_input_tokens")
        if created is None:
            created = observed.get("cache_creation_input_tokens")
        if isinstance(created, int) and created >= 0:
            call["cache_creation_input_tokens"] = created
        if cached is None:
            cached = observed.get("cached_tokens")
        if cached is None:
            cached = observed.get("prompt_cache_hit_tokens")
        if isinstance(cached, int) and 0 <= cached <= call.get("prompt_tokens", -1):
            call["cached_tokens"] = cached
        usage["model_calls"] = len(calls)
        usage["calls_with_usage"] = sum("prompt_tokens" in c for c in calls)
        usage["calls_with_cache_usage"] = sum("cached_tokens" in c for c in calls)
        usage["usage_complete"] = usage["calls_with_usage"] == len(calls)
        reasoning_calls = [c for c in calls if "reasoning_observed" in c]
        if reasoning_calls:
            usage["reasoning_observed"] = any(c["reasoning_observed"] for c in reasoning_calls)
            usage["reasoning_characters"] = sum(c.get("reasoning_characters", 0) for c in reasoning_calls)
        requested = {c["reasoning_requested"] for c in calls if "reasoning_requested" in c}
        if requested:
            usage["reasoning_requested"] = next(iter(requested)) if len(requested) == 1 else "mixed"
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = [c[key] for c in calls if key in c]
            if values:
                usage[key] = sum(values)
        cache_calls = [c for c in calls if "cached_tokens" in c]
        denominator = sum(c["prompt_tokens"] for c in cache_calls)
        usage["cached_tokens"] = (
            sum(c["cached_tokens"] for c in cache_calls) if cache_calls else None
        )
        usage["cache_hit_ratio"] = usage["cached_tokens"] / denominator if denominator else None
        usage["cache_measured_prompt_tokens"] = denominator
        created_values = [
            c["cache_creation_input_tokens"] for c in calls if "cache_creation_input_tokens" in c
        ]
        usage["cache_creation_input_tokens"] = sum(created_values) if created_values else None
