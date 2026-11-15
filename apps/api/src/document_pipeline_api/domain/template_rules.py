from decimal import Decimal, Inexact, InvalidOperation, localcontext
from fractions import Fraction

from document_pipeline_api.domain.pattern_matching import matches_pattern

from document_pipeline_api.schemas.extraction import TemplateExtraction, ValidationIssue
from document_pipeline_api.schemas.rules import (
    BinaryExpression,
    ConstantExpression,
    EnumRule,
    EquationRule,
    FieldExpression,
    PatternRule,
    RangeRule,
    RequiredRule,
    RuleExpression,
    SumExpression,
    ValidationRule,
)
from document_pipeline_api.schemas.templates import TemplateFieldRead


TEMPLATE_RULE_ENGINE_VERSION = "template-v3"
# Finite business inputs have at most 309 integer digits or a float exponent
# of about 324. Allow ample intermediate headroom without expanding arbitrary
# historical text exponents or unbounded expression products into giant ints.
_MAX_CALCULATION_BITS = 13_000  # fewer than 4,000 decimal digits per integer
_MAX_DECIMAL_COMPONENT = 3_900
_MAX_NUMERIC_TEXT = 4_300


def validate_template_rules(
    result: TemplateExtraction,
    rules: list[ValidationRule],
    fields: list[TemplateFieldRead],
    *,
    manual_revision: bool = False,
) -> list[ValidationIssue]:
    labels = {(field.section, field.key): field.label for field in fields}
    issues: list[ValidationIssue] = []
    for rule_index, rule in enumerate(rules):
        bindings = range(len(result.items)) if _rule_uses_items(rule) else (None,)
        for item_index in bindings:
            try:
                issue = _validate_rule(result, rule, labels, rule_index, item_index, manual_revision=manual_revision)
            except TimeoutError:
                issues.append(_issue(rule_index, _concrete_path(rule.field, item_index),
                    "格式表达式计算过久，已停止该规则的检查；请简化表达式后重新核对。", "error"))
                break
            if issue is not None:
                issues.append(issue)
    return issues


def _validate_rule(
    result: TemplateExtraction,
    rule: ValidationRule,
    labels: dict[tuple[str, str], str],
    rule_index: int,
    item_index: int | None,
    *,
    manual_revision: bool,
) -> ValidationIssue | None:
    field_path = _concrete_path(rule.field, item_index)
    label = _field_label(rule.field, labels)
    value = _resolve_field(result, rule.field, item_index)
    if isinstance(rule, RequiredRule):
        if value is None or (isinstance(value, str) and not value.strip()):
            return _issue(rule_index, field_path, f"{label}不能为空。", rule.severity)
        return None
    if value is None and not (manual_revision and isinstance(rule, (RangeRule, EquationRule))):
        return None
    if isinstance(rule, RangeRule):
        number = None if manual_revision and isinstance(value, str) else _decimal(value)
        if number is None:
            if manual_revision:
                return _not_checked(rule_index, field_path, label)
            return _issue(rule_index, field_path, f"{label}必须是数字。", rule.severity)
        if rule.minimum is not None and number < Decimal(str(rule.minimum)):
            return _issue(
                rule_index,
                field_path,
                f"{label}不能小于 {rule.minimum}，当前为 {value}。",
                rule.severity,
            )
        if rule.maximum is not None and number > Decimal(str(rule.maximum)):
            return _issue(
                rule_index,
                field_path,
                f"{label}不能大于 {rule.maximum}，当前为 {value}。",
                rule.severity,
            )
        return None
    if isinstance(rule, EnumRule):
        if value not in rule.values:
            expected = "、".join(str(item) for item in rule.values)
            return _issue(
                rule_index,
                field_path,
                f"{label}应为 {expected} 之一，当前为 {value}。",
                rule.severity,
            )
        return None
    if isinstance(rule, PatternRule):
        text = str(value)
        if not matches_pattern(rule.pattern, text):
            return _issue(
                rule_index,
                field_path,
                f"{label}格式不符合要求，当前为 {text[:64]}。",
                rule.severity,
            )
        return None
    if isinstance(rule, EquationRule):
        try:
            left = _evaluate_expression(result, rule.left, item_index, require_numbers=manual_revision)
            right = _evaluate_expression(result, rule.right, item_index, require_numbers=manual_revision)
            if left is None or right is None:
                return _not_checked(rule_index, field_path, label) if manual_revision else None
            different = abs(_bounded_fraction(left - right)) > _fraction(rule.tolerance)
        except RuleEvaluationError as error:
            return _issue(
                rule_index,
                field_path,
                f"{label}无法完成计算：{error}。",
                rule.severity,
            )
        if different:
            return _issue(
                rule_index,
                field_path,
                f"{label}计算不一致：左侧为 {_display_fraction(left)}，右侧为 {_display_fraction(right)}。",
                rule.severity,
            )
    return None


def _not_checked(rule_index: int, field_path: str, label: str) -> ValidationIssue:
    return _issue(rule_index, field_path, f"{label}未校验：计算所需的值为空或为文本。", "warning").model_copy(
        update={"not_checked": True},
    )


def _evaluate_expression(
    result: TemplateExtraction,
    expression: RuleExpression,
    item_index: int | None,
    *,
    require_numbers: bool,
) -> Fraction | None:
    if isinstance(expression, ConstantExpression):
        return _fraction(expression.value)
    if isinstance(expression, FieldExpression):
        raw = _resolve_field(result, expression.path, item_index)
        return None if require_numbers and isinstance(raw, str) else _fraction(raw)
    if isinstance(expression, SumExpression):
        total = None
        for index in range(len(result.items)):
            raw = _resolve_field(result, expression.path, index)
            value = None if require_numbers and isinstance(raw, str) else _fraction(raw)
            if value is None and require_numbers:
                return None
            if value is not None:
                total = value if total is None else _bounded_fraction(total + value)
        return total
    if isinstance(expression, BinaryExpression):
        left = _evaluate_expression(result, expression.left, item_index, require_numbers=require_numbers)
        right = _evaluate_expression(result, expression.right, item_index, require_numbers=require_numbers)
        if left is None or right is None:
            return None
        if expression.op == "add":
            return _bounded_fraction(left + right)
        if expression.op == "subtract":
            return _bounded_fraction(left - right)
        if expression.op == "multiply":
            return _bounded_fraction(left * right)
        if right == 0:
            raise RuleEvaluationError("除数为 0")
        return _bounded_fraction(left / right)
    raise TypeError("未知规则表达式")


def _resolve_field(
    result: TemplateExtraction,
    path: str,
    item_index: int | None,
):
    section, key = path.split(".", 1)
    if section == "header":
        return result.header.get(key)
    if item_index is None or item_index >= len(result.items):
        return None
    return result.items[item_index].get(key)


def _rule_uses_items(rule: ValidationRule) -> bool:
    if rule.field.startswith("items[]."):
        return True
    return isinstance(rule, EquationRule) and (
        _expression_uses_item_binding(rule.left)
        or _expression_uses_item_binding(rule.right)
    )


def _expression_uses_item_binding(expression: RuleExpression) -> bool:
    if isinstance(expression, FieldExpression):
        return expression.path.startswith("items[].")
    if isinstance(expression, SumExpression):
        return False
    if isinstance(expression, BinaryExpression):
        return _expression_uses_item_binding(
            expression.left
        ) or _expression_uses_item_binding(expression.right)
    return False


def _concrete_path(path: str, item_index: int | None) -> str:
    return path.replace("items[]", f"items[{item_index}]") if item_index is not None else path


def _field_label(path: str, labels: dict[tuple[str, str], str]) -> str:
    section, key = path.split(".", 1)
    return labels.get(("item" if section == "items[]" else "header", key), key)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value)
        if len(text) > _MAX_NUMERIC_TEXT:
            return None
        with localcontext():
            number = Decimal(text)
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _fraction(value: object) -> Fraction | None:
    if isinstance(value, str) and len(value) > _MAX_NUMERIC_TEXT:
        raise RuleEvaluationError("数值超出计算范围，请拆分计算或简化规则")
    number = _decimal(value)
    if number is None:
        return None
    _, digits, exponent = number.as_tuple()
    if len(digits) > _MAX_DECIMAL_COMPONENT or abs(exponent) > _MAX_DECIMAL_COMPONENT:
        raise RuleEvaluationError("数值超出计算范围，请拆分计算或简化规则")
    return _bounded_fraction(Fraction(number))


def _bounded_fraction(number: Fraction) -> Fraction:
    if max(number.numerator.bit_length(), number.denominator.bit_length()) > _MAX_CALCULATION_BITS:
        raise RuleEvaluationError("数值超出计算范围，请拆分计算或简化规则")
    return number


def _display_fraction(number: Fraction) -> str:
    if number.denominator == 1:
        return str(number.numerator)
    # Display terminating decimals exactly when they fit the same small budget.
    # Repeating decimals retain the exact fraction instead of a rounded claim.
    with localcontext() as context:
        context.prec = _MAX_NUMERIC_TEXT
        context.traps[Inexact] = True
        try:
            return str(Decimal(number.numerator) / Decimal(number.denominator))
        except Inexact:
            return str(number)


def _issue(
    rule_index: int,
    field: str,
    message: str,
    severity: str,
) -> ValidationIssue:
    return ValidationIssue(
        code=f"template_rule_{rule_index + 1}",
        field=field,
        message=message,
        severity=severity,
    )


class RuleEvaluationError(ValueError):
    pass
