import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import TemplateRecord
from document_pipeline_api.schemas.rules import ValidationRule
from document_pipeline_api.schemas.templates import TemplateCreate
from document_pipeline_api.services.templates import create_template


WIDE = 10**30 + 1
RULES = TypeAdapter(list[ValidationRule])


@pytest.mark.parametrize("rule", [
    {"kind": "equation", "field": "header.total", "tolerance": 0,
     "left": {"op": "constant", "value": WIDE},
     "right": {"op": "field", "path": "header.total"}},
    {"kind": "range", "field": "header.total", "minimum": WIDE},
    {"kind": "range", "field": "header.total", "maximum": WIDE},
    {"kind": "enum", "field": "header.total", "values": [WIDE]},
    # str(float(10**30)) looks unchanged, but the binary float itself differs
    # and would compare unequal to the exact integer in an enum rule.
    {"kind": "enum", "field": "header.total", "values": [10**30]},
    {"kind": "equation", "field": "header.total", "tolerance": "0.1234567890123456789",
     "left": {"op": "constant", "value": 1},
     "right": {"op": "field", "path": "header.total"}},
])
def test_rule_numeric_ingress_rejects_loss_before_template_is_saved(rule):
    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            with pytest.raises(ValidationError, match="精度"):
                create_template(session, TemplateCreate.model_validate({
                    "name": "Synthetic precise rule",
                    "fields": [{"key": "total", "label": "Total", "value_type": "number"}],
                    "deterministic_rules": [rule],
                }))
            assert session.scalar(select(func.count(TemplateRecord.id))) == 0
    finally:
        engine.dispose()


def test_supported_numbers_and_existing_float_rule_snapshots_keep_their_contract():
    rules = RULES.validate_python([
        {"kind": "equation", "field": "header.total", "tolerance": "0.01",
         "left": {"op": "constant", "value": "0.25"},
         "right": {"op": "field", "path": "header.total"}},
        {"kind": "range", "field": "header.total", "minimum": 1, "maximum": 1e30},
        {"kind": "enum", "field": "header.total", "values": ["1000000000000000000000000000001", True, False, 0.1, 3]},
    ])
    assert rules[0].left.value == 0.25
    assert rules[0].tolerance == 0.01
    assert rules[1].maximum == 1e30
    assert rules[2].values == ["1000000000000000000000000000001", True, False, 0.1, 3.0]
    assert [type(value) for value in rules[2].values] == [str, bool, bool, float, float]
