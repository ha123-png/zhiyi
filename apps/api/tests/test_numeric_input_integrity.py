"""Real numeric ingress and bounded JSON-leaf reads, using isolated synthetic data."""

import json
import threading
from dataclasses import replace
from decimal import Decimal, Inexact, InvalidOperation, getcontext, localcontext
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_providers import OpenAICompatibleProvider, OllamaProvider
from document_pipeline_api.model_providers.base import ModelResponseError
from document_pipeline_api.model_providers.conversation import ConversationProvider
from document_pipeline_api.models import DataRowRecord, DataRowRevisionRecord, DataTableRecord, ExtractionRecord, FileCurrentDataRecord, TaskRecord
from document_pipeline_api.schemas.extraction import DocumentExtraction, LineItem
from document_pipeline_api.schemas.templates import TemplateFieldRead
from document_pipeline_api.schemas.json_numbers import NumericPrecisionError, loads_json_numbers
from document_pipeline_api.services.autonomous_extraction import AUTONOMOUS_TEMPLATE_ID, AutonomousExtraction
from document_pipeline_api.services.assistant_operations import Plan
from document_pipeline_api.services.assistant_tools import Changes, structured_arguments
from document_pipeline_api.services.extraction import process_task
from document_pipeline_api.services.cell_values import import_cell
from document_pipeline_api.services.task_result_reads import read_task_result_page, read_task_result_value
from document_pipeline_api.services.template_processing import build_template_extraction_model
from test_task_result_boundaries import engine as engine_fixture, seed


engine = engine_fixture
LOSSY = ["0.10000000000000001", "999999999999999.99", "1e-400"]
BIG = "123456789012345678901234567890"
FIELDS = [
    {"key": "amount", "label": "金额", "section": "header", "value_type": "number"},
    {"key": "code", "label": "编号", "section": "header", "value_type": "text"},
]
WRITE_TOKEN = "write-" + "b" * 40


def extraction_model(kind):
    if kind == "autonomous":
        return AutonomousExtraction
    if kind == "legacy":
        return DocumentExtraction
    return build_template_extraction_model(SimpleNamespace(fields=[TemplateFieldRead(**f) for f in FIELDS]))


def content_for(kind, token):
    if kind == "legacy":
        data = {key: None for key in DocumentExtraction.model_fields}
        data.update(items=[], total_amount="TOKEN", document_number="00123")
        return json.dumps(data).replace('"TOKEN"', token)
    prefix = '"fields":' + json.dumps(FIELDS) + ',' if kind == "autonomous" else ""
    return '{' + prefix + '"header":{"amount":' + token + ',"code":"00123"},"items":[]}'


def provider_for(provider_class, content):
    envelope = ({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
                if provider_class is OpenAICompatibleProvider else {"message": {"content": content}, "done": True})
    transport = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope)))
    return provider_class("http://127.0.0.1:9/v1", "synthetic", client=transport)


@pytest.mark.parametrize("provider_class", [OpenAICompatibleProvider, OllamaProvider])
@pytest.mark.parametrize("kind", ["template", "autonomous", "legacy"])
@pytest.mark.parametrize("token", LOSSY)
def test_provider_rejects_lossy_business_json_before_schema(provider_class, kind, token):
    provider = provider_for(provider_class, content_for(kind, token))
    try:
        with pytest.raises(ModelResponseError, match="精度"):
            provider.complete_text("synthetic", extraction_model(kind))
    finally:
        provider.close()


@pytest.mark.parametrize("provider_class", [OpenAICompatibleProvider, OllamaProvider])
@pytest.mark.parametrize("kind", ["template", "autonomous", "legacy"])
@pytest.mark.parametrize("token", ["0.1", "21.50", "1e2", "5e-324", BIG, "-" + BIG])
def test_provider_keeps_supported_numeric_value_and_text(provider_class, kind, token):
    provider = provider_for(provider_class, content_for(kind, token))
    try:
        result = provider.complete_text("synthetic", extraction_model(kind)).model_dump()
    finally:
        provider.close()
    value = result["total_amount"] if kind == "legacy" else result["header"]["amount"]
    code = result["document_number"] if kind == "legacy" else result["header"]["code"]
    assert Decimal(str(value)) == Decimal(token)
    assert type(value) in (int, float) and code == "00123"
    if token.lstrip("-") == BIG:
        assert type(value) is int
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("kind", ["template", "autonomous"])
@pytest.mark.parametrize("token", LOSSY)
def test_declared_model_numeric_strings_reject_rounding(kind, token):
    with pytest.raises(ValidationError):
        extraction_model(kind).model_validate(json.loads(content_for(kind, json.dumps(token))))


def stream_provider(tmp_path, provider_name, tokens):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'unused.db'}", storage_dir=tmp_path / "uploads")
    provider = ConversationProvider(replace(settings, model_provider=provider_name,
                                           model_base_url="http://127.0.0.1:9"), threading.Event())
    provider.client.close()
    args = ['{"title":"合成","operations":[{"kind":"increment_rows","table_id":"ledger",'
            '"row_ids":[1],"changes":{"amount":' + token + '}}]}' for token in tokens]
    if provider_name == "ollama":
        calls = ['{"function":{"name":"propose_operations","arguments":' + value + '}}' for value in args]
        body = '{"metadata_probability":0.10000000000000001,"message":{"tool_calls":[' + ','.join(calls) + ']},"done":true}\n'
    else:
        frames = []
        for index, value in enumerate(args):
            cut = value.index('"amount":') + len('"amount":') + 2
            for piece, name in [(value[:cut], "propose_operations"), (value[cut:], "")]:
                frame = {"choices": [{"delta": {"tool_calls": [{"index": index, "id": f"call_{index}",
                    "function": {"name": name, "arguments": piece}}]}}]}
                frames.append("data: " + json.dumps(frame) + "\n\n")
        body = ''.join(frames) + "data: [DONE]\n\n"
    provider.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body.encode())))
    return provider


@pytest.mark.parametrize("provider_name", ["openai_compatible", "ollama"])
@pytest.mark.parametrize("token", LOSSY)
def test_later_lossy_tool_prevents_entire_batch_from_escaping(tmp_path, provider_name, token):
    provider = stream_provider(tmp_path, provider_name, ["0.1", token])
    events = []
    try:
        with pytest.raises(ValueError, match="精度"):
            events.extend(provider.stream([{"role": "user", "content": "synthetic"}], []))
    finally:
        provider.close()
    assert not any(event["type"] == "tool" for event in events)


@pytest.mark.parametrize("provider_name", ["openai_compatible", "ollama"])
@pytest.mark.parametrize("token", ["0.1", "21.50", BIG])
def test_tool_numbers_are_ordinary_types_and_envelope_metadata_is_unrestricted(tmp_path, provider_name, token):
    provider = stream_provider(tmp_path, provider_name, [token])
    try:
        events = list(provider.stream([{"role": "user", "content": "synthetic"}], []))
    finally:
        provider.close()
    value = next(e for e in events if e["type"] == "tool")["arguments"]["operations"][0]["changes"]["amount"]
    assert type(value) in (int, float) and Decimal(str(value)) == Decimal(token)
    json.dumps(events, allow_nan=False)


@pytest.fixture
def numeric_client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'numeric.db'}", storage_dir=tmp_path / "uploads",
                        queue_enabled=False, integration_read_token="read-" + "a" * 40,
                        integration_write_token=WRITE_TOKEN)
    with TestClient(create_app(settings)) as client:
        seed(client.app.state.session_factory.kw["bind"], schema=FIELDS,
             header={"amount": 10, "code": "00123"}, items=[])
        with client.app.state.session_factory() as session:
            session.add(DataTableRecord(id="ledger", name="合成", template_key="manual:numeric", template_version="1",
                                       document_kind="custom", columns_json=json.dumps(FIELDS)))
            session.flush()
            row = DataRowRecord(table_id="ledger", item_index=1, row_json='{"amount":10,"code":"00123"}')
            session.add(row)
            session.commit()
            client.numeric_row_id = row.id
        yield client


def row_state(client):
    with client.app.state.session_factory() as session:
        return ([(r.id, r.row_version, r.row_json) for r in session.scalars(select(DataRowRecord).order_by(DataRowRecord.id))],
                session.scalar(select(func.count(DataRowRevisionRecord.id))),
                [(r.task_id, r.current_version, r.header_json) for r in session.scalars(select(FileCurrentDataRecord))])


def raw_write(client, route, token):
    common = '"expected_version":1,"changes":{"amount":' + token + '}'
    method, path, body = {
        "update": ("PATCH", f"/tables/ledger/rows/{client.numeric_row_id}", '{' + common + '}'),
        "create": ("POST", "/tables/ledger/rows", '{"values":{"amount":' + token + ',"code":"00123"}}'),
        "integration": ("PATCH", f"/integration/v1/tables/ledger/rows/{client.numeric_row_id}", '{' + common + '}'),
        "current": ("PATCH", "/tasks/file/current", '{"expected_version":0,"header_changes":{"amount":' + token + '}}'),
        "review": ("PUT", "/tasks/file/review", '{"expected_version":0,"result":{"header":{"amount":' + token + ',"code":"00123"},"items":[]}}'),
    }[route]
    prefix = "/api" if route == "integration" else "/api/v1"
    return client.request(method, prefix + path, content=body,
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + WRITE_TOKEN})


@pytest.mark.parametrize("route", ["update", "create", "integration", "current", "review"])
@pytest.mark.parametrize("token", LOSSY + ["NaN", "Infinity", "1e309"])
def test_raw_http_number_precision_rejects_without_mutating(numeric_client, route, token):
    client = numeric_client
    before = row_state(client)
    response = raw_write(client, route, token)
    assert response.status_code == 422, response.text
    assert "精度" in response.text
    assert row_state(client) == before


@pytest.mark.parametrize("route", ["update", "create", "integration", "current", "review"])
@pytest.mark.parametrize("token", ["0.1", "21.50", BIG, json.dumps(BIG)])
def test_raw_http_supported_numbers_stay_numeric_in_storage(numeric_client, route, token):
    client = numeric_client
    response = raw_write(client, route, token)
    assert response.status_code == (201 if route == "create" else 200), response.text
    with client.app.state.session_factory() as session:
        if route in {"current", "review"}:
            values = json.loads(session.get(FileCurrentDataRecord, "file").header_json)
        else:
            row_id = response.json()["id"]
            values = json.loads(session.get(DataRowRecord, row_id).row_json)
    assert type(values["amount"]) in (int, float)
    assert Decimal(str(values["amount"])) == Decimal(token.strip('"'))
    assert values["code"] == "00123"


def test_csv_new_table_preserves_numeric_looking_text(numeric_client):
    client = numeric_client
    response = client.post("/api/v1/tables/import-new", files={
        "file": ("synthetic.csv", ("金额,编号\n" + "\n".join(t + ",00123" for t in LOSSY)).encode(), "text/csv")})
    assert response.status_code == 201, response.text
    table = client.get(f"/api/v1/tables/{response.json()['id']}").json()
    assert all(c["value_type"] == "text" for c in table["columns"])
    amount = next(c["key"] for c in table["columns"] if c["label"] == "金额")
    code = next(c["key"] for c in table["columns"] if c["label"] == "编号")
    assert [r["values"][amount] for r in table["rows"]] == LOSSY
    assert all(r["values"][code] == "00123" for r in table["rows"])


@pytest.mark.parametrize("token", LOSSY)
def test_csv_later_lossy_number_rejects_whole_existing_column_import(numeric_client, token):
    client = numeric_client
    before = row_state(client)
    response = client.post("/api/v1/tables/ledger/import", files={
        "file": ("synthetic.csv", ("金额,编号\n21.50,00123\n" + token + ",00124\n").encode(), "text/csv")})
    assert response.status_code == 422, response.text
    assert "精度" in response.text
    assert row_state(client) == before


@pytest.mark.parametrize("view", ["current", "initial"])
@pytest.mark.parametrize("section", ["header", "items"])
def test_task_single_numeric_leaf_matches_exact_stored_tokens(engine, view, section):
    values = {"plain": int(BIG), "dot.key": -int(BIG), 'quote"key': 21.5, "slash\\key": 0.1}
    schema = [{"key": k, "label": k, "section": "header" if section == "header" else "item", "value_type": "number"}
              for k in values]
    seed(engine, schema=schema, header=values if section == "header" else {}, items=[values] if section == "items" else [])
    with Session(engine) as session:
        page = read_task_result_page(session, "file", view=view)
        for key, value in values.items():
            path = ["header", key] if section == "header" else ["items", 0, key]
            result = read_task_result_value(session, "file", path, view=view, expected_version=page["review_version"])
            expected = str(value) if isinstance(value, int) else value
            assert result["value"] == expected
            assert result["total_characters"] == len(str(value))
            page_value = page["result"]["header"][key] if section == "header" else page["result"]["items"][0][key]
            assert result["value"] == page_value


@pytest.mark.parametrize("token", LOSSY + ["NaN", "Infinity", "-Infinity", "1e309", "1" * 4301])
def test_json_numeric_boundary_rejects_inexact_nonfinite_and_oversized_tokens(token):
    with pytest.raises(NumericPrecisionError, match="精度"):
        loads_json_numbers('{"amount":' + token + '}')


def test_json_numeric_boundary_keeps_ordinary_types_and_decimal_context():
    before = getcontext().copy()
    parsed = loads_json_numbers('{"values":[0.1,21.50,-0,1e2,5e-324,' + BIG + ',true,null,"00123"]}')
    assert parsed["values"] == [0.1, 21.5, 0, 100.0, 5e-324, int(BIG), True, None, "00123"]
    assert type(parsed["values"][5]) is int
    json.dumps(parsed, allow_nan=False)
    after = getcontext()
    assert (after.prec, after.rounding, after.flags, after.traps) == (before.prec, before.rounding, before.flags, before.traps)


def test_template_numeric_schema_keeps_provider_number_null_protocol():
    model = extraction_model("template")
    field = model.model_json_schema()["$defs"]["TemplateHeader"]["properties"]["amount"]
    assert {choice["type"] for choice in field["anyOf"]} == {"number", "null"}
    result = model.model_validate(json.loads(content_for("template", json.dumps(BIG))))
    assert type(result.header.amount) is int and result.header.amount == int(BIG)


@pytest.mark.parametrize("provider_class", [OpenAICompatibleProvider, OllamaProvider])
@pytest.mark.parametrize("kind", ["template", "autonomous", "legacy"])
@pytest.mark.parametrize("token", LOSSY[:2])
def test_provider_reports_numeric_string_precision_errors(provider_class, kind, token):
    provider = provider_for(provider_class, content_for(kind, json.dumps(token)))
    try:
        with pytest.raises(ModelResponseError, match="精度"):
            provider.complete_text("synthetic", extraction_model(kind))
    finally:
        provider.close()


@pytest.mark.parametrize("kind", ["template", "autonomous"])
@pytest.mark.parametrize("token", LOSSY + [BIG, "0.1", "21.50"])
def test_upload_process_original_current_and_export_contract(numeric_client, kind, token):
    client = numeric_client
    if kind == "template":
        created = client.post("/api/v1/templates", json={"name": "合成精度", "fields": FIELDS})
        assert created.status_code == 201, created.text
        template_id = created.json()["id"]
    else:
        template_id = AUTONOMOUS_TEMPLATE_ID
    uploaded = client.post("/api/v1/tasks", files={"file": ("numeric.txt", b"Synthetic numeric input", "text/plain")},
                           data={"template_id": template_id})
    assert uploaded.status_code == 201, uploaded.text
    task_id = uploaded.json()["id"]
    if kind == "autonomous":
        content = json.dumps({
            "header": [{"label": "金额", "value_type": "number", "value": "TOKEN"},
                       {"label": "编号", "value_type": "text", "value": "00123"}],
            "item_fields": [], "items": [],
        }).replace('"TOKEN"', token)
    else:
        content = content_for(kind, token)
    provider = provider_for(OpenAICompatibleProvider, content)
    needs_field_review = kind == "autonomous" and token in LOSSY
    try:
        with client.app.state.session_factory() as session:
            if token in LOSSY and not needs_field_review:
                with pytest.raises(ModelResponseError, match="精度"):
                    process_task(session, client.app.state.settings, task_id, client=provider)
            else:
                result = process_task(session, client.app.state.settings, task_id, client=provider)
                if needs_field_review:
                    issue, = result.validation_issues
                    assert issue.field == "header.h1" and issue.raw_value == token
    finally:
        provider.close()
    with client.app.state.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert (client.app.state.settings.storage_dir / Path(task.storage_path)).exists()
        extraction = session.get(ExtractionRecord, task_id)
        current = session.get(FileCurrentDataRecord, task_id)
        if token in LOSSY and not needs_field_review:
            assert extraction is None and current is None
            assert task.status == "failed" and "精度" in task.failure_message
            return
        original = json.loads(extraction.result_json)["header"]
        stored = json.loads(current.header_json)
        amount_key, code_key = ("h1", "h2") if kind == "autonomous" else ("amount", "code")
        assert original == stored and stored[code_key] == "00123"
        if needs_field_review:
            assert task.status == "needs_review" and stored[amount_key] is None
        else:
            assert type(stored[amount_key]) in (int, float)
            assert Decimal(str(stored[amount_key])) == Decimal(token)
        exported = client.get(f"/api/v1/tables/{current.table_id}/export.json").json()
        exported_headers = [file["header"] for file in exported["files"]] if kind == "autonomous" else exported
        assert any(row.get(code_key) == "00123" and amount_key in row and row[amount_key] == stored[amount_key]
                   for row in exported_headers)


def test_integration_precision_check_keeps_authorization_required(numeric_client):
    response = numeric_client.patch(f"/api/integration/v1/tables/ledger/rows/{numeric_client.numeric_row_id}",
        content='{"expected_version":1,"changes":{"amount":0.10000000000000001}}',
        headers={"Content-Type": "application/json"})
    assert response.status_code == 401


@pytest.mark.parametrize("model", [Plan, Changes])
@pytest.mark.parametrize("token", LOSSY)
def test_compatible_structured_argument_strings_reject_lossy_numbers(model, token):
    changes = '{"amount":' + token + ',"code":"00123"}'
    arguments = ({"title": "合成", "operations": '[{"kind":"update_rows","table_id":"ledger","row_ids":[1],"changes":' + changes + '}]'}
                 if model is Plan else {"table_id": "ledger", "row_id": 1, "changes": changes})
    with pytest.raises(NumericPrecisionError, match="精度"):
        model.model_validate(structured_arguments(model, arguments))


@pytest.mark.parametrize("token", ["0.1", "21.50", BIG])
def test_compatible_structured_argument_strings_keep_numbers_and_text(token):
    result = Changes.model_validate(structured_arguments(Changes, {
        "table_id": "ledger", "row_id": 1, "changes": '{"amount":' + token + ',"code":"00123"}'}))
    assert Decimal(str(result.changes["amount"])) == Decimal(token)
    assert result.changes["code"] == "00123"


@pytest.mark.parametrize("model,field", [(DocumentExtraction, key) for key in
    ("amount_before_tax", "tax_amount", "total_amount", "total_quantity")] + [(LineItem, key) for key in
    ("quantity", "unit_price", "amount", "tax_amount")])
@pytest.mark.parametrize("token", LOSSY)
def test_legacy_declared_numeric_string_fields_reject_rounding(model, field, token):
    data = {key: None for key in model.model_fields}
    if model is DocumentExtraction:
        data["items"] = []
    data[field] = token
    with pytest.raises(ValidationError, match="精度"):
        model.model_validate(data)


@pytest.mark.parametrize("model,field", [(DocumentExtraction, "total_amount"), (LineItem, "amount")])
def test_legacy_numeric_schema_and_supported_string_values_are_compatible(model, field):
    field_schema = model.model_json_schema()["properties"][field]
    assert {part["type"] for part in field_schema["anyOf"]} == {"integer", "number", "null"}
    for token in ["0.1", "21.50", "1e2", BIG, "-" + BIG]:
        data = {key: None for key in model.model_fields}
        if model is DocumentExtraction:
            data["items"] = []
        data[field] = token
        value = getattr(model.model_validate(data), field)
        assert type(value) in (int, float) and Decimal(str(value)) == Decimal(token)


@pytest.mark.parametrize("digits", ["０１２３４５６７８９", "٠١٢٣٤٥٦٧٨٩"])
@pytest.mark.parametrize("ascii_token", ["123", BIG])
def test_existing_csv_unicode_digit_integers_keep_exact_integer_type(numeric_client, digits, ascii_token):
    token = ascii_token.translate(str.maketrans("0123456789", digits))
    converted = import_cell(token, "number", label="金额", row_index=1)
    assert type(converted) is int and converted == int(ascii_token)
    response = numeric_client.post("/api/v1/tables/ledger/import", files={
        "file": ("unicode.csv", ("金额,编号\n" + token + ",00123\n").encode(), "text/csv")})
    assert response.status_code == 200, response.text
    with numeric_client.app.state.session_factory() as session:
        row = session.scalar(select(DataRowRecord).where(DataRowRecord.table_id == "ledger").order_by(DataRowRecord.id.desc()))
        value = json.loads(row.row_json)["amount"]
    assert type(value) is int and value == int(ascii_token)


@pytest.mark.parametrize("kind", ["template", "legacy"])
@pytest.mark.parametrize("token", ["1_000", "1_.0", "1._0", "1e_2", "1_e2", "1e2_0"])
def test_model_numeric_strings_keep_existing_pydantic_underscore_grammar(kind, token):
    parsed = extraction_model(kind).model_validate(json.loads(content_for(kind, json.dumps(token)))).model_dump()
    value = parsed["total_amount"] if kind == "legacy" else parsed["header"]["amount"]
    assert type(value) in (int, float) and Decimal(str(value)) == Decimal(token.replace("_", ""))


@pytest.mark.parametrize("kind", ["template", "legacy"])
@pytest.mark.parametrize("token", ["1__0", "_1000", "1000_", "１２３"])
def test_model_numeric_string_grammar_does_not_expand(kind, token):
    with pytest.raises(ValidationError):
        extraction_model(kind).model_validate(json.loads(content_for(kind, json.dumps(token))))


@pytest.mark.parametrize("token", ["0e999999999999999999999", "-0e999999999999999999999", "0e-999999999999999999999"])
def test_exact_zero_exponents_do_not_depend_on_decimal_exponent_range(token):
    assert loads_json_numbers('{"amount":' + token + '}')["amount"] == 0


@pytest.mark.parametrize("token", ["1e-999999999999999999999", "1e999999999999999999999", "1e-400", "0.10000000000000001"])
@pytest.mark.parametrize("invalid_trapped", [True, False])
def test_rejected_numeric_tokens_preserve_callers_decimal_context(token, invalid_trapped):
    with localcontext() as context:
        context.clear_flags()
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = invalid_trapped
        before = context.copy()
        with pytest.raises(NumericPrecisionError, match="精度"):
            loads_json_numbers('{"amount":' + token + '}')
        assert (context.prec, context.rounding, context.flags, context.traps) == (before.prec, before.rounding, before.flags, before.traps)
