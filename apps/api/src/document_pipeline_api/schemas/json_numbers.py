"""JSON values that survive JavaScript's IEEE-754 number parser."""

MAX_SAFE_JSON_INTEGER = 2**53 - 1


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
