"""Term lengths of the state offices the ballot page shows — see
data/office_terms.json, which lists them explicitly and never defaults."""

import json
import os
from functools import lru_cache

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "office_terms.json")


@lru_cache(maxsize=1)
def _terms() -> dict:
    with open(_PATH) as f:
        return json.load(f)


def term_years(kind: str, state: str, office: str, year: int | None = None) -> int | None:
    """Years in one regular term of `office` in `state` elected in `year`,
    or None when the file does not list it. `kind` is "statewide",
    "legislature" or "judicial". A district-seated body
    ("public_service_commission-3") is looked up by its bare code.

    A term that depends on the year is stored as the rule, not as one
    cycle's answer: {"term": 4, "byYearEnding": {"0": 2}} (Minnesota's
    senate, two-year terms in years ending in 0), where null means the
    seats elected that year serve different terms and no single one is
    shown. With no `year` the regular term is returned."""
    value = _terms().get(kind, {}).get(state, {}).get(office.split("-")[0])
    if not isinstance(value, dict):
        return value
    by_year = value.get("byYearEnding") or {}
    if year is not None and str(year % 10) in by_year:
        return by_year[str(year % 10)]
    return value.get("term")
