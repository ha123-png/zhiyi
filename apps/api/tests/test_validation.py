from document_pipeline_api.domain.validation import validate_extraction
from document_pipeline_api.schemas.extraction import (
    DocumentExtraction,
    DocumentKind,
    LineItem,
)


def make_result(**changes) -> DocumentExtraction:
    values = {
        "document_type": "发票",
        "seller_name": "销售方",
        "buyer_name": "购买方",
        "document_number": "NO-1",
        "document_date": "2026-07-30",
        "amount_before_tax": 100,
        "tax_amount": 6,
        "total_amount": 106,
        "items": [
            LineItem(
                name="服务",
                specification=None,
                unit="项",
                quantity=1,
                unit_price=100,
                amount=100,
                tax_rate="6%",
                tax_amount=6,
            )
        ],
    }
    values.update(changes)
    return DocumentExtraction(**values)


def test_valid_invoice_has_no_deterministic_issues() -> None:
    assert validate_extraction(make_result(), DocumentKind.INVOICE) == []


def test_invoice_tax_mismatch_is_flagged() -> None:
    issues = validate_extraction(make_result(total_amount=105), DocumentKind.INVOICE)

    assert [issue.code for issue in issues] == ["invoice_tax_total_mismatch"]


def test_missing_role_is_sent_to_review() -> None:
    issues = validate_extraction(make_result(buyer_name=None), DocumentKind.INVOICE)

    assert issues[0].field == "buyer_name"


def test_line_quantity_times_price_must_equal_amount() -> None:
    broken_item = make_result().items[0].model_copy(update={"amount": 120})
    issues = validate_extraction(
        make_result(items=[broken_item], amount_before_tax=120),
        DocumentKind.INVOICE,
    )

    assert issues[0].code == "line_amount_mismatch"
    assert issues[0].field == "items[0].amount"
    assert "数量 1.0 × 单价 100.0" in issues[0].message
