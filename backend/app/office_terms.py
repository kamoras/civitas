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


def term_years(kind: str, state: str, office: str) -> int | None:
    """Years in one regular term of `office` in `state`, or None when the
    file does not list it. `kind` is "statewide", "legislature" or
    "judicial". A district-seated body ("public_service_commission-3") is
    looked up by its bare code."""
    return _terms().get(kind, {}).get(state, {}).get(office.split("-")[0])
