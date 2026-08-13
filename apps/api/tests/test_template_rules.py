import pytest
from pydantic import TypeAdapter, ValidationError

from document_pipeline_api.domain.template_rules import validate_template_rules
from document_pipeline_api.schemas.extraction import TemplateExtraction
from document_pipeline_api.schemas.rules import ValidationRule
from document_pipeline_api.schemas.templates import TemplateFieldRead


RULES = TypeAdapter(list[ValidationRule])
FIELDS = [
    TemplateFieldRead(key="total", label="合计", value_type="number"),
    TemplateFieldRead(key="status", label="状态"),
    TemplateFieldRead(key="code", label="单号"),
    TemplateFieldRead(key="qty", label="数量", section="item", value_type="number"),
    TemplateFieldRead(key="price", label="单价", section="item", value_type="number"),
    TemplateFieldRead(key="amount", label="金额", section="item", value_type="number"),
]


def result() -> TemplateExtraction:
    return TemplateExtraction(
        header={"total": 25, "status": "有效", "code": "PO-001"},
        items=[{"qty": 2, "price": 10, "amount": 20}, {"qty": 1, "price": 5, "amount": 5}],
    )


def test_validates_required_range_enum_and_safe_pattern() -> None:
    rules = RULES.validate_python(
        [
            {"kind": "required", "field": "header.code"},
            {"kind": "range", "field": "header.total", "minimum": 0, "maximum": 100},
            {"kind": "enum", "field": "header.status", "values": ["有效", "作废"]},
            {"kind": "pattern", "field": "header.code", "pattern": r"PO-\d{3}"},
        ]
    )

    assert validate_template_rules(result(), rules, FIELDS) == []


def test_rejects_unsafe_regex_and_invalid_paths() -> None:
    with pytest.raises(ValidationError, match="不支持分组"):
        RULES.validate_python(
            [{"kind": "pattern", "field": "header.code", "pattern": "(a+)+"}]
        )
    with pytest.raises(ValidationError, match="header.字段"):
        RULES.validate_python([{"kind": "required", "field": "__class__"}])
    with pytest.raises(ValidationError, match="格式不正确"):
        RULES.validate_python(
            [{"kind": "pattern", "field": "header.code", "pattern": "[abc"}]
        )
    with pytest.raises(ValidationError, match="finite number"):
        RULES.validate_python(
            [{"kind": "range", "field": "header.total", "maximum": float("nan")}]
        )


def test_validates_each_item_equation_and_reports_concrete_row() -> None:
    rules = RULES.validate_python(
        [
            {
                "kind": "equation",
                "field": "items[].amount",
                "left": {
                    "op": "multiply",
                    "left": {"op": "field", "path": "items[].qty"},
                    "right": {"op": "field", "path": "items[].price"},
                },
                "right": {"op": "field", "path": "items[].amount"},
                "tolerance": 0.01,
            }
        ]
    )
    broken = result()
    broken.items[1]["amount"] = 7

    issues = validate_template_rules(broken, rules, FIELDS)

    assert len(issues) == 1
    assert issues[0].field == "items[1].amount"
    assert "左侧为 5" in issues[0].message
    assert "右侧为 7" in issues[0].message


def test_sums_item_fields_without_repeating_header_rule_for_each_row() -> None:
    rules = RULES.validate_python(
        [
            {
                "kind": "equation",
                "field": "header.total",
                "left": {"op": "sum", "path": "items[].amount"},
                "right": {"op": "field", "path": "header.total"},
            }
        ]
    )

    assert validate_template_rules(result(), rules, FIELDS) == []
    broken = result()
    broken.header["total"] = 99
    issues = validate_template_rules(broken, rules, FIELDS)
    assert len(issues) == 1
    assert issues[0].field == "header.total"


def test_rejects_header_target_for_row_expression_and_reports_division_by_zero() -> None:
    with pytest.raises(ValidationError, match="定位到明细字段"):
        RULES.validate_python(
            [
                {
                    "kind": "equation",
                    "field": "header.total",
                    "left": {"op": "field", "path": "items[].amount"},
                    "right": {"op": "constant", "value": 1},
                }
            ]
        )

    rules = RULES.validate_python(
        [
            {
                "kind": "equation",
                "field": "header.total",
                "left": {
                    "op": "divide",
                    "left": {"op": "field", "path": "header.total"},
                    "right": {"op": "constant", "value": 0},
                },
                "right": {"op": "constant", "value": 1},
            }
        ]
    )
    issues = validate_template_rules(result(), rules, FIELDS)
    assert len(issues) == 1
    assert "除数为 0" in issues[0].message
