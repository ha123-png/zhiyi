"""Application request budgets, distinct from local model loading parameters."""
from ipaddress import ip_address
from urllib.parse import urlsplit


def default_context_budget(base_url: str, model: str) -> int:
    host = (urlsplit(base_url).hostname or "").lower().rstrip(".")
    try:
        local = host == "localhost" or ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    if local:
        return 8192
    # Provider-published capacity; only match the actual provider endpoint.
    if host.endswith(".aliyuncs.com") and model.lower().startswith("qwen3.6-flash"):
        return 1_000_000
    # A visible application budget for unknown services, not a claimed capacity.
    return 32768


def profile_context_budget(version) -> int:
    if getattr(version, "context_policy", "fixed") == "auto":
        return default_context_budget(version.base_url, version.model_name)
    return version.context_length
