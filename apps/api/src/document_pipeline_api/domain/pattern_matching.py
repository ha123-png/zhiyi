"""Use the same bounded format check in rule execution and the editor preview."""
import regex


def matches_pattern(pattern: str, text: str) -> bool:
    if len(text) > 512:
        return False
    # VERSION0 retains the existing re semantics. Timeout also releases a worker
    # from ambiguous repeats that do not contain forbidden groups or branches.
    return regex.fullmatch(pattern, text, flags=regex.VERSION0, timeout=0.02) is not None
