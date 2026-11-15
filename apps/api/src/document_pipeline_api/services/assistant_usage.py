"""Account for every inference, including failed/repair attempts, without storing prompts."""

import time


def tracked_stream(usage, provider, messages, specs, *, on_checkpoint=None, tool_choice=None):
    calls = usage.setdefault("calls", [])
    call = {"index": len(calls) + 1, "status": "running"}
    calls.append(call)
    started = time.monotonic()
    observed = {}

    def update_call_and_totals():
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if (
                isinstance(observed.get(key), int)
                and not isinstance(observed.get(key), bool)
                and observed[key] >= 0
            ):
                call[key] = observed[key]
        if observed.get("reasoning_requested") in ("off", "on", "provider"):
            call["reasoning_requested"] = observed["reasoning_requested"]
        if isinstance(observed.get("reasoning_observed"), bool):
            call["reasoning_observed"] = observed["reasoning_observed"]
        characters = observed.get("reasoning_characters")
        if isinstance(characters, int) and not isinstance(characters, bool) and characters >= 0:
            call["reasoning_characters"] = characters
        details = observed.get("prompt_tokens_details") or {}
        if not isinstance(details, dict):
            details = {}
        cached = details.get("cached_tokens")
        created = details.get("cache_creation_input_tokens")
        if created is None:
            created = observed.get("cache_creation_input_tokens")
        if isinstance(created, int) and not isinstance(created, bool) and created >= 0:
            call["cache_creation_input_tokens"] = created
        if cached is None:
            cached = observed.get("cached_tokens")
        if cached is None:
            cached = observed.get("prompt_cache_hit_tokens")
        if (
            isinstance(cached, int)
            and not isinstance(cached, bool)
            and 0 <= cached <= call.get("prompt_tokens", -1)
        ):
            call["cached_tokens"] = cached

        usage["model_calls"] = len(calls)
        usage["calls_with_usage"] = sum("prompt_tokens" in item for item in calls)
        usage["calls_with_cache_usage"] = sum("cached_tokens" in item for item in calls)
        usage["usage_complete"] = usage["calls_with_usage"] == len(calls)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = [item[key] for item in calls if key in item]
            if values:
                usage[key] = sum(values)
            else:
                usage.pop(key, None)
        cache_calls = [item for item in calls if "cached_tokens" in item]
        denominator = sum(item["prompt_tokens"] for item in cache_calls)
        usage["cached_tokens"] = (
            sum(item["cached_tokens"] for item in cache_calls) if cache_calls else None
        )
        usage["cache_hit_ratio"] = usage["cached_tokens"] / denominator if denominator else None
        usage["cache_measured_prompt_tokens"] = denominator
        created_values = [
            item["cache_creation_input_tokens"]
            for item in calls
            if "cache_creation_input_tokens" in item
        ]
        usage["cache_creation_input_tokens"] = sum(created_values) if created_values else None
        reasoning_calls = [item for item in calls if "reasoning_observed" in item]
        if reasoning_calls:
            usage["reasoning_observed"] = any(
                item["reasoning_observed"] for item in reasoning_calls
            )
            usage["reasoning_characters"] = sum(
                item.get("reasoning_characters", 0) for item in reasoning_calls
            )
        else:
            usage.pop("reasoning_observed", None)
            usage.pop("reasoning_characters", None)
        requested = {item["reasoning_requested"] for item in calls if "reasoning_requested" in item}
        if requested:
            usage["reasoning_requested"] = next(iter(requested)) if len(requested) == 1 else "mixed"
        else:
            usage.pop("reasoning_requested", None)

    def checkpoint():
        update_call_and_totals()
        if on_checkpoint is not None:
            on_checkpoint()

    try:
        checkpoint()
        options = {"tool_choice": tool_choice} if tool_choice is not None else {}
        for event in provider.stream(messages, specs, **options):
            if event["type"] == "usage":
                # Streaming usage is a snapshot, not an incremental token delta.
                observed.update(event["usage"])
                checkpoint()
            yield event
        call["status"] = "completed"
    except BaseException:
        call["status"] = "interrupted"
        raise
    finally:
        call["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        checkpoint()
