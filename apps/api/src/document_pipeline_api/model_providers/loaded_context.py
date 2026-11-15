"""Read capacities reported for running local model instances."""

from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx


def is_loopback_url(base_url: str) -> bool:
    host = (urlsplit(base_url).hostname or "").lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _reported_lengths(payload: dict, provider: str) -> dict[str, int]:
    lengths: dict[str, set[int]] = {}

    def add(name, length):
        if isinstance(name, str) and name and type(length) is int and length > 0:
            lengths.setdefault(name, set()).add(length)

    models = payload.get("models")
    if not isinstance(models, list):
        return {}
    for model in models:
        if not isinstance(model, dict):
            continue
        if provider == "ollama":
            add(model.get("name"), model.get("context_length"))
            add(model.get("model"), model.get("context_length"))
        else:
            instances = model.get("loaded_instances")
            if not isinstance(instances, list):
                continue
            for instance in instances:
                if isinstance(instance, dict) and isinstance(instance.get("config"), dict):
                    add(instance.get("id"), instance["config"].get("context_length"))
    # If one name resolves to different lengths, there is no reliable single limit.
    return {name: next(iter(values)) for name, values in lengths.items() if len(values) == 1}


def read_loaded_context_lengths(provider: str, base_url: str, api_key: str = "") -> dict[str, int]:
    if provider not in {"lm_studio", "ollama"} or not is_loopback_url(base_url):
        return {}
    base = base_url.rstrip("/").removesuffix("/v1")
    path = "/api/v1/models" if provider == "lm_studio" else "/api/ps"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(base + path, headers=headers)
            response.raise_for_status()
            payload = response.json()
        return _reported_lengths(payload, provider) if isinstance(payload, dict) else {}
    except (httpx.HTTPError, ValueError):
        return {}


def loaded_context_for_model(provider: str, lengths: dict[str, int], model: str) -> int | None:
    if provider == "ollama" and ":" not in model.rsplit("/", 1)[-1]:
        model = f"{model}:latest" if model not in lengths else model
    return lengths.get(model)
