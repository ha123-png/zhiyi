from __future__ import annotations

import pytest

from document_pipeline_api.model_secrets import (
    ModelSecretStore,
    SecretNotFoundError,
    SecretStoreError,
    TARGET_PREFIX,
)


class MemoryCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def write(self, target: str, secret: bytes) -> None:
        self.values[target] = secret

    def read(self, target: str) -> bytes:
        try:
            return self.values[target]
        except KeyError as error:
            raise SecretNotFoundError("missing") from error

    def delete(self, target: str) -> None:
        if target not in self.values:
            raise SecretNotFoundError("missing")
        del self.values[target]


def test_secret_store_uses_opaque_immutable_refs_and_round_trips() -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)

    first_ref = store.put("first-secret")
    second_ref = store.put("second-secret")

    assert first_ref.startswith("wincred:")
    assert second_ref.startswith("wincred:")
    assert first_ref != second_ref
    assert "first-secret" not in first_ref
    assert all(target.startswith(TARGET_PREFIX) for target in backend.values)
    assert store.get(first_ref) == "first-secret"
    assert store.get(second_ref) == "second-secret"


def test_secret_store_rejects_empty_invalid_missing_and_corrupt_values() -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)

    with pytest.raises(SecretStoreError, match="空"):
        store.put("")
    with pytest.raises(SecretStoreError, match="格式"):
        store.get("environment")
    missing_ref = "wincred:12345678-1234-4123-8123-123456789abc"
    with pytest.raises(SecretNotFoundError):
        store.get(missing_ref)

    corrupt_ref = store.put("temporary")
    target = next(target for target, value in backend.values.items() if value == b"temporary")
    backend.values[target] = b"\xff"
    with pytest.raises(SecretStoreError, match="损坏"):
        store.get(corrupt_ref)


def test_secret_delete_is_exact_and_idempotent() -> None:
    backend = MemoryCredentialBackend()
    store = ModelSecretStore(backend)
    keep_ref = store.put("keep")
    delete_ref = store.put("delete")

    store.delete(delete_ref)
    store.delete(delete_ref)

    assert store.get(keep_ref) == "keep"
    with pytest.raises(SecretNotFoundError):
        store.get(delete_ref)
