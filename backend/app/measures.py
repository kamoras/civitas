"""The four measures a member is compared on, read from the facts the
scorer stores beside each member's scores (score_breakdown): small gifts,
PAC money, breaks with party, bills moved. One definition for the peer
comparison (app/peer_comparison.py), the measures page and the facts each
score snapshot keeps (models.ScoreSnapshot.facts).

Imports nothing from the app, so models.py can use it. Outside
app/pipeline/, so it never moves the analysis-code fingerprint.
"""

import json
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Measure:
    key: str
    label: str
    dimension: str
    direction: str
    unit: str
    source: dict
    value: Callable[[dict], float | None]
    seat_expected: Callable[[dict], float | None]
    record: Callable[[dict], int | None]


def funding_facts(facts: dict) -> dict:
    return facts.get("fundingIndependence") or {}


def alignment_facts(facts: dict) -> dict:
    return facts.get("constituentAlignment") or {}


def bill_facts(facts: dict) -> dict:
    return facts.get("legislativeEffectiveness") or {}


def past_committee(facts: dict) -> int | None:
    stages = bill_facts(facts).get("billsByStage")
    # [introduced only, committee action, past committee, passed the chamber, law]
    return sum(stages[2:]) if isinstance(stages, list) and len(stages) == 5 else None


_FEC = {"name": "FEC filings", "url": "https://www.fec.gov/data/"}
MEASURES = (
    Measure("smallGifts", "Small gifts", "fundingIndependence",
            "higher means more of the money came in gifts of $200 or less", "share", _FEC,
            lambda f: funding_facts(f).get("smallDonorShare"),
            # The House compares with the chamber median, not a seat fit: no
            # seat expectation then, so the row's tick is the peer median.
            lambda f: (funding_facts(f).get("smallDonorExpectedShare")
                       if funding_facts(f).get("smallDonorComparison") != "house-median" else None),
            lambda f: 1 if funding_facts(f).get("contributions") else 0),
    Measure("pacMoney", "PAC money", "fundingIndependence",
            "higher means more of the money came from PACs", "share", _FEC,
            lambda f: funding_facts(f).get("pacShare") if funding_facts(f).get("contributions") else None,
            lambda f: None,
            lambda f: 1 if funding_facts(f).get("contributions") else 0),
    Measure("breaks", "Breaks with party", "constituentAlignment",
            "higher means more breaks with the party on party-line votes", "rate",
            {"name": "Roll-call votes", "url": "https://voteview.com/"},
            lambda f: alignment_facts(f).get("breakRate"),
            lambda f: alignment_facts(f).get("expectedBreakRate"),
            lambda f: alignment_facts(f).get("partyVotes")),
    Measure("billsMoved", "Bills moved", "legislativeEffectiveness",
            "higher means more of the member's bills got past committee", "count",
            {"name": "Congress.gov", "url": "https://www.congress.gov/"},
            past_committee,
            lambda f: None,
            lambda f: bill_facts(f).get("bills")),
)




def snapshot_facts(dimensions: dict | None) -> dict | None:
    """The four measures' figures, with the counts behind them, from a
    stored breakdown's dimensions: what a score snapshot keeps so history
    can show a measure over time. None without a breakdown."""
    if not isinstance(dimensions, dict):
        return None
    facts = {k: (v or {}).get("facts") or {} for k, v in dimensions.items() if isinstance(v, dict)}
    out = {m.key: m.value(facts) for m in MEASURES}
    out.update({
        "partyVotes": alignment_facts(facts).get("partyVotes"),
        "breakCount": alignment_facts(facts).get("breaks"),
        "bills": bill_facts(facts).get("bills"),
    })
    return out if any(v is not None for v in out.values()) else None


def snapshot_facts_json(stored_breakdown: str | None) -> str | None:
    """snapshot_facts as the column stores it, from a member row's
    score_breakdown column value."""
    try:
        kept = json.loads(stored_breakdown or "null")
    except (TypeError, ValueError):
        return None
    facts = snapshot_facts(kept.get("dimensions") if isinstance(kept, dict) else None)
    return json.dumps(facts) if facts else None
