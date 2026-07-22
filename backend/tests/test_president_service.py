"""Tests for president_service's response building and score breakdown."""

from app.models import President
from app.services.president_service import get_president, get_president_score_breakdown


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
        assert result.score.competence is None
        assert result.score.effectiveness is None
        assert result.score.agency_alignment is None
        assert result.score.historical_legacy is None
        assert result.score.overall == 0.0

    def test_partial_scores_renormalize_overall_from_present_dimensions_only(self, db_session):
        # Only competence has a stored score — overall should renormalize
        # to exactly that value, not average it against three None slots.
        db_session.add(_make_president("test-2", score_competence=70.0))
        db_session.commit()

        result = get_president(db_session, "test-2")
        assert result.score.competence == 70.0
        assert result.score.public_mandate is None
        assert result.score.overall == 70.0


class TestGetPresidentScoreBreakdown:
    def test_missing_president_returns_none(self, db_session):
        assert get_president_score_breakdown(db_session, "nobody-0") is None

    def test_breakdown_has_all_five_dimensions(self, db_session):
        db_session.add(_make_president("test-3", eo_count=200))
        db_session.commit()

        breakdown = get_president_score_breakdown(db_session, "test-3")
        assert set(breakdown) == {
            "publicMandate", "competence", "effectiveness", "agencyAlignment", "historicalLegacy",
        }
        assert breakdown["competence"]["score"] is not None
        assert breakdown["publicMandate"]["score"] is None
