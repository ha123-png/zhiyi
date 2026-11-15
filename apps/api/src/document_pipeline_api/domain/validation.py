from document_pipeline_api.schemas.extraction import (
    DocumentExtraction,
    DocumentKind,
    ValidationIssue,
)


BUILTIN_RULE_ENGINE_VERSION = "builtin-v1"


def validate_extraction(
    result: DocumentExtraction,
    document_kind: DocumentKind,
    *,
    manual_revision: bool = False,
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

    def can_compare(*values):
        return all(type(value) in (int, float) for value in values)

    def not_checked(code, field, label):
        issues.append(ValidationIssue(
            code=code, field=field, message=f"{label}未校验：计算所需的值为空或为文本。",
            severity="warning", not_checked=True,
        ))

    item_amounts = [item.amount for item in result.items]
    item_total = (
        None if manual_revision and not can_compare(*item_amounts)
        else sum(value or 0 for value in item_amounts)
    )
    for index, item in enumerate(result.items):
        if not can_compare(item.quantity, item.unit_price, item.amount):
            if manual_revision:
                not_checked("line_amount_mismatch", f"items[{index}].amount", "明细金额")
            continue
        if abs(item.quantity * item.unit_price - item.amount) >= 0.01:
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
        if manual_revision and not can_compare(item_total, result.amount_before_tax):
            not_checked("invoice_line_total_mismatch", "amount_before_tax", "明细金额合计")
        elif can_compare(item_total, result.amount_before_tax) and abs(item_total - result.amount_before_tax) >= 0.01:
            issues.append(
                ValidationIssue(
                    code="invoice_line_total_mismatch",
                    field="amount_before_tax",
                    message="明细金额之和与不含税金额不一致。",
                    severity="error",
                )
            )
        if not can_compare(result.amount_before_tax, result.tax_amount, result.total_amount):
            if manual_revision:
                not_checked("invoice_tax_total_mismatch", "total_amount", "价税合计")
        elif abs(result.amount_before_tax + result.tax_amount - result.total_amount) >= 0.01:
            issues.append(
                ValidationIssue(
                    code="invoice_tax_total_mismatch",
                    field="total_amount",
                    message="不含税金额与税额之和不等于价税合计。",
                    severity="error",
                )
            )
    elif manual_revision and not can_compare(item_total, result.total_amount):
        not_checked("delivery_line_total_mismatch", "total_amount", "明细金额合计")
    elif can_compare(item_total, result.total_amount) and abs(item_total - result.total_amount) >= 0.01:
        issues.append(
            ValidationIssue(
                code="delivery_line_total_mismatch",
                field="total_amount",
                message="明细金额之和与送货单合计不一致。",
                severity="error",
            )
        )
    return issues
