from document_pipeline_api.schemas.extraction import (
    DocumentExtraction,
    DocumentKind,
    ValidationIssue,
)


BUILTIN_RULE_ENGINE_VERSION = "builtin-v1"


def validate_extraction(
    result: DocumentExtraction,
    document_kind: DocumentKind,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field, label in (
        ("seller_name", "销售方"),
        ("buyer_name", "购买方"),
        ("document_number", "单据号码"),
        ("document_date", "单据日期"),
        ("total_amount", "合计金额"),
    ):
        if getattr(result, field) in (None, ""):
            issues.append(
                ValidationIssue(
                    code="required_field_missing",
                    field=field,
                    message=f"{label}未识别，需要确认。",
                    severity="warning",
                )
            )

    item_total = sum(item.amount or 0 for item in result.items)
    for index, item in enumerate(result.items):
        if (
            item.quantity is not None
            and item.unit_price is not None
            and item.amount is not None
            and abs(item.quantity * item.unit_price - item.amount) >= 0.01
        ):
            issues.append(
                ValidationIssue(
                    code="line_amount_mismatch",
                    field=f"items[{index}].amount",
                    message=(
                        f"第 {index + 1} 行：数量 {item.quantity} × 单价 "
                        f"{item.unit_price} 不等于金额 {item.amount}。"
                    ),
                    severity="error",
                )
            )
    if document_kind is DocumentKind.INVOICE:
        if (
            result.amount_before_tax is not None
            and abs(item_total - result.amount_before_tax) >= 0.01
        ):
            issues.append(
                ValidationIssue(
                    code="invoice_line_total_mismatch",
                    field="amount_before_tax",
                    message="明细金额之和与不含税金额不一致。",
                    severity="error",
                )
            )
        if (
            result.amount_before_tax is not None
            and result.tax_amount is not None
            and result.total_amount is not None
            and abs(result.amount_before_tax + result.tax_amount - result.total_amount) >= 0.01
        ):
            issues.append(
                ValidationIssue(
                    code="invoice_tax_total_mismatch",
                    field="total_amount",
                    message="不含税金额与税额之和不等于价税合计。",
                    severity="error",
                )
            )
    elif result.total_amount is not None and abs(item_total - result.total_amount) >= 0.01:
        issues.append(
            ValidationIssue(
                code="delivery_line_total_mismatch",
                field="total_amount",
                message="明细金额之和与送货单合计不一致。",
                severity="error",
            )
        )
    return issues
