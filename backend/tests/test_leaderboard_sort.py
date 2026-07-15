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
