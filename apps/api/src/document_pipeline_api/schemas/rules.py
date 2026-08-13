import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Severity = Literal["warning", "error"]


class FieldExpression(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    op: Literal["field"]
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_rule_path(value)


class SumExpression(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    op: Literal["sum"]
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = validate_rule_path(value)
        if not path.startswith("items[]."):
            raise ValueError("求和表达式只能引用明细字段")
        return path


class ConstantExpression(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    op: Literal["constant"]
    value: float


class BinaryExpression(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    op: Literal["add", "subtract", "multiply", "divide"]
    left: "RuleExpression"
    right: "RuleExpression"


RuleExpression = Annotated[
    FieldExpression | SumExpression | ConstantExpression | BinaryExpression,
    Field(discriminator="op"),
]


class RequiredRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["required"]
    field: str
    severity: Severity = "error"

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        return validate_rule_path(value)


class RangeRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["range"]
    field: str
    minimum: float | None = None
    maximum: float | None = None
    severity: Severity = "error"

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        return validate_rule_path(value)

    @model_validator(mode="after")
    def validate_bounds(self) -> "RangeRule":
        if self.minimum is None and self.maximum is None:
            raise ValueError("范围规则至少需要最小值或最大值")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("范围规则的最小值不能大于最大值")
        return self


class EnumRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["enum"]
    field: str
    values: list[str | float | bool] = Field(min_length=1, max_length=100)
    severity: Severity = "error"

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        return validate_rule_path(value)


class PatternRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["pattern"]
    field: str
    pattern: str = Field(min_length=1, max_length=128)
    severity: Severity = "error"

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        return validate_rule_path(value)

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str) -> str:
        if any(character in value for character in "()|"):
            raise ValueError("安全正则不支持分组、回溯引用或分支")
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError("正则表达式格式不正确") from error
        return value


class EquationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["equation"]
    left: RuleExpression
    right: RuleExpression
    tolerance: float = Field(default=0.01, ge=0, le=1_000_000)
    field: str
    severity: Severity = "error"

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        return validate_rule_path(value)

    @model_validator(mode="after")
    def validate_expression_depth(self) -> "EquationRule":
        if max(_expression_depth(self.left), _expression_depth(self.right)) > 8:
            raise ValueError("计算表达式最多允许 8 层")
        if (
            _expression_uses_item_binding(self.left)
            or _expression_uses_item_binding(self.right)
        ) and not self.field.startswith("items[]."):
            raise ValueError("逐行计算规则必须把错误定位到明细字段")
        return self


ValidationRule = Annotated[
    RequiredRule | RangeRule | EnumRule | PatternRule | EquationRule,
    Field(discriminator="kind"),
]


def validate_rule_path(value: str) -> str:
    section, separator, key = value.partition(".")
    if section not in {"header", "items[]"} or separator != ".":
        raise ValueError("字段路径必须使用 header.字段 或 items[].字段")
    if not key or not key.replace("_", "a").isalnum() or not key[0].isalpha():
        raise ValueError("字段路径只能包含字母、数字和下划线")
    return value


def _expression_depth(expression: RuleExpression) -> int:
    if isinstance(expression, BinaryExpression):
        return 1 + max(
            _expression_depth(expression.left),
            _expression_depth(expression.right),
        )
    return 1


def _expression_uses_item_binding(expression: RuleExpression) -> bool:
    if isinstance(expression, FieldExpression):
        return expression.path.startswith("items[].")
    if isinstance(expression, BinaryExpression):
        return _expression_uses_item_binding(
            expression.left
        ) or _expression_uses_item_binding(expression.right)
    return False


BinaryExpression.model_rebuild()
