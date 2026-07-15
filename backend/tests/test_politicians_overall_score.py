"""_senator_overall (politicians.py) now delegates its weighted sum to
score_calculator.compute_overall_score instead of carrying a fourth copy
of the same SCORE_WEIGHTS formula (senate_pipeline.py, house_pipeline.py,
and the senator/representative leaderboards were the other three, deduped
in #82/#83). This locks in its two remaining local behaviors: the
all-zero "not yet scored" guard and rounding to 1 decimal place."""

from app.api.politicians import _senator_overall
from app.models import Representative, Senator
from app.pipeline.analyze.score_calculator import compute_overall_score


def test_all_zero_scores_means_not_yet_scored():
    # Explicit 0.0s, not bare construction: SQLAlchemy column defaults
    # apply at INSERT, not at object construction, so an uncommitted bare
    # Senator() has None fields, not 0.0 — a different (untested-here)
    # code path than what a real not-yet-scored DB row looks like.
    s = Senator(
        id="S001", name="New Senator", state="CA", party="D",
        score_funding_independence=0.0, score_promise_persistence=0.0,
        score_independent_voting=0.0, score_funding_diversity=0.0,
        score_legislative_effectiveness=0.0,
    )
    assert _senator_overall(s) is None


def test_matches_compute_overall_score_rounded_to_one_decimal():
    s = Senator(
        id="S001", name="Test Senator", state="CA", party="D",
        score_funding_independence=61, score_promise_persistence=72,
        score_independent_voting=48, score_funding_diversity=55,
        score_legislative_effectiveness=80,
    )
    assert _senator_overall(s) == round(compute_overall_score(s), 1)


def test_works_for_representatives_too_via_duck_typing():
    r = Representative(
        id="R001", name="Test Rep", state="CA", district=1, party="D",
        score_funding_independence=61, score_promise_persistence=72,
        score_independent_voting=48, score_funding_diversity=55,
        score_legislative_effectiveness=80,
    )
    assert _senator_overall(r) == round(compute_overall_score(r), 1)
