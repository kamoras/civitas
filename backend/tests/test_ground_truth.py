"""Tests for the derived consistency gate.

No test here names a real politician or asserts a hand-typed score range —
every expectation the gate checks is derived from the test population's own
raw data, mirroring how the gate works in production (AGENTS.md 1/3a).
"""

from app.models import KeyVote, RepKeyVote, Representative, ScoreSnapshot, Senator
from app.pipeline.analyze.ground_truth import (
    check_ground_truth,
    check_score_distribution,
)
from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION


def _add_senator(db, id_, *, fi=50.0, iv=50.0, fd=50.0, le=50.0,
                 total_raised=0.0, total_from_pacs=0.0, small_donor_pct=0.0):
    s = Senator(
        id=id_,
        name=f"Senator {id_}",
        state="NY",
        party="D",
        score_funding_independence=fi,
        score_independent_voting=iv,
        score_funding_diversity=fd,
        score_legislative_effectiveness=le,
        total_raised=total_raised,
        total_from_pacs=total_from_pacs,
        small_donor_percentage=small_donor_pct,
    )
    db.add(s)
    return s


def _add_representative(db, id_, *, fi=50.0, iv=50.0, fd=50.0, le=50.0,
                        total_raised=0.0, total_from_pacs=0.0,
                        small_donor_pct=0.0):
    r = Representative(
        id=id_,
        name=f"Rep {id_}",
        state="NY",
        district=1,
        party="D",
        score_funding_independence=fi,
        score_independent_voting=iv,
        score_funding_diversity=fd,
        score_legislative_effectiveness=le,
        total_raised=total_raised,
        total_from_pacs=total_from_pacs,
        small_donor_percentage=small_donor_pct,
    )
    db.add(r)
    return r


def _add_votes(db, senator_id, breaks, total):
    """Give a senator `total` party-labeled votes, `breaks` of them crossing."""
    for j in range(total):
        db.add(KeyVote(
            senator_id=senator_id,
            bill_name=f"Bill {j}",
            bill_id=f"bill-{j}",
            date="2026-01-01",
            vote="Yea",
            voted_with_party=j >= breaks,
        ))


def _healthy_population(db, n=40, votes_per_member=50):
    """A population whose scores rank-track their raw data by construction:
    FI falls as PAC share rises and rises with small-donor share; IV rises
    with the observed break rate."""
    for i in range(n):
        s = _add_senator(
            db, f"s{i}",
            fi=95 - 1.5 * i,
            iv=25 + 1.5 * i,
            total_raised=1_000_000,
            total_from_pacs=1_000_000 * i / 50,
            small_donor_pct=40 - 0.8 * i,
        )
        _add_votes(db, s.id, breaks=i, total=votes_per_member)
    db.commit()


class TestDerivedConsistency:
    def test_consistent_population_passes(self, db_session):
        _healthy_population(db_session)
        report = check_ground_truth(db_session)
        assert report["failures"] == []
        # Integrity probes + 3 correlations + 3x2 decile tests all ran.
        assert report["checked"] >= 10

    def test_inverted_fi_flagged(self, db_session):
        # Algorithm-regression simulation: FI now RISES with PAC share.
        for i in range(40):
            s = _add_senator(
                db_session, f"s{i}",
                fi=20 + 1.5 * i,
                iv=25 + 1.5 * i,
                total_raised=1_000_000,
                total_from_pacs=1_000_000 * i / 50,
                small_donor_pct=40 - 0.8 * i,
            )
            _add_votes(db_session, s.id, breaks=i, total=50)
        db_session.commit()

        failures = check_ground_truth(db_session)["failures"]
        assert any(
            f["dimension"] == "FI" and "PAC share" in f["rationale"]
            for f in failures
        )
        assert not any(f["dimension"] == "IV" for f in failures)

    def test_top_crossers_scored_low_flagged(self, db_session):
        # The old gate's core purpose, derived: whoever currently crosses
        # party most must not land at the bottom of IV. Scores track break
        # rate for everyone except the five most frequent crossers.
        for i in range(40):
            s = _add_senator(
                db_session, f"s{i}",
                iv=10 if i >= 35 else 30 + i,
                fi=95 - 1.5 * i,
                total_raised=1_000_000,
                total_from_pacs=1_000_000 * i / 50,
                small_donor_pct=40 - 0.8 * i,
            )
            _add_votes(db_session, s.id, breaks=i, total=50)
        db_session.commit()

        failures = check_ground_truth(db_session)["failures"]
        assert any(
            f["dimension"] == "IV" and "most-independent decile" in f["senator"]
            for f in failures
        )

    def test_pac_totals_all_zero_flagged(self, db_session):
        # The historical silent-fetch regression: everyone funded, nobody
        # with a cent of PAC money on record.
        for i in range(12):
            s = _add_senator(
                db_session, f"s{i}",
                fi=40 + 3 * i, iv=40 + 3 * i,
                total_raised=500_000, total_from_pacs=0,
                small_donor_pct=10 + i,
            )
            _add_votes(db_session, s.id, breaks=i, total=12)
        db_session.commit()

        failures = check_ground_truth(db_session)["failures"]
        assert any("PAC totals are zero" in f["rationale"] for f in failures)

    def test_no_labeled_votes_flagged(self, db_session):
        for i in range(12):
            _add_senator(
                db_session, f"s{i}",
                fi=40 + 3 * i, iv=40 + 3 * i,
                total_raised=500_000, total_from_pacs=50_000 * (i + 1),
                small_donor_pct=10 + i,
            )
        db_session.commit()

        failures = check_ground_truth(db_session)["failures"]
        assert any("party-labeled vote" in f["rationale"] for f in failures)

    def test_small_population_skipped(self, db_session):
        # Below the n=10 minimum the gate stays silent rather than running
        # statistics on noise (a fresh install mid-first-fetch).
        for i in range(5):
            _add_senator(db_session, f"s{i}")
        db_session.commit()

        report = check_ground_truth(db_session)
        assert report == {"checked": 0, "failures": []}

    def test_membership_churn_needs_no_maintenance(self, db_session):
        # The point of deriving expectations: an entirely different roster
        # passes the same gate with zero edits to the checker. Same
        # construction as the healthy population, different people.
        for i in range(40):
            s = Senator(
                id=f"new{i}", name=f"Freshman {i}", state="OH", party="R",
                score_funding_independence=95 - 1.5 * i,
                score_independent_voting=25 + 1.5 * i,
                total_raised=2_000_000,
                total_from_pacs=2_000_000 * i / 50,
                small_donor_percentage=40 - 0.8 * i,
            )
            db_session.add(s)
            _add_votes(db_session, s.id, breaks=i, total=50)
        db_session.commit()

        assert check_ground_truth(db_session)["failures"] == []

    def test_house_gets_the_same_gate(self, db_session):
        # The named-reference table was Senate-only; the derived gate is
        # chamber-agnostic. A House-wide PAC blackout must be flagged.
        for i in range(12):
            r = _add_representative(
                db_session, f"r{i}",
                fi=40 + 3 * i, iv=40 + 3 * i,
                total_raised=500_000, total_from_pacs=0,
                small_donor_pct=10 + i,
            )
            for j in range(12):
                db_session.add(RepKeyVote(
                    representative_id=r.id,
                    bill_name=f"Bill {j}", bill_id=f"bill-{j}",
                    date="2026-01-01", vote="Yea",
                    voted_with_party=j >= (i % 5),
                ))
        db_session.commit()

        failures = check_ground_truth(db_session, model=Representative)["failures"]
        assert any(
            "PAC totals are zero" in f["rationale"]
            and "representatives" in f["senator"]
            for f in failures
        )


def _add_snapshot_history(db, dates, values_fn, version=ALGORITHM_VERSION):
    """Write senator score_1 (FI) snapshot history for the given dates."""
    for d, date in enumerate(dates):
        for j, v in enumerate(values_fn(d)):
            db.add(ScoreSnapshot(
                entity_type="senator",
                entity_id=f"s{j}",
                date=date,
                overall_score=v,
                score_1=v,
                algorithm_version=version,
            ))


class TestCheckScoreDistribution:
    def test_point_mass_collapse_flagged(self, db_session):
        # A strict majority sharing one value is a collapse by definition —
        # the failure mode that hit Promise Persistence (76% at the neutral
        # prior) before it was removed as a scored dimension.
        for i in range(15):
            _add_senator(db_session, f"s{i}", fi=52.0, iv=20 + 4 * i,
                         fd=20 + 4 * i, le=20 + 4 * i)
        db_session.commit()

        failures = check_score_distribution(db_session)
        dims_flagged = {f["dimension"] for f in failures}
        assert "FI" in dims_flagged
        assert "IV" not in dims_flagged
        assert any("point mass" in f["rationale"] for f in failures)

    def test_narrow_band_without_history_passes(self, db_session):
        # Behavior change vs the old hand-calibrated stdev floors: a narrow
        # but non-degenerate spread is NOT flagged when there is no snapshot
        # history to compare against — narrowness alone is not evidence of
        # regression (the old fixed floors false-alarmed for two+ weeks on
        # exactly this, per the 2026-07 audit that lowered them).
        for i in range(15):
            _add_senator(db_session, f"s{i}", fi=48 + (i % 5) * 0.7,
                         iv=20 + 4 * i, fd=20 + 4 * i, le=20 + 4 * i)
        db_session.commit()

        assert check_score_distribution(db_session) == []

    def test_sudden_collapse_vs_own_history_flagged(self, db_session):
        # Live FI compressed to stdev ~1 while this algorithm version's own
        # snapshot history sits near stdev ~21 — an extreme low outlier by
        # modified z-score, flagged with a floor derived from that history.
        for i in range(15):
            _add_senator(db_session, f"s{i}", fi=48 + (i % 5) * 0.7,
                         iv=20 + 4 * i, fd=20 + 4 * i, le=20 + 4 * i)
        dates = [f"2026-07-0{d}" for d in range(1, 7)]
        _add_snapshot_history(
            db_session, dates,
            lambda d: [(20 + 6 * j) * (1 + 0.01 * d) for j in range(12)],
        )
        db_session.commit()

        failures = check_score_distribution(db_session)
        fi = [f for f in failures if f["dimension"] == "FI"]
        assert len(fi) == 1
        assert "history" in fi[0]["rationale"]
        # The reported floor is derived from history, not hand-typed.
        assert fi[0]["expected"][0] > 5
        assert {f["dimension"] for f in failures} == {"FI"}

    def test_history_from_other_algorithm_versions_ignored(self, db_session):
        # A deliberate algorithm change legitimately reshapes distributions;
        # only same-version history is evidence of a regression.
        for i in range(15):
            _add_senator(db_session, f"s{i}", fi=48 + (i % 5) * 0.7,
                         iv=20 + 4 * i, fd=20 + 4 * i, le=20 + 4 * i)
        dates = [f"2026-07-0{d}" for d in range(1, 7)]
        _add_snapshot_history(
            db_session, dates,
            lambda d: [(20 + 6 * j) * (1 + 0.01 * d) for j in range(12)],
            version="v0-test",
        )
        db_session.commit()

        assert check_score_distribution(db_session) == []

    def test_too_few_senators_skipped(self, db_session):
        for i in range(5):
            _add_senator(db_session, f"s{i}")
        db_session.commit()

        assert check_score_distribution(db_session) == []

    def test_null_scores_excluded(self, db_session):
        for i, v in enumerate([20, 30, 40, 45, 50, 55, 60, 65, 75, 85]):
            _add_senator(db_session, f"s{i}", fi=v, iv=v, fd=v, le=v)
        s = Senator(id="new", name="New Senator", state="CA", party="I")
        s.score_funding_independence = None
        s.score_independent_voting = None
        s.score_funding_diversity = None
        s.score_legislative_effectiveness = None
        db_session.add(s)
        db_session.commit()

        assert check_score_distribution(db_session) == []

    def test_house_and_senate_rows_dont_cross_contaminate(self, db_session):
        # Point-mass in the House only; healthy Senate. Each chamber's check
        # sees only its own rows.
        for i in range(15):
            _add_senator(db_session, f"s{i}", fi=20 + 4 * i, iv=20 + 4 * i,
                         fd=20 + 4 * i, le=20 + 4 * i)
            _add_representative(db_session, f"r{i}", fi=52.0, iv=20 + 4 * i,
                                fd=20 + 4 * i, le=20 + 4 * i)
        db_session.commit()

        assert check_score_distribution(db_session) == []
        house = check_score_distribution(db_session, model=Representative)
        assert {f["dimension"] for f in house} == {"FI"}
        assert all("representatives" in f["senator"] for f in house)
