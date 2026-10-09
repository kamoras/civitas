"""Scores as the site shows them.

The frontend shows every score as a whole number (lib/formatting.ts
displayScore: Math.round, half up). Anything the backend states as a shown
score, or ranks by one (the leaderboard, a profile's rank, a post's text),
goes through here, so the number and the rank match the page.

Outside app/pipeline/ on purpose: a display rule must not move the
analysis-code fingerprint (senate_pipeline._compute_analysis_code_hash).
"""

import math


def displayed_score(score: float) -> int:
    """Math.round's rounding. Python's round() rounds half to even, so 56.5
    would read 56 here and 57 on the page."""
    return math.floor(score + 0.5)


def displayed_rank(score: float, field: list[float]) -> int:
    """Standard competition rank ("1224") of `score` within `field`, both
    compared as displayed: members who show the same number share a rank."""
    mine = displayed_score(score)
    return 1 + sum(1 for v in field if displayed_score(v) > mine)
