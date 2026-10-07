"""Tests for president_service's response building and score breakdown."""

from app.models import President
from app.services.president_service import (
    get_current_president,
    get_president,
    get_president_leaderboard,
    get_president_score_breakdown,
)


def _make_president(id_: str, **overrides) -> President:
    defaults = dict(
        id=id_, name="Test President", party="D", number=99,
        term_start="2021-01-20", term_end=None, is_current=True,
    )
    defaults.update(overrides)
    return President(**defaults)


class TestGetPresident:
    def test_missing_president_returns_none(self, db_session):
        assert get_president(db_session, "nobody-0") is None

    def test_nullable_dimension_surfaces_as_none_not_zero(self, db_session):
        # No live data at all — every score dimension should come back
        # None, never a fabricated 0 or 50.
        db_session.add(_make_president("test-1"))
        db_session.commit()

        result = get_president(db_session, "test-1")
        assert result.score.public_mandate is None
        assert result.score.effectiveness is None
        assert result.score.effective_weights == {}
        assert result.score.historical_legacy is None
        assert result.score.overall == 0.0
        assert result.score.dimensions_available == 0

    def test_partial_scores_renormalize_overall_from_present_dimensions_only(self, db_session):
        # Only effectiveness has a stored score — overall should renormalize
        # to exactly that value, not average it against the other None slots.
        db_session.add(_make_president("test-2", score_effectiveness=70.0))
        db_session.commit()

        result = get_president(db_session, "test-2")
        assert result.score.effectiveness == 70.0
        assert result.score.public_mandate is None
        assert result.score.overall == 70.0

    def test_dimensions_available_counts_only_non_null_scores(self, db_session):
        db_session.add(_make_president(
            "test-4", score_public_mandate=70.0, score_effectiveness=50.0,
        ))
        db_session.commit()

        result = get_president(db_session, "test-4")
        assert result.score.dimensions_available == 2


class TestGetPresidentLeaderboardExcludesCurrent:
    """2026-07: ranking the currently-serving president alongside completed
    terms compares a structurally-incomplete record (no C-SPAN Historians
    Survey rating yet, often no full-term GDP/jobs data) to complete ones
    under one ordinal position — get_current_president serves their own
    separate, non-ranked profile instead."""

    def test_current_president_excluded_from_leaderboard(self, db_session):
        db_session.add(_make_president("current-1", is_current=True, term_end=None))
        db_session.add(_make_president("historical-1", is_current=False, term_end="2020-01-20"))
        db_session.commit()

        entries = get_president_leaderboard(db_session)
        assert [e.id for e in entries] == ["historical-1"]

    def test_get_current_president_returns_the_current_one(self, db_session):
        db_session.add(_make_president("current-1", is_current=True, term_end=None))
        db_session.add(_make_president("historical-1", is_current=False, term_end="2020-01-20"))
        db_session.commit()

        result = get_current_president(db_session)
        assert result.id == "current-1"

    def test_get_current_president_returns_none_with_no_current_president(self, db_session):
        db_session.add(_make_president("historical-1", is_current=False, term_end="2020-01-20"))
        db_session.commit()

        assert get_current_president(db_session) is None


class TestGetPresidentScoreBreakdown:
    def test_missing_president_returns_none(self, db_session):
        assert get_president_score_breakdown(db_session, "nobody-0") is None

    def test_breakdown_has_all_four_dimensions(self, db_session):
        db_session.add(_make_president("test-3", gdp_growth_avg=3.0))
        db_session.commit()

        breakdown = get_president_score_breakdown(db_session, "test-3")
        assert set(breakdown) == {
            "publicMandate", "effectiveness", "historicalLegacy",
        }
        assert breakdown["effectiveness"]["score"] is not None
        assert breakdown["publicMandate"]["score"] is None

    def test_facts_carry_each_figure_and_the_average_it_is_scored_against(self, db_session):
        """The scorecard states these; it computes none of them."""
        db_session.add(_make_president(
            "trump-47", name="Donald J. Trump", number=47, term_start="2025-01-20",
            avg_approval=37.3, approval_trend=-5.6, recent_avg_approval=35.4,
            jobs_created_millions=0.5,
        ))
        db_session.add(_make_president(
            "trump-45", name="Donald J. Trump", number=45, term_start="2017-01-20", term_end="2021-01-20",
            is_current=False, historical_legacy_score=312, score_historical_legacy=12.0,
        ))
        db_session.commit()

        b = get_president_score_breakdown(db_session, "trump-47")
        mandate = b["publicMandate"]["facts"]
        assert (mandate["approval"], mandate["approvalTrend"], mandate["recentApproval"]) == (37.3, -5.6, 35.4)
        assert mandate["approvalMean"] is not None  # the all-president average it's scored against
        assert b["effectiveness"]["facts"]["jobsMillions"] == 0.5
        # A sitting president's term is unrated; the same person's other
        # presidency's rating is named.
        assert b["historicalLegacy"]["facts"]["points"] is None
        assert b["historicalLegacy"]["facts"]["otherTerms"] == [
            {"id": "trump-45", "number": 45, "points": 312, "score": 12.0},
        ]
