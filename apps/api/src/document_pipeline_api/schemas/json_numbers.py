"""JSON values that survive JavaScript's IEEE-754 number parser."""

import json
import math
import re
from decimal import Decimal, DecimalException, localcontext

from pydantic import TypeAdapter

MAX_SAFE_JSON_INTEGER = 2**53 - 1
MAX_NUMBER_TOKEN_LENGTH = 4300
PRECISION_MESSAGE = "数值精度超出当前支持范围，本次未保存。请核对原值，或使用文本字段保留原文。"
_MODEL_FLOAT_GRAMMAR = TypeAdapter(float)


class NumericPrecisionError(ValueError):
    """A numeric input would change value in the finite int/float contract."""


class JSONNumberToken(str):
    """Numeric lexeme kept until the consumer can validate or display it."""


def number_from_token(token: str) -> int | float:
    """Return ordinary numbers only; Decimal is comparison state, never output."""
    if len(token) > MAX_NUMBER_TOKEN_LENGTH:
        raise NumericPrecisionError(PRECISION_MESSAGE)
    try:
        if re.fullmatch(r"[+-]?\d+", token):
            value = int(token)
        else:
            value = float(token)
            # Rejected extreme exponents must not change the caller's flags.
            with localcontext():
                # Exact zero does not depend on Decimal's bounded exponent range.
                zero = value == 0 and Decimal(re.split("[eE]", token, maxsplit=1)[0]).is_zero()
                if not math.isfinite(value) or (not zero and Decimal(token) != Decimal(str(value))):
                    raise NumericPrecisionError(PRECISION_MESSAGE)
        # Match the existing finite numeric-cell range, including integer input.
        if not math.isfinite(value):
            raise NumericPrecisionError(PRECISION_MESSAGE)
        return value
    except (OverflowError, DecimalException) as error:
        raise NumericPrecisionError(PRECISION_MESSAGE) from error


def loads_json_numbers(raw: str | bytes, *, keep_unrepresentable: bool = False):
    """Decode business JSON before its decimal lexemes can silently round."""
    def preserve_number(token: str) -> int | float | JSONNumberToken:
        try:
            return number_from_token(token)
        except NumericPrecisionError:
            return JSONNumberToken(token)

    parse_number = preserve_number if keep_unrepresentable else number_from_token
    return json.loads(raw, parse_int=parse_number, parse_float=parse_number,
                      parse_constant=number_from_token)


def normalize_json_number_tokens(value):
    """Normalize only the selected business subtree of a protocol envelope."""
    if isinstance(value, JSONNumberToken):
        return number_from_token(value)
    if isinstance(value, dict):
        return {key: normalize_json_number_tokens(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_json_number_tokens(item) for item in value]
    if type(value) is int:
        return number_from_token(str(value))
    return value


def model_number(value):
    """Lossless coercion for a schema-declared model number, never text fields."""
    if isinstance(value, str):
        token = value.strip()
        if len(token) > MAX_NUMBER_TOKEN_LENGTH:
            raise NumericPrecisionError(PRECISION_MESSAGE)
        # Keep Pydantic's previous numeric-string grammar (including its
        # underscore forms). Ignore the rounded result and check original digits.
        _MODEL_FLOAT_GRAMMAR.validate_python(token)
        return number_from_token(token.replace("_", ""))
    if type(value) not in (int, float):
        raise ValueError("number 字段必须是数字。")
    try:
        if not math.isfinite(value):
            raise NumericPrecisionError(PRECISION_MESSAGE)
    except OverflowError as error:
        raise NumericPrecisionError(PRECISION_MESSAGE) from error
    return value


def stringify_unsafe_integers(value):
    """Represent unsafe integers as decimal strings in API JSON responses.

    JSON itself permits arbitrary precision number tokens, but common browser
    clients parse them as IEEE-754 doubles and silently round them. Keeping the
    exact digits as a string gives clients a lossless value to display and send
    back. Internal Python data and exported JSON files remain numeric.
    """
    if type(value) is int:
        return str(value) if abs(value) > MAX_SAFE_JSON_INTEGER else value
    if isinstance(value, dict):
        return {key: stringify_unsafe_integers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [stringify_unsafe_integers(item) for item in value]
    if isinstance(value, tuple):
        return [stringify_unsafe_integers(item) for item in value]
    return value
