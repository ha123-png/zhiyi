"""Offline model-list and native-ticket corruption boundaries."""
import json
import os
import time

import httpx
import pytest

from document_pipeline_api.model_providers.base import ModelResponseError
from document_pipeline_api.model_providers.openai_compatible import OpenAICompatibleProvider
from document_pipeline_api.services.file_copies import CopyExportError
from document_pipeline_api.services.native_files import consume_import_ticket, issue_import_ticket


@pytest.mark.parametrize("payload", [None, [], [{"id": "model"}], "model", 7, True])
def test_model_list_invalid_json_root_is_a_recoverable_response_error(payload):
    def respond(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, content=json.dumps(payload), headers={"content-type": "application/json"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = OpenAICompatibleProvider("https://model.invalid/v1", "unused", client=client)
        with pytest.raises(ModelResponseError, match="模型列表格式不正确"):
            provider.available_models()


@pytest.mark.parametrize("payload,expected", [
    ({}, []),
    ({"data": []}, []),
    ({"data": [{"id": "vision-v1", "owned_by": "synthetic"}, {"id": "中文模型"}], "object": "list"}, ["vision-v1", "中文模型"]),
])
def test_model_list_valid_envelopes_keep_existing_behavior(payload, expected):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        provider = OpenAICompatibleProvider("https://model.invalid/v1", "unused", client=client)
        assert provider.available_models() == expected


@pytest.mark.parametrize("identifier", [None, 5, {}, [], True, 1.5])
def test_model_list_non_string_identifier_is_a_response_error(identifier):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"id": identifier}]}))) as client:
        provider = OpenAICompatibleProvider("https://model.invalid/v1", "unused", client=client)
        with pytest.raises(ModelResponseError, match="模型列表格式不正确"):
            provider.available_models()


@pytest.mark.parametrize("data", [None, 3, True, [None], [{}], ["model"], {"unexpected": "value"}])
def test_model_list_existing_invalid_entry_translation_is_preserved(data):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": data}))) as client:
        provider = OpenAICompatibleProvider("https://model.invalid/v1", "unused", client=client)
        with pytest.raises(ModelResponseError, match="模型列表格式不正确"):
            provider.available_models()


_NATIVE_CASES = [
    "null_root", "array_root", "string_root", "number_root", "empty_object", "broken_json",
    "string_expiry", "null_expiry", "array_expiry", "nan_expiry", "infinite_expiry",
    "missing_path", "null_path", "array_path", "empty_path", "nul_path", "expired", "wrong_generation",
]


def _corrupt_ticket(value, case):
    if case == "broken_json":
        return "{broken"
    if case.endswith("_root"):
        return json.dumps({"null_root": None, "array_root": [], "string_root": "broken", "number_root": 9}[case])
    if case == "empty_object":
        return "{}"
    replacement = {
        "string_expiry": ("expires", "tomorrow"), "null_expiry": ("expires", None),
        "array_expiry": ("expires", []), "nan_expiry": ("expires", float("nan")),
        "infinite_expiry": ("expires", float("inf")), "null_path": ("path", None),
        "array_path": ("path", []), "empty_path": ("path", ""), "nul_path": ("path", "bad\0path"),
        "expired": ("expires", 0), "wrong_generation": ("generation", "another-dataset"),
    }
    if case == "missing_path":
        value.pop("path")
    else:
        key, item = replacement[case]
        value[key] = item
    return json.dumps(value)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows ticket ownership")
@pytest.mark.parametrize("case", _NATIVE_CASES)
def test_bad_native_ticket_cleans_only_its_owned_claim_and_preserves_originals(tmp_path, case):
    source = tmp_path / "original.txt"
    source.write_bytes(b"synthetic original must remain")
    selected = issue_import_ticket(tmp_path, source)
    ticket_path = tmp_path / "native-imports" / f"{selected['token']}.json"
    valid_value = json.loads(ticket_path.read_text(encoding="utf-8"))
    other_source = tmp_path / "other.txt"
    other_source.write_bytes(b"independent selection")
    other = issue_import_ticket(tmp_path, other_source)
    other_ticket = ticket_path.with_name(f"{other['token']}.json")
    other_before = other_ticket.read_bytes()
    ticket_path.write_text(_corrupt_ticket(valid_value, case), encoding="utf-8")

    with pytest.raises(CopyExportError) as caught:
        with consume_import_ticket(tmp_path, selected["token"]):
            pytest.fail("Malformed or expired ticket reached the source")
    assert caught.value.code == "invalid_ticket"
    assert str(source) not in str(caught.value)
    assert not ticket_path.exists()
    assert not ticket_path.with_suffix(".claimed").exists()
    assert source.read_bytes() == b"synthetic original must remain"
    assert other_ticket.read_bytes() == other_before
    assert other_source.read_bytes() == b"independent selection"
    with consume_import_ticket(tmp_path, other["token"]) as (handle, _):
        assert handle.read() == b"independent selection"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows ticket ownership")
def test_native_ticket_valid_integral_expiry_and_consumer_error_keep_cleanup(tmp_path):
    source = tmp_path / "original.txt"
    source.write_bytes(b"retained original")
    selected = issue_import_ticket(tmp_path, source)
    ticket_path = tmp_path / "native-imports" / f"{selected['token']}.json"
    value = json.loads(ticket_path.read_text(encoding="utf-8"))
    value["expires"] = int(time.time()) + 300
    value["future_metadata"] = {"allowed": True}
    ticket_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RuntimeError, match="consumer interrupted"):
        with consume_import_ticket(tmp_path, selected["token"]) as (handle, origin):
            assert handle.read() == b"retained original"
            assert origin["future_metadata"] == {"allowed": True}
            raise RuntimeError("consumer interrupted")
    assert not ticket_path.exists()
    assert not ticket_path.with_suffix(".claimed").exists()
    assert source.read_bytes() == b"retained original"
