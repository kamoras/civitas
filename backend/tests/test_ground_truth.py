"""Tests for the ground-truth regression gate."""

from app.models import Representative, Senator
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


def _add_representative(db, id_, **scores):
    r = Representative(
        id=id_,
        name=f"Rep {id_}",
        state="NY",
        district=1,
        party="D",
        score_funding_independence=scores.get("fi", 50),
        score_promise_persistence=scores.get("pp", 50),
        score_independent_voting=scores.get("iv", 50),
        score_funding_diversity=scores.get("fd", 50),
        score_legislative_effectiveness=scores.get("le", 50),
    )
    db.add(r)
    return r


class TestCheckScoreDistribution:
    def test_healthy_spread_passes(self, db_session):
        # Wide, varied scores across 10 senators — well above the stdev floor.
        pp_values = [20, 30, 40, 45, 50, 55, 60, 65, 75, 85]
        for i, v in enumerate(pp_values):
            _add_senator(db_session, f"s{i}", pp=v, fi=v, iv=v, fd=v, le=v)
        db_session.commit()

        failures = check_score_distribution(db_session)
        assert failures == []

    def test_collapsed_dimension_flagged(self, db_session):
        # All FI scores compressed into a narrow band (the same shrinkage-
        # prior failure mode that once hit Promise Persistence — now removed
        # as a scored dimension, see ground_truth.py's MIN_STDEV docstring)
        # while every other dimension stays healthy.
        for i in range(15):
            _add_senator(
                db_session, f"s{i}",
                fi=52 + (i % 3),  # 52-54, stdev ~1
                pp=20 + i * 4, iv=20 + i * 4, fd=20 + i * 4, le=20 + i * 4,
            )
        db_session.commit()

        failures = check_score_distribution(db_session)
        dims_flagged = {f["dimension"] for f in failures}
        assert "FI" in dims_flagged
        assert "IV" not in dims_flagged

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


class TestDimensionSpecificFloors:
    """independentVoting (6.5) and fundingDiversity (5.5) got lower floors
    than fundingIndependence/legislativeEffectiveness (8.0 each) after a
    2026-07 audit found both structurally can't reach 8.0 given real
    lobbying-match/industry-classification data sparsity — see
    ground_truth.py's MIN_STDEV docstring for the full evidence trail.
    """

    def test_iv_between_6_5_and_8_now_passes(self, db_session):
        # stdev ~7.3 — would have failed the old uniform 8.0 floor, and
        # still correctly fails for FI/LE (unchanged at 8.0) in the same
        # population, but now passes for IV specifically.
        iv_values = [38, 42, 45, 47, 49, 51, 53, 55, 58, 64]
        for i, v in enumerate(iv_values):
            _add_senator(db_session, f"s{i}", iv=v, fi=v, le=v, fd=50, pp=50)
        db_session.commit()

        failures = check_score_distribution(db_session)
        dims_flagged = {f["dimension"] for f in failures}
        assert "IV" not in dims_flagged
        assert "FI" in dims_flagged
        assert "LE" in dims_flagged

    def test_fd_between_5_5_and_8_now_passes(self, db_session):
        # stdev ~6.7 — between the new 5.5 floor and FI/LE's unchanged 8.0.
        fd_values = [40, 44, 47, 49, 50, 51, 53, 55, 58, 65]
        for i, v in enumerate(fd_values):
            _add_senator(db_session, f"s{i}", fd=v, fi=v, le=v, iv=50, pp=50)
        db_session.commit()

        failures = check_score_distribution(db_session)
        dims_flagged = {f["dimension"] for f in failures}
        assert "FD" not in dims_flagged
        assert "FI" in dims_flagged
        assert "LE" in dims_flagged

    def test_iv_still_flags_a_genuine_collapse_below_6_5(self, db_session):
        for i in range(15):
            _add_senator(db_session, f"s{i}", iv=52 + (i % 3))  # stdev ~1
        db_session.commit()

        failures = check_score_distribution(db_session)
        dims_flagged = {f["dimension"] for f in failures}
        assert "IV" in dims_flagged

    def test_fd_still_flags_a_genuine_collapse_below_5_5(self, db_session):
        for i in range(15):
            _add_senator(db_session, f"s{i}", fd=52 + (i % 3))  # stdev ~1
        db_session.commit()

        failures = check_score_distribution(db_session)
        dims_flagged = {f["dimension"] for f in failures}
        assert "FD" in dims_flagged


class TestCheckScoreDistributionHouse:
    def test_model_param_scopes_to_representatives(self, db_session):
        # House has no named ground-truth cases; check_score_distribution
        # is its only regression gate, run against Representative rows via
        # the model= param instead of the Senator default.
        for i in range(15):
            _add_representative(
                db_session, f"r{i}",
                fi=52 + (i % 3),  # collapsed, stdev ~1
                pp=20 + i * 4, iv=20 + i * 4, fd=20 + i * 4, le=20 + i * 4,
            )
        db_session.commit()

        failures = check_score_distribution(db_session, model=Representative)
        dims_flagged = {f["dimension"] for f in failures}
        assert "FI" in dims_flagged
        assert all("representatives" in f["senator"] for f in failures)

    def test_house_and_senate_rows_dont_cross_contaminate(self, db_session):
        # A Senator population that would fail on its own must not affect
        # the House check when scoped to Representative, and vice versa.
        for i in range(15):
            _add_senator(db_session, f"s{i}", pp=52 + (i % 3))
            _add_representative(
                db_session, f"r{i}",
                pp=20 + i * 4, fi=20 + i * 4, iv=20 + i * 4,
                fd=20 + i * 4, le=20 + i * 4,
            )
        db_session.commit()

        assert check_score_distribution(db_session, model=Representative) == []
