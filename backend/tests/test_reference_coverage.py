"""A population reference is re-measured only on enough of the chamber.

On 2026-10-15 the House sponsorship read stopped after 105 of 433 members
(the first 105 in the roster), and the Legislative Effectiveness reference
was measured on those alone: 61 members rose 5 to 11 points and none fell
more than 1, 133 of the 149 who rose Republicans. Under the calibrated
coverage floor (app/data/reference_coverage.json) a run carries the last
reference measured on full coverage instead, and says so.
"""

import json

import pytest

from app.models import President
from app.pipeline import live_references
from app.pipeline.analyze.population_reference import (
    CONSTITUENT_REFERENCE,
    FUNDING_REFERENCE,
    LES_REFERENCE,
    reference_coverage_floor,
)
from app.pipeline.analyze.score_calculator import _calc_legislative_effectiveness

FLOOR = 0.9


def _bills(n, law=False):
    return [
        {"billType": "hr", "congress": 119, "isLaw": law and i == 0,
         "stage": "ENACTED" if law and i == 0 else "REFERRED"}
        for i in range(n)
    ]


@pytest.fixture()
def floor(monkeypatch):
    monkeypatch.setattr(live_references, "reference_coverage_floor", lambda name, chamber: FLOOR)
    alerts = []
    monkeypatch.setattr(live_references, "send_ops_alert", lambda *a, **k: alerts.append((a, k)))
    monkeypatch.setattr(live_references, "resolve_ops_alert", lambda *a, **k: None)
    return alerts


def _president(db):
    db.add(President(id="p", name="P", party="R", number=47, term_start="2025-01-20", is_current=True))
    db.commit()


def _house(read: int, roster: int = 433):
    """`read` members whose bills were read this run, the rest carried from
    an earlier run's stored bills — the first `read` in roster order, as a
    stopped read leaves them, and skewed the way 10-15's were."""
    return [
        (_bills(1 + n % 9, law=n % 11 == 0), "R" if n % 2 else "D", "read" if n < read else "carried")
        for n in range(roster)
    ]


class TestLegislativeEffectiveness:
    def test_105_of_433_read_carries_the_last_full_reference(self, db_session, pinned_les_reference, floor):
        _president(db_session)
        good = {**pinned_les_reference["house"], "coverage": 1.0, "roster": 433}
        LES_REFERENCE.write("house", good)
        before = json.loads(LES_REFERENCE.live_path.read_text())["house"]

        ref = live_references.live_les_reference("house", _house(105), db_session)

        assert ref["house"] == before  # carried as stored, not re-measured
        assert json.loads(LES_REFERENCE.live_path.read_text())["house"] == before  # and not rewritten
        # So every member scores exactly as against the last good reference.
        for bills, party, _ in _house(105)[:40]:
            assert _calc_legislative_effectiveness(
                bills, 0.5, party=party, years_in_office=4, les_reference=ref, chamber="house",
            ) == _calc_legislative_effectiveness(
                bills, 0.5, party=party, years_in_office=4, les_reference={"house": before}, chamber="house",
            )
        (subject, body), kwargs = floor[0]
        assert "105 of 433" in subject and before["computed_at"] in body  # from when
        assert kwargs["condition"] == "reference-coverage-les_reference-house"

    def test_full_coverage_is_re_measured_as_before(self, db_session, pinned_les_reference, floor):
        _president(db_session)
        ref = live_references.live_les_reference("house", _house(433), db_session)["house"]
        assert ref["n_members"] == 433 and ref["coverage"] == 1.0 and ref["roster"] == 433
        assert json.loads(LES_REFERENCE.live_path.read_text())["house"]["coverage"] == 1.0
        assert floor == []

    def test_a_partial_stored_reference_is_not_carried(self, db_session, pinned_les_reference, floor):
        """10-15's own reference (105 members, written before coverage was
        recorded) is no good to carry: with nothing better on file, the run
        measures on every member as scored — the bills read and the stored
        bills carried — and records that it did."""
        _president(db_session)
        LES_REFERENCE.write("house", {**pinned_les_reference["house"], "n": 104, "n_members": 105})
        ref = live_references.live_les_reference("house", _house(105), db_session)["house"]
        assert ref["n_members"] == 433 and ref["basis"] == "stored bills for 328 members"
        assert "measured on the 433 members as scored" in floor[0][0][1]

    def test_unavailable_members_stay_out_of_the_as_scored_measure(self, db_session, pinned_les_reference, floor):
        _president(db_session)
        LES_REFERENCE.write("house", {**pinned_les_reference["house"], "n_members": 105})
        members = [(b, p, "unavailable" if s == "carried" and i % 2 else s) for i, (b, p, s) in enumerate(_house(105))]
        ref = live_references.live_les_reference("house", members, db_session)["house"]
        assert ref["n_members"] == sum(1 for *_, s in members if s != "unavailable")

    def test_a_single_member_run_says_nothing(self, db_session, pinned_les_reference, floor):
        _president(db_session)
        LES_REFERENCE.write("senate", {**pinned_les_reference["senate"], "coverage": 1.0})
        live_references.live_les_reference("senate", [(_bills(3), "R", "read")], db_session, roster=100, quiet=True)
        assert floor == []


class TestFundingAndConstituent:
    def test_funding_carried_below_the_floor(self, pinned_funding_reference, floor):
        FUNDING_REFERENCE.write("senate", {**pinned_funding_reference["senate"], "coverage": 1.0})
        stored = json.loads(FUNDING_REFERENCE.live_path.read_text())["senate"]
        fundings = [{"totalRaised": 1e6, "totalContributions": 1e6, "totalFromPACs": 2e5}] * 40
        ref = live_references.live_funding_reference("senate", fundings, roster=100)
        assert ref["senate"] == stored
        assert "40 of 100" in floor[0][0][0]

    def test_funding_measured_at_full_coverage(self, pinned_funding_reference, floor):
        fundings = [{"totalRaised": 1e6, "totalContributions": 1e6, "totalFromPACs": 2e5 + i} for i in range(100)]
        ref = live_references.live_funding_reference("senate", fundings, roster=100)["senate"]
        assert ref["coverage"] == 1.0 and floor == []

    def test_constituent_carried_below_the_floor_is_not_measured(self, pinned_population_references, floor):
        CONSTITUENT_REFERENCE.write("house", {**CONSTITUENT_REFERENCE.load()["house"], "coverage": 1.0})
        stored = json.loads(CONSTITUENT_REFERENCE.live_path.read_text())["house"]
        merged, measured = live_references.live_constituent_reference_measured(
            "house", [{"state": "TX", "party": "R", "votingRecord": {}}] * 50, roster=433,
        )
        assert merged["house"] == stored and measured is False
        assert "50 of 433" in floor[0][0][0]


def test_the_calibrated_floors_are_on_file():
    """Generated by scripts/calibrate_reference_coverage.py (AGENTS.md §3a):
    every member reference has a floor for both chambers, and 10-15's read
    (105 of 433 House members) falls under the House LES one."""
    for chamber in ("senate", "house"):
        for name in ("les_reference", "funding_reference", "constituent_reference"):
            assert 0.0 < reference_coverage_floor(name, chamber) <= 1.0
    assert 105 / 433 < reference_coverage_floor("les_reference", "house")
