"""_senator_overall (politicians.py) now delegates its weighted sum to
score_calculator.compute_overall_score instead of carrying a fourth copy
of the same SCORE_WEIGHTS formula (senate_pipeline.py, house_pipeline.py,
and the senator/representative leaderboards were the other three, deduped
in #82/#83). This locks in its two remaining local behaviors: the
all-zero "not yet scored" guard and rounding to 1 decimal place."""

from app.api.politicians import _president_overall, _senator_overall
from app.models import President, Representative, Senator
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


class TestPresidentOverall:
    """2026-07 (#218 review S4): the four President score_* columns are
    nullable — a dimension that's genuinely inapplicable or not-yet-
    computed is None, never 0.0 (unlike Senator/Representative's 0.0
    "not yet scored" sentinel above). `v == 0.0` never matches None, so
    a fresh, fully-unscored president used to pass the old guard and get
    a real-looking overall score (compute_president_overall_score's
    defensive 0.0 fallback) instead of being excluded entirely; the guard
    also never checked score_historical_legacy at all."""

    def test_fully_unscored_president_returns_none(self):
        p = President(
            id="test-99", name="Test President", party="D", number=99,
            term_start="2029-01-20",
        )
        assert _president_overall(p) is None

    def test_only_historical_legacy_present_is_not_treated_as_unscored(self):
        # Regression: the old guard didn't check score_historical_legacy
        # at all, but this specifically verifies a president who has ONLY
        # that dimension (e.g. a fully historical figure pre-dating every
        # mechanical data source) is still scored, not excluded.
        p = President(
            id="test-98", name="Test President", party="D", number=98,
            term_start="1850-01-20", score_historical_legacy=42.0,
        )
        assert _president_overall(p) is not None

    def test_zero_is_a_real_score_not_a_sentinel(self):
        # Unlike senators/reps, 0.0 is a legitimate (if extreme) computed
        # value for a president, never a placeholder — must not be
        # excluded just because every present dimension happens to be 0.
        p = President(
            id="test-97", name="Test President", party="D", number=97,
            term_start="1850-01-20", score_public_mandate=0.0,
            score_effectiveness=0.0, score_agency_alignment=0.0,
            score_historical_legacy=0.0,
        )
        assert _president_overall(p) == 0.0
