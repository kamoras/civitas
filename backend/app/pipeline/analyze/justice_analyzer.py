"""A justice's voting record from Oyez: how often in the majority, in
dissent, in unanimous and close cases, the opinions written, and agreement
with each sitting justice. Shown on the scorecard, not scored.

The score is loyalty to the appointing president (justice_loyalty.py).
Removed in justice v2 (docs/research/justice-scores.md): Ideological
Consistency (the gap between agreement with the appointing party's bloc
and the other bloc's) and Independence (siding with the other bloc in split
decisions). On today's Court the appointing party and ideology coincide, so
both ranked justices by their distance from the Court's median (Spearman
-0.82 and -0.75 on the 2024 term), the construct Judicial Restraint was
removed for in v6.13, along with Bipartisan Agreement, which measured
Independence again.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# A "vote" that isn't a real participation on the merits (recusal,
# non-participation): excluded from pairwise agreement, so a shared
# non-vote can't read as two justices agreeing, and a recusal doesn't sit
# in an agreement denominator.
_PARTICIPATION_VOTES = ("majority", "minority")


def analyze_justice_votes(
    justice_id: str,
    votes: list[dict],
    all_case_votes: dict[str, list[dict]],
    active_ids: set[str],
) -> dict:
    """One justice's record: counts and shares of their votes, the opinions
    they wrote, and agreement with each sitting justice (`active_ids`), as
    the share of cases both decided that they decided the same way."""
    if not votes:
        return _empty_result()

    total = len(votes)
    majority_count = sum(1 for v in votes if v["vote"] == "majority")
    minority_count = sum(1 for v in votes if v["vote"] == "minority")
    unanimous_count = sum(1 for v in votes if v.get("is_unanimous"))
    close_votes = [v for v in votes if v.get("is_close")]
    close_majority = sum(1 for v in close_votes if v["vote"] == "majority")

    agree: dict[str, int] = defaultdict(int)
    both: dict[str, int] = defaultdict(int)
    for v in votes:
        if v["vote"] not in _PARTICIPATION_VOTES:
            continue
        for other in all_case_votes.get(v["case_id"], []):
            oid = other["justice_id"]
            if oid == justice_id or oid not in active_ids or other["vote"] not in _PARTICIPATION_VOTES:
                continue
            both[oid] += 1
            agree[oid] += other["vote"] == v["vote"]

    return {
        "cases_decided": total,
        "majority_pct": round(majority_count / total * 100, 1),
        "dissent_pct": round(minority_count / total * 100, 1),
        "unanimous_pct": round(unanimous_count / total * 100, 1),
        "authored_majority": sum(1 for v in votes if v["opinion_type"] == "majority"),
        "authored_dissent": sum(1 for v in votes if v["opinion_type"] == "dissent"),
        "authored_concurrence": sum(1 for v in votes if v["opinion_type"] == "concurrence"),
        "close_case_majority_pct": round(close_majority / len(close_votes) * 100, 1) if close_votes else 0.0,
        "agreement_matrix": {oid: round(agree[oid] / n * 100, 1) for oid, n in both.items() if n},
    }


def _empty_result() -> dict:
    return {
        "cases_decided": 0,
        "majority_pct": 0.0,
        "dissent_pct": 0.0,
        "unanimous_pct": 0.0,
        "authored_majority": 0,
        "authored_dissent": 0,
        "authored_concurrence": 0,
        "close_case_majority_pct": 0.0,
        "agreement_matrix": {},
    }
