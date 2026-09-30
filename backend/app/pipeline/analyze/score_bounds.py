"""Rounding a value and bounding it to a range (0–100 by default) — the one
``clamp``.

Shared by the scoring engine (score_calculator), the president scorer and
the scorecard validator (scores and percentages alike), so all three bound
a value identically. It lives in a module of its own, importing nothing,
so the validator and the president scorer can use it without loading the
scoring engine (and, with it, the app settings, database engine and ORM
models).
"""


def clamp(value: float, min_val: int = 0, max_val: int = 100) -> int:
    """Clamp a value to [min_val, max_val] and round to int."""
    return max(min_val, min(max_val, round(value)))
