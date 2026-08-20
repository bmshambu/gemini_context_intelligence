"""Watch condition evaluation — pure, dependency-free (so it's unit-testable)."""


def condition_met(condition: dict, base: float, current: float) -> bool:
    t = (condition or {}).get("type")
    if t == "pct_drop":
        return current <= base * (1 - float(condition.get("pct", 0)) / 100)
    if t == "target_price":
        return current <= float(condition.get("target", 0))
    if t == "any_drop":
        return current < base
    if t == "back_in_stock":
        return True  # simulated: treat as back in stock on check
    return False
