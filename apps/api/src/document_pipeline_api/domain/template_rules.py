import re
from decimal import Decimal, InvalidOperation

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


TEMPLATE_RULE_ENGINE_VERSION = "template-v1"


def validate_template_rules(
    result: TemplateExtraction,
    rules: list[ValidationRule],
    fields: list[TemplateFieldRead],
) -> list[ValidationIssue]:
    labels = {(field.section, field.key): field.label for field in fields}
    issues: list[ValidationIssue] = []
    for rule_index, rule in enumerate(rules):
        bindings = range(len(result.items)) if _rule_uses_items(rule) else (None,)
        for item_index in bindings:
            issue = _validate_rule(result, rule, labels, rule_index, item_index)
            if issue is not None:
                issues.append(issue)
    return issues


def _validate_rule(
    result: TemplateExtraction,
    rule: ValidationRule,
    labels: dict[tuple[str, str], str],
    rule_index: int,
    item_index: int | None,
) -> ValidationIssue | None:
    field_path = _concrete_path(rule.field, item_index)
    label = _field_label(rule.field, labels)
    value = _resolve_field(result, rule.field, item_index)
    if isinstance(rule, RequiredRule):
        if value is None or (isinstance(value, str) and not value.strip()):
            return _issue(rule_index, field_path, f"{label}不能为空。", rule.severity)
        return None
    if value is None:
        return None
    if isinstance(rule, RangeRule):
        number = _decimal(value)
        if number is None:
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
        if len(text) > 512 or re.fullmatch(rule.pattern, text) is None:
            return _issue(
                rule_index,
                field_path,
                f"{label}格式不符合要求，当前为 {text[:64]}。",
                rule.severity,
            )
        return None
    if isinstance(rule, EquationRule):
        try:
            left = _evaluate_expression(result, rule.left, item_index)
            right = _evaluate_expression(result, rule.right, item_index)
        except RuleEvaluationError as error:
            return _issue(
                rule_index,
                field_path,
                f"{label}无法完成计算：{error}。",
                rule.severity,
            )
        if left is None or right is None:
            return None
        if abs(left - right) > Decimal(str(rule.tolerance)):
            return _issue(
                rule_index,
                field_path,
                f"{label}计算不一致：左侧为 {left}，右侧为 {right}。",
                rule.severity,
            )
    return None


def _evaluate_expression(
    result: TemplateExtraction,
    expression: RuleExpression,
    item_index: int | None,
) -> Decimal | None:
    if isinstance(expression, ConstantExpression):
        return Decimal(str(expression.value))
    if isinstance(expression, FieldExpression):
        return _decimal(_resolve_field(result, expression.path, item_index))
    if isinstance(expression, SumExpression):
        values = (
            _decimal(_resolve_field(result, expression.path, index))
            for index in range(len(result.items))
        )
        present = [value for value in values if value is not None]
        return sum(present, Decimal(0)) if present else None
    if isinstance(expression, BinaryExpression):
        left = _evaluate_expression(result, expression.left, item_index)
        right = _evaluate_expression(result, expression.right, item_index)
        if left is None or right is None:
            return None
        if expression.op == "add":
            return left + right
        if expression.op == "subtract":
            return left - right
        if expression.op == "multiply":
            return left * right
        if right == 0:
            raise RuleEvaluationError("除数为 0")
        return left / right
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
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


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
