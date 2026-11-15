"""Group headers and saved field views preserve exact stored numeric values."""
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import DataRowRecord, DataTableRecord
from document_pipeline_api.schemas.data_tables import DataRowUpdate
from document_pipeline_api.schemas.json_numbers import stringify_unsafe_integers
from document_pipeline_api.services.data_rows import update_data_row
from document_pipeline_api.services.data_views import create_split_views, get_data_view
from document_pipeline_api.services.source_cards import list_source_cards, source_card_details


TABLE_ID = "source-group-precision"
BIG_INTEGER = 9223372036854775809


@pytest.fixture
def engine(tmp_path):
    value = build_engine(f"sqlite:///{tmp_path / 'groups.db'}")
    Base.metadata.create_all(value)
    with Session(value) as session:
        session.add(DataTableRecord(
            id=TABLE_ID, name="Synthetic numeric groups", template_key="manual-precision",
            template_version="1", document_kind="custom", columns_json=json.dumps([
                {"key": "amount", "label": "Amount", "section": "header", "value_type": "number"},
                {"key": "name", "label": "Name", "section": "item", "value_type": "text"},
            ]),
        ))
        session.commit()
    yield value
    value.dispose()


def _seed(engine, values):
    with Session(engine) as session:
        rows = [DataRowRecord(
            table_id=TABLE_ID, item_index=index,
            row_json=json.dumps({"amount": value, "name": f"Synthetic {index}", "__row_group": "same-source"}),
        ) for index, value in enumerate(values, 1)]
        session.add_all(rows)
        session.commit()
        return [row.id for row in rows]


@pytest.mark.parametrize("value", [BIG_INTEGER, -BIG_INTEGER, 10**40 + 123, 1.2345678901234567])
def test_shared_numeric_header_card_and_details_keep_exact_value(engine, value):
    _seed(engine, [value, value])
    expected = stringify_unsafe_integers(value)
    with Session(engine) as session:
        card = list_source_cards(session, TABLE_ID)["groups"][0]
        preview = next(field for field in card["preview_header"] if field["key"] == "amount")
        assert preview["value"] == expected
        detail = source_card_details(session, TABLE_ID, card["id"])
        assert detail["header"]["amount"] == expected
        assert "amount" in detail["shared_header_keys"]


@pytest.mark.parametrize("first,second", [
    (BIG_INTEGER, BIG_INTEGER + 1),
    (1.2345678901234567, 1.2345678901234569),
])
def test_distinct_numeric_headers_are_never_collapsed_to_one_shared_cell(engine, first, second):
    _seed(engine, [first, second])
    with Session(engine) as session:
        card = list_source_cards(session, TABLE_ID)["groups"][0]
        assert "amount" not in card["shared_header_keys"]
        detail = source_card_details(session, TABLE_ID, card["id"])
        assert "amount" not in detail["header"]
        assert [row["values"]["amount"] for row in detail["rows"]] == [
            stringify_unsafe_integers(first), stringify_unsafe_integers(second),
        ]


@pytest.mark.parametrize("first,second", [
    (BIG_INTEGER, BIG_INTEGER + 1),
    (1.2345678901234567, 1.2345678901234569),
])
def test_editing_one_distinct_numeric_header_does_not_rewrite_other_rows(engine, first, second):
    identifiers = _seed(engine, [first, second])
    with Session(engine) as session:
        update_data_row(session, TABLE_ID, identifiers[0], DataRowUpdate(
            expected_version=1, changes={"amount": 100},
        ))
    with Session(engine) as check:
        rows = list(check.scalars(select(DataRowRecord).order_by(DataRowRecord.id)))
        assert [json.loads(row.row_json)["amount"] for row in rows] == [100, second]
        assert [row.row_version for row in rows] == [2, 1]


def test_large_integer_field_views_keep_distinct_keys_and_read_the_counted_rows(engine):
    values = [BIG_INTEGER, BIG_INTEGER + 1, -BIG_INTEGER, 1]
    identifiers = _seed(engine, values)
    with Session(engine) as session:
        views = create_split_views(session, TABLE_ID, "amount")
    assert len(views) == len(values)
    assert {view.field_value for view in views} == set(values)
    for view in views:
        with Session(engine) as session:
            detail = get_data_view(session, TABLE_ID, view.id)
            assert detail.row_count == view.row_count == 1
            assert [row.id for row in detail.rows] == [identifiers[values.index(view.field_value)]]
            assert detail.rows[0].values["amount"] == view.field_value


def test_group_readers_preserve_scalar_type_distinctions_in_legacy_rows(engine):
    values = [1, 1.0, "1", True, None]
    identifiers = _seed(engine, values)
    expected = {json.dumps(value): identifier for value, identifier in zip(values, identifiers)}
    with Session(engine) as session:
        card = list_source_cards(session, TABLE_ID)["groups"][0]
        assert "amount" not in card["shared_header_keys"]
    with Session(engine) as session:
        views = create_split_views(session, TABLE_ID, "amount")
    assert {json.dumps(view.field_value) for view in views} == set(expected)
    for view in views:
        with Session(engine) as session:
            detail = get_data_view(session, TABLE_ID, view.id)
            assert detail.row_count == 1
            assert [row.id for row in detail.rows] == [expected[json.dumps(view.field_value)]]
