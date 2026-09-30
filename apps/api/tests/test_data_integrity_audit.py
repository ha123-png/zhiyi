from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import ExtractionRecord, TaskRecord
from document_pipeline_api.services.data_tables import create_table_from_import, materialize_task_result


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data-integrity.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    return TestClient(create_app(settings))


def _csv_import(client: TestClient, name: str, text: str):
    return client.post(
        "/api/v1/tables/import-new",
        files={"file": (name, text.encode("utf-8-sig"), "text/csv")},
    )


def _xlsx_bytes(headers: list[str], values: list[object]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    sheet.append(values)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _xlsx_formula_with_cache() -> bytes:
    original = _xlsx_bytes(["value"], ["=1+2"])
    output = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(output, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(b"<v />", b"<v>3</v>")
            target.writestr(info, content)
    return output.getvalue()


def test_import_new_keeps_later_columns_when_first_data_row_is_short(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        imported = _csv_import(client, "ragged.csv", "a,b\nx\ny,z\n")
        assert imported.status_code == 201, imported.text
        table = client.get(f"/api/v1/tables/{imported.json()['id']}").json()
        columns = {column["label"]: column["key"] for column in table["columns"]}
        assert {"a", "b"} <= columns.keys()
        assert table["rows"][1]["values"][columns["b"]] == "z"


def test_import_export_round_trip_keeps_double_underscore_business_column(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = _csv_import(client, "reserved.csv", "__id\nA001\n")
        assert created.status_code == 201, created.text
        table_id = created.json()["id"]
        exported = client.get(f"/api/v1/tables/{table_id}/export.csv")
        assert exported.status_code == 200
        imported = client.post(
            "/api/v1/tables/import-new",
            files={"file": ("round-trip.csv", exported.content, "text/csv")},
        )
        assert imported.status_code == 201, imported.text
        detail = client.get(f"/api/v1/tables/{imported.json()['id']}").json()
        column = next(column for column in detail["columns"] if column["label"] == "__id")
        assert detail["rows"][0]["values"][column["key"]] == "A001"


def test_app_created_double_underscore_column_survives_csv_reimport(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = _csv_import(client, "base.csv", "name\nrow\n")
        assert created.status_code == 201, created.text
        table_id = created.json()["id"]
        detail = client.get(f"/api/v1/tables/{table_id}").json()
        column = client.post(f"/api/v1/tables/{table_id}/columns", json={
            "label": "__id", "section": "item", "value_type": "text",
        })
        assert column.status_code == 201, column.text
        row = detail["rows"][0]
        edited = client.patch(f"/api/v1/tables/{table_id}/rows/{row['id']}", json={
            "expected_version": row["version"], "changes": {column.json()["key"]: "A001"},
        })
        assert edited.status_code == 200, edited.text
        exported = client.get(f"/api/v1/tables/{table_id}/export.csv")
        reimported = client.post("/api/v1/tables/import-new", files={
            "file": ("app-created.csv", exported.content, "text/csv"),
        })
        assert reimported.status_code == 201, reimported.text
        result = client.get(f"/api/v1/tables/{reimported.json()['id']}").json()
        app_column = next(item for item in result["columns"] if item["label"] == "__id")
        assert result["rows"][0]["values"][app_column["key"]] == "A001"


@pytest.mark.parametrize("size, expected_status", [(131071, 201), (131072, 201), (131073, 422)])
def test_csv_cell_size_boundary_is_a_controlled_import_result(
    tmp_path: Path, size: int, expected_status: int
) -> None:
    with _client(tmp_path) as client:
        response = _csv_import(client, "large-cell.csv", "value\n" + ("x" * size) + "\n")
        assert response.status_code == expected_status, response.text[:500]
        if expected_status == 422:
            assert "单元格" in response.text or "限制" in response.text
            assert client.get("/api/v1/tables").json() == []


@pytest.mark.parametrize("column_count, expected_status", [(511, 201), (512, 201), (513, 422)])
def test_xlsx_business_column_limit_excludes_internal_group_metadata(
    tmp_path: Path, column_count: int, expected_status: int
) -> None:
    with _client(tmp_path) as client:
        headers = [f"column_{index}" for index in range(column_count)]
        response = client.post(
            "/api/v1/tables/import-new",
            files={"file": ("wide.xlsx", _xlsx_bytes(headers, ["v"] * column_count),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == expected_status, response.text[:500]


@pytest.mark.parametrize(
    "rows",
    [
        [{"value": "x" * 32768}],
        [{"value": "bad\x01text"}],
        [{"value": 9007199254740993}],
    ],
    ids=["long-text", "control-character", "unsafe-excel-integer"],
)
def test_xlsx_export_rejects_values_that_excel_cannot_preserve(
    tmp_path: Path, rows: list[dict[str, object]]
) -> None:
    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            table = create_table_from_import(session, "unsafe-export", rows)
            session.commit()
            table_id = table.id
        response = client.get(f"/api/v1/tables/{table_id}/export.xlsx")
        assert response.status_code == 422, response.text[:500]
        assert "JSON" in response.text or "Excel" in response.text


@pytest.mark.parametrize("number", [1.2345678901234567, 1.234567890123456e20])
def test_xlsx_export_rejects_float_values_over_excel_precision(
    tmp_path: Path, number: float
) -> None:
    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            table = create_table_from_import(session, "float-export", [{"value": number}])
            session.commit()
            table_id = table.id
        response = client.get(f"/api/v1/tables/{table_id}/export.xlsx")
        assert response.status_code == 422, response.text[:500]
        assert "15 位有效数字" in response.text


@pytest.mark.parametrize(
    "value",
    [1.23456789012345, 1e20, 1.23456789012345e-20, 0.000000123456789012345],
)
def test_xlsx_export_preserves_supported_float_precision(tmp_path: Path, value: float) -> None:
    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            table = create_table_from_import(session, "supported-float", [{"value": value}])
            session.commit()
            table_id = table.id
        response = client.get(f"/api/v1/tables/{table_id}/export.xlsx")
        assert response.status_code == 200, response.text
        cell = load_workbook(BytesIO(response.content)).active["A2"]
        assert cell.data_type == "n"
        assert cell.value == value


def test_xlsx_export_keeps_error_literal_as_text_without_changing_its_value(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            table = create_table_from_import(session, "literal-export", [{"value": "=#REF!"}])
            session.commit()
            table_id = table.id
        response = client.get(f"/api/v1/tables/{table_id}/export.xlsx")
        assert response.status_code == 200, response.text
        cell = load_workbook(BytesIO(response.content)).active["A2"]
        assert cell.value == "=#REF!"
        assert cell.data_type == "s"


def test_per_file_xlsx_export_rejects_unsupported_cell_text(tmp_path: Path) -> None:
    from document_pipeline_api.models import DataTableRecord, FileCurrentDataRecord
    from openpyxl.worksheet._writer import ALL_TEMP_FILES

    with _client(tmp_path) as client:
        _seed_review_task(client, "unsupported-file-export")
        with client.app.state.session_factory() as session:
            current = session.get(FileCurrentDataRecord, "unsupported-file-export")
            assert current is not None and current.table_id
            table = session.get(DataTableRecord, current.table_id)
            assert table is not None
            table.collection_kind = "files"
            header = json.loads(current.header_json)
            header["seller_name"] = "x" * 32768
            current.header_json = json.dumps(header, ensure_ascii=False)
            table_id = table.id
            session.commit()
        before_temp_files = set(ALL_TEMP_FILES)
        for _ in range(3):
            response = client.get(f"/api/v1/tables/{table_id}/export.xlsx")
            assert response.status_code == 422
            assert "JSON" in response.text
            assert set(ALL_TEMP_FILES) <= before_temp_files


def test_formula_without_cached_value_is_explained_before_import(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["value"])
    sheet.append(["=1+2"])
    output = BytesIO()
    workbook.save(output)
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/tables/import-new",
            files={"file": ("formula.xlsx", output.getvalue(),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 422
        assert "公式" in response.text and ("缓存" in response.text or "计算" in response.text)


def test_formula_with_cached_value_imports_the_cached_result(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post("/api/v1/tables/import-new", files={
            "file": ("cached-formula.xlsx", _xlsx_formula_with_cache(),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        })
        assert response.status_code == 201, response.text
        detail = client.get(f"/api/v1/tables/{response.json()['id']}").json()
        assert detail["rows"][0]["values"]["import_1"] == 3


def test_import_mapping_rejects_two_headers_that_target_one_column(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = _csv_import(client, "target.csv", "销售方名称\nBEFORE\n")
        assert created.status_code == 201, created.text
        table_id = created.json()["id"]
        before = client.get(f"/api/v1/tables/{table_id}").json()
        response = client.post(
            f"/api/v1/tables/{table_id}/import",
            files={"file": ("collision.csv", "销售方,销售方名称\nOLD_ALIAS,NEW_LABEL\n".encode("utf-8-sig"), "text/csv")},
        )
        assert response.status_code == 422, response.text
        after = client.get(f"/api/v1/tables/{table_id}").json()
        assert after["row_count"] == before["row_count"]
        assert after["columns"] == before["columns"]


def test_import_mapping_rejects_target_key_and_label_collision(tmp_path: Path) -> None:
    from document_pipeline_api.models.data_table import DataRowRecord, DataTableRecord

    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            table = DataTableRecord(
                id="key-label-target", name="target", template_key="custom-key-label",
                template_version="1", document_kind="custom",
                columns_json=json.dumps([{
                    "key": "seller_name", "label": "销售方名称", "section": "item",
                    "value_type": "text", "user_defined": True,
                }], ensure_ascii=False),
            )
            session.add(table)
            session.flush()
            session.add(DataRowRecord(
                table_id=table.id, task_id=None, item_index=1,
                row_json=json.dumps({"seller_name": "BEFORE"}), row_version=1,
            ))
            session.commit()
        before = client.get("/api/v1/tables/key-label-target").json()
        response = client.post("/api/v1/tables/key-label-target/import", files={
            "file": ("key-label.csv", "seller_name,销售方名称\nOLD_KEY,NEW_LABEL\n".encode("utf-8-sig"), "text/csv"),
        })
        assert response.status_code == 422
        after = client.get("/api/v1/tables/key-label-target").json()
        assert after["row_count"] == before["row_count"]
        assert after["rows"][0]["values"]["seller_name"] == "BEFORE"


def test_search_ignores_internal_row_group_but_keeps_business_values(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = _csv_import(client, "search.csv", "name,value\nAlpha,one\nBeta,two\n")
        assert created.status_code == 201, created.text
        table_id = created.json()["id"]
        hidden = client.get(f"/api/v1/tables/{table_id}?search=import").json()
        visible = client.get(f"/api/v1/tables/{table_id}?search=Alpha").json()
        exported = client.get(f"/api/v1/tables/{table_id}/export.csv?search=import")
        assert hidden["row_count"] == 0
        assert visible["row_count"] == 1
        assert exported.headers["x-zhiyi-row-count"] == "0"
        assert len(exported.content.decode("utf-8-sig").splitlines()) == 1


def _seed_review_task(client: TestClient, task_id: str, *, seller: str = "seller") -> None:
    result = {
        "document_type": "发票",
        "seller_name": seller,
        "buyer_name": "buyer",
        "seller_tax_id": None,
        "buyer_tax_id": None,
        "seller_contact": None,
        "buyer_contact": None,
        "buyer_address": None,
        "document_number": "NO-1",
        "document_date": "2026-01-01",
        "amount_before_tax": 9007199254740993,
        "tax_amount": 6,
        "total_amount": 9007199254740999,
        "total_quantity": None,
        "remarks": None,
        "items": [{
            "name": "item", "specification": None, "unit": "unit", "quantity": 1,
            "unit_price": 9007199254740993, "amount": 9007199254740993,
            "tax_rate": "6%", "tax_amount": 6, "remarks": None,
        }],
    }
    with client.app.state.session_factory() as session:
        session.add(TaskRecord(
            id=task_id, filename=f"{task_id}.png", content_type="image/png", size_bytes=1,
            sha256=f"digest-{task_id}", storage_path=f"{task_id}.png", template_mode="invoice",
            status="needs_review",
        ))
        session.flush()
        session.add(ExtractionRecord(
            task_id=task_id, document_kind="invoice", model_name="test-model",
            prompt_version="test-prompt", elapsed_seconds=1,
            result_json=json.dumps(result, ensure_ascii=False), validation_json="[]",
        ))
        session.commit()
        materialize_task_result(session, task_id)
        session.commit()


def test_file_result_review_round_trip_keeps_integers_beyond_binary64(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_review_task(client, "integer-review")
        result = client.get("/api/v1/tasks/integer-review/result").json()
        assert result["original_result"]["amount_before_tax"] == "9007199254740993"
        assert result["result"]["header"]["amount_before_tax"] == "9007199254740993"
        assert result["result"]["header"]["tax_amount"] == 6
        saved = client.put("/api/v1/tasks/integer-review/review", json={
            "expected_version": result["review_version"], "result": result["result"],
        })
        assert saved.status_code == 200, saved.text
        table_id = client.get("/api/v1/tables").json()[0]["id"]
        table = client.get(f"/api/v1/tables/{table_id}").json()
        assert table["rows"][0]["values"]["amount_before_tax"] == "9007199254740993"
        assert table["rows"][0]["values"]["unit_price"] == "9007199254740993"
        exported = client.get(f"/api/v1/tables/{table_id}/export.json").json()
        assert exported[0]["amount_before_tax"] == 9007199254740993
        assert exported[0]["unit_price"] == 9007199254740993
        assert saved.json()["original_result"]["tax_amount"] == 6

        updated = json.loads(json.dumps(saved.json()["result"]))
        updated["header"]["amount_before_tax"] = "9007199254740995"
        edited = client.put("/api/v1/tasks/integer-review/review", json={
            "expected_version": saved.json()["review_version"], "result": updated,
        })
        assert edited.status_code == 200, edited.text
        revisions = client.get("/api/v1/tasks/integer-review/revisions").json()["revisions"]
        history = client.get(
            f"/api/v1/tasks/integer-review/revisions/{revisions[0]['id']}"
        ).json()
        assert history["header"]["amount_before_tax"]["before"] == "9007199254740993"
        assert history["header"]["amount_before_tax"]["after"] == "9007199254740995"
        current_details = client.get("/api/v1/tasks/integer-review/current-details").json()
        assert current_details["header"]["amount_before_tax"] == "9007199254740995"
        cards = client.get(f"/api/v1/tables/{table_id}/source-cards").json()
        assert cards["groups"][0]["preview_header"]
        card_detail = client.get(f"/api/v1/tables/{table_id}/source-details", params={
            "source_key": cards["groups"][0]["id"],
        }).json()
        assert card_detail["header"]["amount_before_tax"] == "9007199254740995"


def test_review_ignore_and_revoke_are_versioned_and_audited(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_review_task(client, "ignore-review")
        initial = client.get("/api/v1/tasks/ignore-review/result").json()
        wrong = json.loads(json.dumps(initial["result"]))
        wrong["header"]["total_amount"] = 7
        first_edit = client.put("/api/v1/tasks/ignore-review/review", json={
            "expected_version": initial["review_version"], "result": wrong,
        })
        assert first_edit.status_code == 200, first_edit.text
        issue_indices = [index for index, issue in enumerate(first_edit.json()["validation_issues"])
                         if issue["severity"] == "error"]
        assert issue_indices
        ignored = client.put("/api/v1/tasks/ignore-review/review", json={
            "expected_version": first_edit.json()["review_version"], "result": wrong,
            "ignored_issue_indices": issue_indices,
        })
        assert ignored.status_code == 200, ignored.text
        assert ignored.json()["review_version"] == first_edit.json()["review_version"] + 1
        stale = client.put("/api/v1/tasks/ignore-review/review", json={
            "expected_version": first_edit.json()["review_version"], "result": wrong,
            "ignored_issue_indices": [],
        })
        assert stale.status_code == 409
        revoked = client.put("/api/v1/tasks/ignore-review/review", json={
            "expected_version": ignored.json()["review_version"], "result": wrong,
            "ignored_issue_indices": [],
        })
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["review_version"] == ignored.json()["review_version"] + 1
        revisions = client.get("/api/v1/tasks/ignore-review/revisions").json()["revisions"]
        ignored_detail = client.get(f"/api/v1/tasks/ignore-review/revisions/{revisions[1]['id']}").json()
        revoked_detail = client.get(f"/api/v1/tasks/ignore-review/revisions/{revisions[0]['id']}").json()
        assert ignored_detail["validation"]["after"] == ignored.json()["validation_issues"]
        assert revoked_detail["validation"]["before"] == ignored.json()["validation_issues"]
        assert all(issue["ignored"] for issue in ignored_detail["validation"]["after"]
                   if issue["severity"] == "error")
        assert not any(issue["ignored"] for issue in revoked_detail["validation"]["after"])
        ignored_error = next(issue for issue in ignored_detail["validation"]["after"]
                             if issue["severity"] == "error")
        revoked_error = next(issue for issue in revoked_detail["validation"]["after"]
                             if issue["severity"] == "error")
        assert (ignored_error["code"], ignored_error["field"]) == (
            revoked_error["code"], revoked_error["field"],
        )


@pytest.mark.parametrize("indices", [[-1], [0.5], ["0"], [999], [0, 0]])
def test_review_rejects_invalid_ignore_issue_indices(tmp_path: Path, indices: list[object]) -> None:
    with _client(tmp_path) as client:
        _seed_review_task(client, "bad-ignore-index")
        review = client.get("/api/v1/tasks/bad-ignore-index/result").json()
        payload = {
            "expected_version": review["review_version"], "result": review["result"],
            "ignored_issue_indices": indices,
        }
        response = client.put("/api/v1/tasks/bad-ignore-index/review", json=payload)
        assert response.status_code == 422


def test_review_ignore_indices_resolve_to_prior_rule_identity_after_same_request_edit(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        _seed_review_task(client, "stable-ignore-identity")
        initial = client.get("/api/v1/tasks/stable-ignore-identity/result").json()
        first_result = json.loads(json.dumps(initial["result"]))
        first_result["header"]["seller_name"] = ""
        first_result["header"]["buyer_name"] = ""
        first_result["header"]["total_amount"] = 7
        first = client.put("/api/v1/tasks/stable-ignore-identity/review", json={
            "expected_version": initial["review_version"], "result": first_result,
        })
        assert first.status_code == 200, first.text
        first_issues = first.json()["validation_issues"]
        tax_issue = next(
            (index, issue) for index, issue in enumerate(first_issues)
            if issue["code"] == "invoice_tax_total_mismatch"
        )
        assert tax_issue[0] == 2  # Two required-field warnings precede this error.

        edited = json.loads(json.dumps(first.json()["result"]))
        edited["header"]["seller_name"] = "seller"  # Removes issue zero.
        saved = client.put("/api/v1/tasks/stable-ignore-identity/review", json={
            "expected_version": first.json()["review_version"], "result": edited,
            "ignored_issue_indices": [tax_issue[0]],
        })
        assert saved.status_code == 200, saved.text
        current_issues = saved.json()["validation_issues"]
        current_tax_issue = next(
            issue for issue in current_issues if issue["code"] == "invoice_tax_total_mismatch"
        )
        assert current_tax_issue["field"] == "total_amount"
        assert current_tax_issue["ignored"] is True


def test_review_ignore_index_range_uses_prior_version_issue_list(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_review_task(client, "new-issue-cannot-be-ignored-by-old-index")
        initial = client.get("/api/v1/tasks/new-issue-cannot-be-ignored-by-old-index/result").json()
        edited = json.loads(json.dumps(initial["result"]))
        edited["header"]["total_amount"] = 7  # Creates an issue absent from the loaded version.
        response = client.put("/api/v1/tasks/new-issue-cannot-be-ignored-by-old-index/review", json={
            "expected_version": initial["review_version"], "result": edited,
            "ignored_issue_indices": [len(initial["validation_issues"])],
        })
        assert response.status_code == 422


def test_unsafe_integer_string_over_python_conversion_limit_is_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            table = create_table_from_import(session, "large-int", [{"value": 9007199254740993}])
            session.commit()
            table_id = table.id
        row = client.get(f"/api/v1/tables/{table_id}").json()["rows"][0]
        response = client.patch(f"/api/v1/tables/{table_id}/rows/{row['id']}", json={
            "expected_version": row["version"], "changes": {"import_1": "9" * 5000},
        })
        assert response.status_code == 422


def test_table_row_api_preserves_unsafe_integer_string_round_trip(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            table = create_table_from_import(session, "large-int", [{"value": 9007199254740993}])
            session.commit()
            table_id = table.id
        before = client.get(f"/api/v1/tables/{table_id}").json()["rows"][0]
        assert before["values"]["import_1"] == "9007199254740993"
        updated = client.patch(f"/api/v1/tables/{table_id}/rows/{before['id']}", json={
            "expected_version": before["version"], "changes": {"import_1": "9007199254740995"},
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()["values"]["import_1"] == "9007199254740995"
        exported = client.get(f"/api/v1/tables/{table_id}/export.json").json()
        assert exported[0]["import_1"] == 9007199254740995


def test_assistant_xlsx_snapshot_rejects_unsafe_integer(tmp_path: Path) -> None:
    from document_pipeline_api.models.assistant import (
        AssistantMessage, AssistantRun, AssistantThread, AssistantToolCall,
    )

    with _client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            session.add(AssistantThread(id="thread", title="test"))
            session.flush()
            session.add(AssistantMessage(
                id="message", thread_id="thread", role="assistant", position=1,
            ))
            session.flush()
            session.add(AssistantRun(
                id="run", thread_id="thread", message_id="message", profile_id="profile",
                profile_version=1, model="model", provider="provider",
            ))
            session.flush()
            snapshot = {"kind": "export", "analysis": {
                "columns": [{"key": "amount", "label": "金额"}],
                "rows": [{"values": {"amount": 9007199254740993}}],
                "source": {"table_name": "synthetic", "generated_at": "2026-09-30",
                           "request": {}},
            }}
            session.add(AssistantToolCall(
                id="tool", run_id="run", name="prepare_export", arguments_json="{}",
                result_json=json.dumps(snapshot), status="completed",
            ))
            session.commit()
        response = client.get("/api/v1/assistant/tools/tool/export")
        assert response.status_code == 422


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_excel_value_guard_rejects_non_finite_float(value: float) -> None:
    from fastapi import HTTPException
    from document_pipeline_api.services.excel_values import safe_excel_value

    with pytest.raises(HTTPException) as error:
        safe_excel_value(value)
    assert error.value.status_code == 422


def test_excel_value_guard_rejects_float_outside_excel_numeric_range() -> None:
    from fastapi import HTTPException
    from document_pipeline_api.services.excel_values import safe_excel_value

    with pytest.raises(HTTPException) as error:
        safe_excel_value(1e308)
    assert error.value.status_code == 422
