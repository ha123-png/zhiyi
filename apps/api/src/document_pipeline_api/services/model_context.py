"""Application request budgets, distinct from local model loading parameters."""
from urllib.parse import urlsplit

from document_pipeline_api.model_providers.loaded_context import is_loopback_url

DEFAULT_LOCAL_PROFILE_ID = "default-local-qwen35-4b"


def default_context_budget(base_url: str, model: str) -> int:
    host = (urlsplit(base_url).hostname or "").lower().rstrip(".")
    if is_loopback_url(base_url):
        return 8192
    # Provider-published capacity; only match the actual provider endpoint.
    if host.endswith(".aliyuncs.com") and model.lower().startswith("qwen3.6-flash"):
        return 1_000_000
    # A visible application budget for unknown services, not a claimed capacity.
    return 32768


def profile_context_policy(version) -> str:
    # Version 1 of the seeded profile was created by the app, never edited in place.
    if (getattr(version, "profile_id", None) == DEFAULT_LOCAL_PROFILE_ID and version.version == 1
            and version.context_length == 8192):
        return "auto"
    return getattr(version, "context_policy", "fixed")


def profile_context_budget(version) -> int:
    if profile_context_policy(version) == "auto":
        return default_context_budget(version.base_url, version.model_name)
    return version.context_length
