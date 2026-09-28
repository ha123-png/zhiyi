"""Small, shared calculations for persisted file revision summaries."""


def detail_counts(change: dict | None) -> tuple[int, int, int]:
    if change is None:
        return 0, 0, 0
    before, after = change["before"], change["after"]
    if after is None:
        last = change.get("deleted_before")
        modified = 0
        if before is not None and last is not None:
            old, new = before["values"], last["values"]
            modified = sum(old.get(key) != new.get(key) for key in old.keys() | new.keys())
        return modified, 1, 0
    if before is None:
        return 0, 0, 1
    old, new = before["values"], after["values"]
    return sum(old.get(key) != new.get(key) for key in old.keys() | new.keys()), 0, 0
