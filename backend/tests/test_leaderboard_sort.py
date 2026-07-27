"""get_leaderboard / get_rep_leaderboard sort by score_calculator's shared
compute_overall_score (previously each had its own copy-pasted
_FIELD_TO_WEIGHT_KEY dict + _weighted_score closure computing the identical
SCORE_WEIGHTS-weighted sum)."""

from app.models import Representative, Senator
from app.services.representative_service import get_rep_leaderboard
from app.services.senator_service import get_leaderboard


def _senator(id, name, funding_independence):
    return Senator(
        id=id, name=name, state="CA", party="D",
        score_funding_independence=funding_independence,
        score_promise_persistence=50, score_independent_voting=50,
        score_funding_diversity=50, score_legislative_effectiveness=50,
    )


def _rep(id, name, funding_independence):
    return Representative(
        id=id, name=name, state="CA", district=1, party="D",
        score_funding_independence=funding_independence,
        score_promise_persistence=50, score_independent_voting=50,
        score_funding_diversity=50, score_legislative_effectiveness=50,
    )


def test_senator_leaderboard_ranks_higher_weighted_score_first(db_session):
    db_session.add(_senator("S001", "Low Scorer", funding_independence=10))
    db_session.add(_senator("S002", "High Scorer", funding_independence=90))
    db_session.commit()

    result = get_leaderboard(db_session)

    assert [r.id for r in result] == ["S002", "S001"]


def test_rep_leaderboard_ranks_higher_weighted_score_first(db_session):
    db_session.add(_rep("R001", "Low Scorer", funding_independence=10))
    db_session.add(_rep("R002", "High Scorer", funding_independence=90))
    db_session.commit()

    result = get_rep_leaderboard(db_session)

    assert [r["id"] for r in result["entries"]] == ["R002", "R001"]


# ── Departed members are excluded (see senator_service.get_leaderboard) ──

def test_senator_leaderboard_excludes_departed_members(db_session):
    serving = _senator("S001", "Still Serving", funding_independence=10)
    departed = _senator("S002", "Left Office", funding_independence=90)
    departed.is_current = False
    departed.left_office_date = "2026-06-01"
    db_session.add_all([serving, departed])
    db_session.commit()

    result = get_leaderboard(db_session)

    # Departed ranks first on score alone — exclusion, not sorting, keeps them out.
    assert [r.id for r in result] == ["S001"]


def test_rep_leaderboard_excludes_departed_members(db_session):
    serving = _rep("R001", "Still Serving", funding_independence=10)
    departed = _rep("R002", "Left Office", funding_independence=90)
    departed.is_current = False
    departed.left_office_date = "2026-06-01"
    db_session.add_all([serving, departed])
    db_session.commit()

    result = get_rep_leaderboard(db_session)

    assert [r["id"] for r in result["entries"]] == ["R001"]
    assert result["total"] == 1


def test_president_leaderboard_still_ranks_former_presidents(db_session):
    """The deliberate exception: a president's only meaningful comparison
    is against the historical field, so leaving office is what QUALIFIES
    them for this ranking rather than removing them from it."""
    from app.models import President
    from app.services.president_service import get_president_leaderboard

    db_session.add(President(
        id="obama-44", name="Barack Obama", party="D", number=44,
        term_start="2009-01-20", term_end="2017-01-20", is_current=False,
        score_public_mandate=70.0, score_effectiveness=70.0,
        score_agency_alignment=70.0, score_historical_legacy=70.0,
    ))
    db_session.commit()

    result = get_president_leaderboard(db_session)

    assert [p.id for p in result] == ["obama-44"]
