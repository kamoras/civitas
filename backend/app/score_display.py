"""Scores as the site shows them.

The frontend shows every score as a whole number (lib/formatting.ts
displayScore: Math.round, half up). Anything the backend states as a shown
score, or ranks by one (the leaderboard, a profile's rank, a post's text),
goes through here, so the number and the rank match the page.

Outside app/pipeline/ on purpose: a display rule must not move the
analysis-code fingerprint (senate_pipeline._compute_analysis_code_hash).
"""

import math
import statistics
from collections import Counter

from app.config_definitions import SCORE_WEIGHTS


def displayed_score(score: float) -> int:
    """Math.round's rounding. Python's round() rounds half to even, so 56.5
    would read 56 here and 57 on the page."""
    return math.floor(score + 0.5)


def displayed_rank(score: float, field: list[float]) -> int:
    """Standard competition rank ("1224") of `score` within `field`, both
    compared as displayed: members who show the same number share a rank."""
    mine = displayed_score(score)
    return 1 + sum(1 for v in field if displayed_score(v) > mine)


def thin_dimensions(confidence: dict | None) -> list[str]:
    """The weighted dimensions (SCORE_WEIGHTS) whose data-sufficiency grade
    (score_calculator.calculate_confidence) is "low": too little source data
    behind them for the number to be more than a pull toward neutral."""
    return [k for k in SCORE_WEIGHTS if (confidence or {}).get(k) == "low"]


def score_spread(scores: list[float]) -> dict:
    """How a chamber's overall scores are spread, as displayed: how many
    members show each whole number, and the quartiles. Members who show
    the same number share a rank, so the counts say how much a rank
    difference is worth; the quartiles say how tightly the field sits."""
    shown = [displayed_score(s) for s in scores]
    counts = Counter(shown)
    quartiles = (statistics.quantiles(shown, n=4, method="inclusive") if len(shown) >= 2
                 else [shown[0]] * 3 if shown else [None] * 3)
    return {
        "total": len(shown),
        "counts": [{"score": v, "members": counts[v]} for v in sorted(counts)],
        "p25": None if quartiles[0] is None else displayed_score(quartiles[0]),
        "median": None if quartiles[1] is None else displayed_score(quartiles[1]),
        "p75": None if quartiles[2] is None else displayed_score(quartiles[2]),
    }
