"""Explicit holding corrections for cases that cannot be inferred reliably."""
from .models import Row

def _matches(actual: str | None, condition) -> bool:
    condition = condition if isinstance(condition, dict) else {"value": condition}
    actual = (actual or "").strip()
    expected = str(condition["value"]).strip()
    if not condition.get("caseSensitive", True):
        actual, expected = actual.casefold(), expected.casefold()
    return actual == expected if condition.get("fullMatch", True) else expected in actual


def apply_manual_updates(rows: list[Row], rules: list[dict]) -> int:
    """Apply matching rules sequentially and return the number of changed rows."""
    # Validate all rules before touching rows so typos cannot partially apply a file.
    for rule in rules:
        conditions = {key: value for key, value in rule.items() if key != "TO_BE_CHANGED"}
        updates = rule["TO_BE_CHANGED"]
        if not conditions or not updates:
            raise ValueError("manual update rules require conditions and TO_BE_CHANGED values")
        for column in conditions.keys() | updates.keys():
            if column not in Row.__dataclass_fields__:
                raise ValueError(f"unknown manual update field: {column}")
        for condition in conditions.values():
            if isinstance(condition, dict) and "value" not in condition:
                raise ValueError("manual update condition requires value")

    changed = 0
    for row in rows:
        before = vars(row).copy()
        for rule in rules:
            if all(_matches(getattr(row, column), condition)
                   for column, condition in rule.items() if column != "TO_BE_CHANGED"):
                for column, value in rule["TO_BE_CHANGED"].items():
                    setattr(row, column, str(value) if value is not None else None)
        changed += vars(row) != before
    return changed
