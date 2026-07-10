"""Tests for the ground-truth regression gate."""

from app.models import Senator
from app.pipeline.analyze.ground_truth import check_score_distribution


def _add_senator(db, id_, **scores):
    s = Senator(
        id=id_,
        name=f"Senator {id_}",
        state="NY",
        party="D",
        score_funding_independence=scores.get("fi", 50),
        score_promise_persistence=scores.get("pp", 50),
        score_independent_voting=scores.get("iv", 50),
        score_funding_diversity=scores.get("fd", 50),
        score_legislative_effectiveness=scores.get("le", 50),
    )
    db.add(s)
    return s


class TestCheckScoreDistribution:
    def test_healthy_spread_passes(self, db_session):
        # Wide, varied scores across 10 senators — well above the stdev floor.
        pp_values = [20, 30, 40, 45, 50, 55, 60, 65, 75, 85]
        for i, v in enumerate(pp_values):
            _add_senator(db_session, f"s{i}", pp=v, fi=v, iv=v, fd=v, le=v)
        db_session.commit()

        failures = check_score_distribution(db_session)
        assert failures == []

    def test_collapsed_promise_persistence_flagged(self, db_session):
        # All PP scores compressed into a narrow band (the v5.1 shrinkage-
        # prior failure mode) while every other dimension stays healthy.
        for i in range(15):
            _add_senator(
                db_session, f"s{i}",
                pp=52 + (i % 3),  # 52-54, stdev ~1
                fi=20 + i * 4, iv=20 + i * 4, fd=20 + i * 4, le=20 + i * 4,
            )
        db_session.commit()

        failures = check_score_distribution(db_session)
        dims_flagged = {f["dimension"] for f in failures}
        assert "PP" in dims_flagged
        assert "FI" not in dims_flagged

    def test_too_few_senators_skipped(self, db_session):
        # Below the n=10 minimum — not enough data to judge population spread.
        for i in range(5):
            _add_senator(db_session, f"s{i}", pp=50, fi=50, iv=50, fd=50, le=50)
        db_session.commit()

        assert check_score_distribution(db_session) == []

    def test_null_scores_excluded_from_stdev(self, db_session):
        pp_values = [20, 30, 40, 45, 50, 55, 60, 65, 75, 85]
        for i, v in enumerate(pp_values):
            _add_senator(db_session, f"s{i}", pp=v, fi=v, iv=v, fd=v, le=v)
        # A senator with no scores yet shouldn't count toward the population.
        s = Senator(id="new", name="New Senator", state="CA", party="I")
        s.score_funding_independence = None
        s.score_promise_persistence = None
        s.score_independent_voting = None
        s.score_funding_diversity = None
        s.score_legislative_effectiveness = None
        db_session.add(s)
        db_session.commit()

        failures = check_score_distribution(db_session)
        assert failures == []
