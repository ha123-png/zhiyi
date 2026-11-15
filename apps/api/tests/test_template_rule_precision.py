import pytest
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.domain.template_rules import validate_template_rules
from document_pipeline_api.schemas.extraction import DocumentKind, TemplateExtraction
from document_pipeline_api.schemas.rules import ValidationRule
from document_pipeline_api.schemas.templates import TemplateCreate
from document_pipeline_api.services.rule_evaluation import evaluate_rules
from document_pipeline_api.services.template_processing import build_template_extraction_model
from document_pipeline_api.services.templates import create_template


def _field(key):
    return {"op": "field", "path": f"header.{key}"}


def _binary(op, left, right):
    return {"op": op, "left": left, "right": right}


@pytest.fixture
def evaluate():
    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        def run(left, header, items=None, tolerance=0):
            template = create_template(session, TemplateCreate.model_validate({
                "name": "Synthetic exact arithmetic",
                "fields": [
                    {"key": key, "label": key, "value_type": "number"}
                    for key in ["a", "b", "total"]
                ] + [{"key": "amount", "label": "amount", "value_type": "number", "section": "item"}],
                "deterministic_rules": [{
                    "kind": "equation", "field": "header.total", "tolerance": tolerance,
                    "left": left, "right": _field("total"),
                }],
            }))
            # Match the normal model-result path, then execute the saved exact
            # template version through the production rule-evaluation service.
            payload = {"header": {"a": None, "b": None, **header}, "items": items or []}
            parsed = build_template_extraction_model(template).model_validate(payload)
            result = TemplateExtraction.model_validate(parsed.model_dump())
            original = result.model_dump()
            evaluation = evaluate_rules(
                session, document_kind=DocumentKind.CUSTOM, result=result,
                template_id=template.id, template_version=template.version,
            )
            assert result.model_dump() == original
            return evaluation
        yield run
    engine.dispose()


@pytest.mark.parametrize("difference", [0, 1])
def test_sum_preserves_wide_integer_and_distinguishes_one_unit(evaluate, difference):
    exact = 10**30 + 1
    evaluation = evaluate(
        {"op": "sum", "path": "items[].amount"},
        {"total": exact - difference}, [{"amount": exact}],
    )
    assert bool(evaluation.issues) is bool(difference)


@pytest.mark.parametrize("difference", [0, 1])
def test_multiplication_preserves_exact_product_and_detects_one_unit(evaluate, difference):
    a = 10**15 + 1
    evaluation = evaluate(
        _binary("multiply", _field("a"), _field("b")),
        {"a": a, "b": a, "total": a * a - difference},
    )
    assert bool(evaluation.issues) is bool(difference)


@pytest.mark.parametrize("op,a,b,total", [
    ("add", 10**30, 1, 10**30),
    ("subtract", 10**30 + 1, 0, 10**30),
])
def test_addition_and_subtraction_do_not_erase_a_unit(evaluate, op, a, b, total):
    evaluation = evaluate(_binary(op, _field("a"), _field("b")), {"a": a, "b": b, "total": total})
    assert len(evaluation.issues) == 1


def test_division_followed_by_multiplication_is_exact_at_zero_tolerance(evaluate):
    left = _binary("multiply", _binary("divide", _field("a"), _field("b")), _field("b"))
    evaluation = evaluate(left, {"a": 1, "b": 3, "total": 1})
    assert evaluation.issues == []


def test_expression_exceeding_calculation_budget_reports_issue_without_changing_input(evaluate):
    expression = _field("a")
    for _ in range(4):
        expression = _binary("multiply", expression, expression)
    evaluation = evaluate(expression, {"a": 1e308, "total": 0})
    assert len(evaluation.issues) == 1
    assert "计算范围" in evaluation.issues[0].message


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_historical_text_gets_numeric_issue_instead_of_exception(value):
    rules = TypeAdapter(list[ValidationRule]).validate_python([
        {"kind": "range", "field": "header.total", "minimum": 0},
    ])
    issues = validate_template_rules(TemplateExtraction(header={"total": value}, items=[]), rules, [])
    assert len(issues) == 1
    assert "必须是数字" in issues[0].message


def test_historical_text_with_extreme_exponent_is_rejected_before_integer_expansion():
    rules = TypeAdapter(list[ValidationRule]).validate_python([{
        "kind": "equation", "field": "header.total", "tolerance": 0,
        "left": _binary("multiply", _field("a"), {"op": "constant", "value": 2}),
        "right": _field("total"),
    }])
    result = TemplateExtraction(header={"a": "1e1000000000", "total": 0}, items=[])
    issues = validate_template_rules(result, rules, [])
    assert len(issues) == 1
    assert "计算范围" in issues[0].message
