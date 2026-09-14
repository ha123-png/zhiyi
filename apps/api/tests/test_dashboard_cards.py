from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import select

from document_pipeline_api.business_backup import create_business_backup, restore_business_backup
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import DataRowRecord, DataTableRecord
from document_pipeline_api.models.task import utc_now


def make_client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'document-pipeline.db'}", storage_dir=tmp_path / "uploads")
    return TestClient(create_app(settings)), settings


def seed(client, values=None):
    with client.app.state.session_factory() as session:
        session.add(DataTableRecord(id="orders", name="订单", template_key="orders", template_version="1", document_kind="manual",
            columns_json=json.dumps([
                {"key": "amount", "label": "金额", "value_type": "number", "section": "header"},
                {"key": "date", "label": "日期", "value_type": "date", "section": "header"},
                {"key": "category", "label": "类别", "value_type": "text", "section": "header"},
                {"key": "currency", "label": "币种", "value_type": "text", "section": "header"},
            ])))
        session.flush()
        for value in values or []:
            session.add(DataRowRecord(table_id="orders", item_index=0, row_json=json.dumps(value)))
        session.commit()


def definition(**changes):
    return {"name": "订单金额", "table_id": "orders", "metric": "sum", "metric_field": "amount", **changes}


def test_card_reuses_document_grain_and_live_stable_fields(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        seed(client, [{"amount": 100, "category": "甲", "currency": "CNY", "__row_group": "a"},
                      {"amount": 100, "category": "甲", "currency": "CNY", "__row_group": "a"},
                      {"amount": 50, "category": "乙", "currency": "人民币", "__row_group": "b"}])
        options = client.get("/api/v1/stats/card-options").json()
        assert options["tables"][0]["columns"][0]["key"] == "amount"
        card = client.post("/api/v1/stats/cards", json=definition()).json()
        result_url = f"/api/v1/stats/cards/{card['id']}/result"
        result = client.get(result_url).json()
        assert result["analysis"]["totals"] == {"sum:amount": 150}
        assert result["analysis"]["source"]["row_count"] == 3
        with client.app.state.session_factory() as session:
            table = session.get(DataTableRecord, "orders")
            table.name = "改名订单"
            columns = json.loads(table.columns_json)
            columns[0]["label"] = "应收金额"
            table.columns_json = json.dumps(columns)
            session.add(DataRowRecord(table_id="orders", item_index=0, row_json='{"amount":25,"currency":"CNY"}'))
            session.commit()
        result = client.get(result_url).json()
        assert result["analysis"]["totals"] == {"sum:amount": 175}
        assert result["analysis"]["source"]["table_name"] == "改名订单"
        assert "应收金额" in result["analysis"]["metric_labels"]["sum:amount"]
        assert "analysis" not in client.get("/api/v1/stats/cards").json()["items"][0]


def test_empty_zero_missing_numbers_and_bad_donut_are_distinct(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        seed(client)
        url = "/api/v1/stats/cards/preview"
        assert client.post(url, json=definition()).json()["status"] == "empty"
        with client.app.state.session_factory() as session:
            session.add_all([DataRowRecord(table_id="orders", item_index=0, row_json=json.dumps(v)) for v in [
                {"amount": 10, "category": "甲", "currency": "CNY"},
                {"amount": -10, "category": "乙", "currency": "CNY"}]])
            session.commit()
        zero = client.post(url, json=definition()).json()
        assert zero["status"] == "ready"
        assert zero["analysis"]["totals"]["sum:amount"] == 0
        assert client.post(url, json=definition(group_field="category", display="donut")).json()["status"] == "invalid"
        assert client.post(url, json=definition(metric="avg", group_field="category", display="donut")).status_code == 422


def test_mixed_currency_and_inconsistent_document_are_blocked(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        seed(client, [{"amount": 1, "currency": "CNY"}, {"amount": 1, "currency": "USD"}])
        result = client.post("/api/v1/stats/cards/preview", json=definition()).json()
        assert result["status"] == "invalid" and "币种" in result["message"]
        assert client.post("/api/v1/stats/cards", json=definition()).status_code == 422
        with client.app.state.session_factory() as session:
            rows = session.scalars(select(DataRowRecord)).all()
            for index, row in enumerate(rows):
                row.row_json = json.dumps({"amount": index + 1, "currency": "CNY", "__row_group": "same"})
            session.commit()
        result = client.post("/api/v1/stats/cards/preview", json=definition()).json()
        assert result["status"] == "invalid" and "公共字段" in result["message"]


def test_single_category_keeps_its_label_in_numeric_presentation(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        seed(client, [{"amount": 3, "currency": "CNY", "category": "唯一分类"}])
        for display in ["auto", "number", "donut"]:
            result = client.post("/api/v1/stats/cards/preview", json=definition(group_field="category", display=display)).json()
            assert result["display"] == "number"
            assert result["analysis"]["data"] == [{"label": "唯一分类", "sum:amount": 3}]


def test_bounded_categories_are_explicit_and_never_become_partial_donut(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        seed(client, [{"amount": i + 1, "currency": "CNY", "category": str(i)} for i in range(31)])
        body = definition(group_field="category", display="bar")
        bar = client.post("/api/v1/stats/cards/preview", json=body).json()
        assert bar["status"] == "ready"
        assert len(bar["analysis"]["data"]) == 30
        assert any("31" in warning and "30" in warning for warning in bar["analysis"]["warnings"])
        assert client.post("/api/v1/stats/cards/preview", json={**body, "display": "donut"}).json()["status"] == "invalid"


def test_fields_deleted_or_changed_invalidate_only_own_card_and_can_be_repaired(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        seed(client, [{"amount": 1, "currency": "CNY"}])
        first = client.post("/api/v1/stats/cards", json=definition()).json()
        other = client.post("/api/v1/stats/cards", json=definition(name="条数", metric="count", metric_field=None)).json()
        with client.app.state.session_factory() as session:
            table = session.get(DataTableRecord, "orders")
            columns = json.loads(table.columns_json)
            columns[0]["value_type"] = "text"
            table.columns_json = json.dumps(columns)
            session.commit()
        assert client.get(f"/api/v1/stats/cards/{first['id']}/result").json()["status"] == "invalid"
        assert client.get(f"/api/v1/stats/cards/{other['id']}/result").json()["status"] == "ready"
        repaired = client.put(f"/api/v1/stats/cards/{first['id']}", json=definition(metric="count", metric_field=None, expected_updated_at=first["updated_at"]))
        assert repaired.status_code == 200
        assert client.get(f"/api/v1/stats/cards/{first['id']}/result").json()["status"] == "ready"
        stale = client.put(f"/api/v1/stats/cards/{first['id']}", json=definition(metric="count", metric_field=None, expected_updated_at=first["updated_at"]))
        assert stale.status_code == 409
        assert client.delete(f"/api/v1/stats/cards/{first['id']}").status_code == 204
        with client.app.state.session_factory() as session:
            assert session.get(DataTableRecord, "orders") is not None
            assert len(session.scalars(select(DataRowRecord)).all()) == 1


def test_limit_is_atomic_and_order_requires_complete_current_ids(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        seed(client)
        with ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(pool.map(lambda i: client.post("/api/v1/stats/cards", json=definition(name=f"统计{i}")).status_code, range(6)))
        assert codes.count(201) == 4 and codes.count(409) == 2
        cards = client.get("/api/v1/stats/cards").json()["items"]
        ids = [card["id"] for card in cards][::-1]
        response = client.put("/api/v1/stats/cards/order", json={"ids": ids})
        assert [card["id"] for card in response.json()["items"]] == ids
        assert client.put("/api/v1/stats/cards/order", json={"ids": ids[:-1]}).status_code == 409


def test_date_filter_follows_current_range_and_months_sort_in_time(tmp_path):
    client, _ = make_client(tmp_path)
    today = utc_now().astimezone().date()
    with client:
        seed(client, [{"amount": 2, "currency": "CNY", "date": today.isoformat()},
                      {"amount": 3, "currency": "CNY", "date": (today - timedelta(days=60)).isoformat()},
                      {"amount": 5, "currency": "CNY", "date": (today + timedelta(days=1)).isoformat()}])
        body = definition(group_field="date", time_bucket="month", date_field="date", time_range="dashboard")
        current = client.post("/api/v1/stats/cards/preview?days=7", json=body).json()
        assert current["analysis"]["totals"]["sum:amount"] == 2
        all_dates = client.post("/api/v1/stats/cards/preview", json={**body, "time_range": "all"}).json()
        assert all_dates["analysis"]["totals"]["sum:amount"] == 10
        labels = [row["label"] for row in all_dates["analysis"]["data"]]
        assert labels == sorted(labels)


def test_zoned_dates_follow_the_same_local_calendar_boundaries(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        seed(client, [{"amount": 7, "currency": "CNY", "date": utc_now().isoformat()}])
        body = definition(group_field="date", time_bucket="day", date_field="date", time_range="dashboard")
        result = client.post("/api/v1/stats/cards/preview?days=1", json=body).json()
        assert result["status"] == "ready"
        assert result["analysis"]["totals"] == {"sum:amount": 7}
        assert result["analysis"]["data"][0]["label"] == utc_now().astimezone().date().isoformat()


def test_cards_restore_with_business_database(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    client, settings = make_client(source)
    with client:
        seed(client, [{"amount": 3, "currency": "CNY"}])
        card = client.post("/api/v1/stats/cards", json=definition()).json()
    archive = create_business_backup(settings, tmp_path / "cards.dpbak")
    target = tmp_path / "restored"
    target.mkdir()
    target_client, target_settings = make_client(target)
    restore_business_backup(target_settings, archive, target)
    with target_client:
        cards = target_client.get("/api/v1/stats/cards").json()["items"]
        assert cards[0]["id"] == card["id"]
        assert target_client.get(f"/api/v1/stats/cards/{card['id']}/result").json()["analysis"]["totals"] == {"sum:amount": 3}
    with sqlite3.connect(target / "document-pipeline.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
