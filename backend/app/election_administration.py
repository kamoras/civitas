"""How the last federal general was run, state by state: the EAC's Election
Administration and Voting Survey, as scripts/fetch_eavs.py summed it into
app/data/eavs.json. Rates are computed here, on the read, and only for a
state where every jurisdiction reported both counts a rate divides; a
state that reported in part shows its counts with how many reported,
never a rate over the part."""

import json
from functools import lru_cache
from pathlib import Path

from app.state_names import STATE_NAMES

_PATH = Path(__file__).resolve().parent / "data" / "eavs.json"

# (key, numerator, denominator): each rate the view shows, from the
# survey's own items.
RATES = (
    ("turnout", "F1a", "A1a"),  # voters who participated, of those registered
    ("byMail", "F1d", "F1a"),  # voted by mail, of all voters
    ("mailRejected", "C9a", "C1b"),  # mail ballots rejected, of those returned
    ("provisionalRejected", "E1d", "E1a"),  # provisional ballots rejected, of those cast
)


@lru_cache(maxsize=1)
def _survey() -> dict | None:
    try:
        return json.loads(_PATH.read_text())
    except (OSError, ValueError):
        return None


def _complete(entry: dict, code: str) -> bool:
    return entry["items"][code]["reporting"] == entry["jurisdictions"]


def _rates(entry: dict) -> dict:
    out = {}
    for key, num, den in RATES:
        denominator = entry["items"][den]["value"]
        ok = _complete(entry, num) and _complete(entry, den) and denominator > 0
        out[key] = round(entry["items"][num]["value"] / denominator, 4) if ok else None
    return out


def administration() -> dict | None:
    """The view's figures, or None when the survey file is missing."""
    survey = _survey()
    if survey is None:
        return None
    states = []
    for st, entry in survey["states"].items():
        if st not in STATE_NAMES:  # the states and DC; the territories have no electors or voting seat
            continue
        states.append({
            "state": st, "stateName": STATE_NAMES[st], "jurisdictions": entry["jurisdictions"],
            "items": entry["items"], "rates": _rates(entry),
        })
    # The national rate for each, over the states whose every jurisdiction
    # reported both counts, and how many those are.
    national = {}
    for key, num, den in RATES:
        full = [s for s in states if s["rates"][key] is not None]
        denominator = sum(s["items"][den]["value"] for s in full)
        national[key] = {
            "rate": round(sum(s["items"][num]["value"] for s in full) / denominator, 4) if denominator else None,
            "states": len(full),
        }
    return {"year": survey["year"], "version": survey["version"], "url": survey["url"],
            "items": survey["items"], "states": states, "national": national, "of": len(states)}
