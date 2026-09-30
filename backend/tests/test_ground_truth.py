"""Tests for the derived consistency gate.

No test here names a real politician or asserts a hand-typed score range —
every expectation the gate checks is derived from the test population's own
raw data, mirroring how the gate works in production (AGENTS.md 1/3a).
"""

import pytest

from app.models import KeyVote, RepKeyVote, Representative, ScoreSnapshot, Senator
from app.pipeline.analyze.ground_truth import (
    _tie_extended_extreme,
    check_ground_truth,
    check_score_distribution,
    evaluate_derived_checks,
)
from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION, CONSTITUENT_REFERENCE_STATISTIC


def _add_senator(db, id_, *, fi=50.0, iv=50.0, fd=50.0, le=50.0,
                 total_raised=0.0, total_from_pacs=0.0, small_donor_pct=0.0):
    s = Senator(
        id=id_,
        name=f"Senator {id_}",
        state="NY",
        party="D",
        score_funding_independence=fi,
        score_constituent_alignment=iv,
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
        score_constituent_alignment=iv,
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


def _vote_iv(breaks, total=200, state="NY", party="D"):
    """The Constituent Alignment a correct pipeline would store for a member
    of this seat — by default a NY Democrat (conftest: ~6.7% expected, so the
    vote score peaks near 13 of 200 breaks): the vote component recomputed
    from these votes, which is what the gate compares stored scores with."""
    from app.pipeline.analyze.ground_truth import constituent_metrics

    return constituent_metrics(breaks / total, total, state, party)["seat_relative_vote"]


def _healthy_population(db, n=40, votes_per_member=200, fi_of=None, iv_of=None):
    """A population whose scores rank-track their raw data by construction:
    FI falls as PAC share rises and rises with small-donor share; IV is the
    vote score the stored votes give (0-19.5% breaks against a ~6.7%
    expectation: rising to the peak, then falling). Member i breaks i times;
    fi_of/iv_of(i) replace a score to simulate a regression."""
    for i in range(n):
        s = _add_senator(
            db, f"s{i}",
            fi=fi_of(i) if fi_of else 95 - 1.5 * i,
            iv=iv_of(i) if iv_of else _vote_iv(i, votes_per_member),
            total_raised=1_000_000,
            total_from_pacs=1_000_000 * i / 50,
            small_donor_pct=40 - 0.8 * i,
        )
        _add_votes(db, s.id, breaks=i, total=votes_per_member)
    db.commit()


def _saturated_reference():
    """A constituent reference whose saturation is shrunk to a sliver, so
    nearly every member reads as past it."""
    return {c: {"expected": {"D": {"a": 0.0, "b": 0.0}}, "deviation_p90": 0.001,
                "statistic": CONSTITUENT_REFERENCE_STATISTIC}
            for c in ("senate", "house")}


class TestDerivedConsistency:
    def test_consistent_population_passes(self, db_session):
        _healthy_population(db_session)
        report = check_ground_truth(db_session)
        assert report["failures"] == []
        # Integrity probes + 3 correlations + 3x2 decile tests all ran.
        assert report["checked"] >= 10

    def test_inverted_fi_flagged(self, db_session):
        # Algorithm-regression simulation: FI now RISES with PAC share.
        _healthy_population(db_session, fi_of=lambda i: 20 + 1.5 * i)

        failures = check_ground_truth(db_session)["failures"]
        assert any(
            f["dimension"] == "FI" and "PAC share" in f["rationale"]
            for f in failures
        )
        assert not any(f["dimension"] == "IV" for f in failures)

    def test_members_at_their_seats_norm_scored_low_flagged(self, db_session):
        # v6.16: whoever breaks about as often as their seat's same-party
        # members must not land at the bottom of IV. Scores follow the votes
        # for everyone except the six members nearest the expectation
        # (11-16 breaks of 200, around the ~6.7% norm).
        _healthy_population(db_session, iv_of=lambda i: 5 if 11 <= i <= 16 else _vote_iv(i))

        failures = check_ground_truth(db_session)["failures"]
        assert any(
            f["dimension"] == "IV" and "highest-expected decile" in f["senator"]
            for f in failures
        )

    @staticmethod
    def _peaked_population(db, iv_of):
        """60 members from never breaking to far past the norm (0-49 breaks
        of 200 plus ten at 70-160), stored IV given by iv_of(breaks)."""
        for i in range(60):
            breaks = i if i < 50 else 70 + 10 * (i - 50)
            s = _add_senator(
                db, f"s{i}",
                iv=iv_of(breaks),
                fi=95 - 1.5 * i,
                total_raised=1_000_000,
                total_from_pacs=1_000_000 * i / 60,
                small_donor_pct=40 - 0.6 * i,
            )
            _add_votes(db, s.id, breaks=breaks, total=200)
        db.commit()

    def test_peaked_scores_pass(self, db_session):
        # v6.16: highest at the seat's norm, falling both ways — the design.
        self._peaked_population(db_session, iv_of=_vote_iv)
        report = check_ground_truth(db_session)
        assert report["failures"] == []

    def test_more_breaking_always_scoring_higher_flagged(self, db_session):
        # A regression to "more breaking scores higher" puts the chamber's
        # heaviest breakers at the top of IV while their vote score
        # recomputed from the votes puts them at the bottom.
        self._peaked_population(db_session, iv_of=lambda b: min(100, 20 + b))
        failures = check_ground_truth(db_session)["failures"]
        assert any(
            f["dimension"] == "IV" and "lowest-expected decile" in f["senator"]
            for f in failures
        )

    def test_senate_sized_chamber_catches_heavy_breakers_scored_high(self, db_session):
        # A real Senate has only a handful of very heavy breakers. The
        # recomputed vote score keeps them in the whole-chamber check: 94
        # scored by their votes, 6 far past the norm wrongly scored 100.
        for i in range(100):
            ordinary = i < 94
            breaks = i // 5 if ordinary else 120 + 10 * (i - 94)
            s = _add_senator(
                db_session, f"s{i}",
                iv=_vote_iv(breaks) if ordinary else 100,
                fi=95 - 0.9 * i,
                total_raised=1_000_000,
                total_from_pacs=1_000_000 * i / 100,
                small_donor_pct=40 - 0.4 * i,
            )
            _add_votes(db_session, s.id, breaks=breaks, total=200)
        db_session.commit()

        failures = check_ground_truth(db_session)["failures"]
        assert any(
            f["dimension"] == "IV" and "lowest-expected decile" in f["senator"]
            for f in failures
        )

    def test_probe_waits_for_enough_readable_members(self, db_session):
        # Early in a congress: 8 members with enough votes, 2 past
        # saturation. 25% of 8 is noise, not a broken reference.
        for i in range(40):
            s = _add_senator(db_session, f"s{i}", iv=50, total_raised=1_000_000,
                             total_from_pacs=1_000_000 * i / 50, small_donor_pct=40 - 0.8 * i)
            if i < 8:
                _add_votes(db_session, s.id, breaks=100 if i < 2 else i, total=200)
        db_session.commit()
        failures = check_ground_truth(db_session, reference_measured=True)["failures"]
        assert not any("reference and the votes disagree" in f["rationale"] for f in failures)

    def test_probe_is_not_counted_when_too_few_members_are_readable(self):
        members = [
            {"name": f"m{i}", "scores": {}, "raw": {"labeled_votes": 50},
             "metrics": {"seat_relative_vote": None, "beyond_saturation": True if i < 3 else None}}
            for i in range(40)
        ]
        measured = evaluate_derived_checks(members, reference_measured=True)
        unmeasured = evaluate_derived_checks(members, reference_measured=False)
        assert measured["checked"] == unmeasured["checked"]
        assert not any("reference and the votes disagree" in f["rationale"] for f in measured["failures"])

    def test_beyond_saturation_counts_both_sides(self):
        # The reference's quantile is taken over |deviation|, so the probe
        # counts members that far out on the loyal side too. Conftest: 10%
        # expected in a swing seat (NY is not; use the helper directly).
        from app.pipeline.analyze.ground_truth import constituent_metrics

        loyal = constituent_metrics(0.0, 50, "SW", "D", reference={"senate": {
            "expected": {"D": {"a": 0.30, "b": 0.0}}, "deviation_p90": 0.2, "n": 100,
            "statistic": CONSTITUENT_REFERENCE_STATISTIC}})
        assert loyal["beyond_saturation"] is True

    def test_probe_allows_exactly_twice_the_tail(self):
        # 2 * (1 - 0.9) is 0.19999999999999996 in floating point; a share of
        # exactly 20% is inside the documented tolerance.
        members = [
            {"name": f"m{i}", "scores": {}, "raw": {"labeled_votes": 50},
             "metrics": {"seat_relative_vote": 50.0, "beyond_saturation": i < 20}}
            for i in range(100)
        ]
        failures = evaluate_derived_checks(members, reference_measured=True)["failures"]
        assert not any("reference and the votes disagree" in f["rationale"] for f in failures)

    def test_break_rate_reads_each_stored_row_as_its_own_roll_call(self, db_session):
        # A Senate key bill's cloture and passage votes share a bill_id. They
        # are two roll calls and both count; bill_id alone would merge them.
        from app.pipeline.analyze.ground_truth import _member_records

        s = _add_senator(db_session, "s0")
        for j in range(20):
            db_session.add(KeyVote(
                senator_id=s.id, bill_name="Same Bill", bill_id="S.1", date="2026-01-01",
                vote="Yea", voted_with_party=j >= 5,
            ))
        db_session.commit()
        record = _member_records(db_session, Senator)[0]
        assert record["raw"]["labeled_votes"] == 20

    def test_gate_judges_members_on_the_reference_the_run_scored_with(self, db_session):
        # The persisted reference is healthy; the run's own reference (passed
        # in) puts nearly everyone past saturation, and the gate must read
        # the one it is given.
        self._peaked_population(db_session, iv_of=_vote_iv)
        assert check_ground_truth(db_session)["failures"] == []
        failures = check_ground_truth(
            db_session, constituent_reference=_saturated_reference(), reference_measured=True,
        )["failures"]
        assert any("reference and the votes disagree" in f["rationale"] for f in failures)

    def test_reference_that_puts_most_members_past_saturation_flagged(self, db_session, monkeypatch):
        # A broken reference (saturation shrunk to a sliver) would push most
        # members out of the rising-side check; the probe says so rather
        # than letting that check fall under its minimum and skip quietly.
        from app.pipeline.analyze import population_reference

        self._peaked_population(db_session, iv_of=_vote_iv)
        monkeypatch.setattr(population_reference.CONSTITUENT_REFERENCE, "load", _saturated_reference)
        failures = check_ground_truth(db_session, reference_measured=True)["failures"]
        probe = [f for f in failures if "reference and the votes disagree" in f["rationale"]]
        assert len(probe) == 1 and probe[0]["dimension"] == "IV"
        # The record carries the share found and the threshold, not a bare 0.
        assert probe[0]["score"] > 0.2 and "20%" in probe[0]["expected"][0]
        # Labelled with the members it counted, not the whole chamber.
        assert "full-confidence senators" in probe[0]["senator"]

    def test_probe_skipped_when_the_reference_was_not_measured_this_run(self, db_session, monkeypatch):
        # A fallback reference (too few members to measure one this run)
        # promises nothing about these members' spread, so the share probe
        # stays out of it.
        from app.pipeline.analyze import population_reference

        self._peaked_population(db_session, iv_of=_vote_iv)
        monkeypatch.setattr(population_reference.CONSTITUENT_REFERENCE, "load", _saturated_reference)
        failures = check_ground_truth(db_session)["failures"]
        assert not any("reference and the votes disagree" in f["rationale"] for f in failures)

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
                score_constituent_alignment=_vote_iv(i, state="OH", party="R"),
                total_raised=2_000_000,
                total_from_pacs=2_000_000 * i / 50,
                small_donor_percentage=40 - 0.8 * i,
            )
            db_session.add(s)
            _add_votes(db_session, s.id, breaks=i, total=200)
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


class TestTieExtendedExtreme:
    """2026-08-26 audit: the Senate Independence check was failing
    (p=0.237, needs <0.05) because party_break_rate has a hard floor at
    0.0 shared by 16+ of 100 senators, and a plain ordered[:k]/ordered[-k:]
    slice split that tie arbitrarily — whichever k of the tied members
    happened to sort first landed in the extreme group, the rest leaked
    into the comparison group. _tie_extended_extreme must put every
    member tied at the boundary value on the same side."""

    def test_no_tie_at_boundary_behaves_like_a_plain_slice(self):
        ordered = [(float(i), 0.0, f"m{i}") for i in range(20)]
        group, rest = _tie_extended_extreme(ordered, k=5, from_start=True)
        assert [nm for _, _, nm in group] == [f"m{i}" for i in range(5)]
        assert len(rest) == 15

    def test_tie_straddling_the_start_boundary_is_extended(self):
        # Six members tied at 0.0 — a plain [:5] slice would arbitrarily
        # leave one of them in "rest".
        ordered = [(0.0, 0.0, f"tied{i}") for i in range(6)] + [
            (float(i), 0.0, f"m{i}") for i in range(1, 15)
        ]
        group, rest = _tie_extended_extreme(ordered, k=5, from_start=True)
        assert {nm for _, _, nm in group} == {f"tied{i}" for i in range(6)}
        assert all(nm.startswith("m") for _, _, nm in rest)

    def test_tie_straddling_the_end_boundary_is_extended(self):
        ordered = [(float(i), 0.0, f"m{i}") for i in range(14)] + [
            (99.0, 0.0, f"tied{i}") for i in range(6)
        ]
        group, rest = _tie_extended_extreme(ordered, k=5, from_start=False)
        assert {nm for _, _, nm in group} == {f"tied{i}" for i in range(6)}
        assert all(nm.startswith("m") for _, _, nm in rest)

    def test_tie_entirely_inside_the_group_needs_no_extension(self):
        # Ties that don't touch the cut point are harmless either way.
        ordered = [(1.0, 0.0, "a"), (1.0, 0.0, "b"), (2.0, 0.0, "c"),
                   (3.0, 0.0, "d"), (4.0, 0.0, "e")]
        group, rest = _tie_extended_extreme(ordered, k=2, from_start=True)
        assert {nm for _, _, nm in group} == {"a", "b"}
        assert len(rest) == 3

    def test_least_independent_decile_reports_the_full_tied_group_size(self):
        # End-to-end reproduction of the live shape: 100 senators, 16 tied
        # at the party_break_rate floor (0.0) with IV scores sitting right
        # at the chamber median (matching the real diagnosis — "IV scores
        # for the zero-rate group cluster tightly around the chamber
        # median"), so the other 84 spread evenly around that same median
        # and the check deterministically fails to find separation. A
        # nominal k=10 decile must report all 16 tied members, never an
        # arbitrary 10 of them.
        members = []
        for i in range(16):
            members.append({
                "name": f"tied{i}",
                "scores": {"score_constituent_alignment": 50.0},
                "metrics": {"seat_relative_vote": 0.0, "pac_ratio": 0.3, "small_donor_pct": 20.0},
                "raw": {"total_raised": 1_000_000, "total_from_pacs": 100_000,
                        "labeled_votes": 50},
            })
        for i in range(84):
            members.append({
                "name": f"m{i}",
                "scores": {"score_constituent_alignment": 10.0 + i * (80.0 / 83)},
                "metrics": {"seat_relative_vote": float(i + 1), "pac_ratio": 0.3, "small_donor_pct": 20.0},
                "raw": {"total_raised": 1_000_000, "total_from_pacs": 100_000,
                        "labeled_votes": 50},
            })

        report = evaluate_derived_checks(members)
        least_failures = [
            f for f in report["failures"]
            if f["dimension"] == "IV" and "lowest-expected decile" in f["senator"]
        ]
        assert least_failures, "expected this deliberately-ambiguous population to fail the check"
        assert "(16 of 100" in least_failures[0]["senator"]


def _add_snapshot_history(db, dates, version=ALGORITHM_VERSION):
    """Write senator score_1 (FI) snapshot history for the given dates, with
    a wide spread (stdev ~21) on every date."""
    for d, date in enumerate(dates):
        for j in range(12):
            v = (20 + 6 * j) * (1 + 0.01 * d)
            db.add(ScoreSnapshot(
                entity_type="senator",
                entity_id=f"s{j}",
                date=date,
                overall_score=v,
                score_1=v,
                algorithm_version=version,
            ))


def _narrow_fi_senate(db):
    """15 senators whose FI sits in a narrow (stdev ~1) but non-degenerate
    band; the other dimensions spread widely."""
    for i in range(15):
        _add_senator(db, f"s{i}", fi=48 + (i % 5) * 0.7,
                     iv=20 + 4 * i, fd=20 + 4 * i, le=20 + 4 * i)


class TestCheckScoreDistribution:
    @pytest.fixture(autouse=True)
    def _today(self, monkeypatch):
        # Snapshot history below is dated July 2026, inside the 119th
        # Congress; pin "today" there so the same-Congress history window
        # doesn't depend on when the suite runs.
        from datetime import datetime

        from app import time_utils

        monkeypatch.setattr(time_utils, "utcnow", lambda: datetime(2026, 7, 10, 12, 0))

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
        _narrow_fi_senate(db_session)
        db_session.commit()

        assert check_score_distribution(db_session) == []

    def test_sudden_collapse_vs_own_history_flagged(self, db_session):
        # Live FI compressed to stdev ~1 while this algorithm version's own
        # snapshot history sits near stdev ~21 — an extreme low outlier by
        # modified z-score, flagged with a floor derived from that history.
        _narrow_fi_senate(db_session)
        dates = [f"2026-07-0{d}" for d in range(1, 7)]
        _add_snapshot_history(db_session, dates)
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
        _narrow_fi_senate(db_session)
        dates = [f"2026-07-0{d}" for d in range(1, 7)]
        _add_snapshot_history(db_session, dates, version="v0-test")
        db_session.commit()

        assert check_score_distribution(db_session) == []

    def test_history_from_the_previous_congress_ignored(self, db_session, monkeypatch):
        # A new Congress resets the current-term window: early in it members
        # have few votes and bills, so scores sit near their shrinkage prior
        # by design. The last Congress's wide spread is not the baseline.
        from datetime import datetime

        from app import time_utils

        monkeypatch.setattr(time_utils, "utcnow", lambda: datetime(2027, 1, 20, 12, 0))
        _narrow_fi_senate(db_session)
        dates = [f"2026-12-0{d}" for d in range(1, 7)]
        _add_snapshot_history(db_session, dates)
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
        s.score_constituent_alignment = None
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
