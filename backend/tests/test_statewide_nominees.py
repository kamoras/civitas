"""Persistence and coverage semantics for statewide-executive nominees
(state_candidates._sync_statewide_nominees + elections._statewide_section).

The point of nearly every test here is one distinction: an empty
statewide section on a state's ballot page can mean "this state elects
no executive officers this cycle" or "nobody has taught us this state's
feed yet", and rendering them identically tells a reader in the second
case that there is nothing to research. Same null-is-not-zero discipline
MeasureCoverage already enforces for ballot measures.
"""

import pytest

from app.api.elections import StatewideCoverageStatus, _statewide_section
from app.models import StatewideNominee
from app.pipeline.fetch.state_candidates import _sync_statewide_nominees

CYCLE = 2026
SOURCE = {
    "strategy": "enhanced_voting",
    "source_name": "Rhode Island Board of Elections",
    "statewide_offices": True,
}

# Shaped exactly as the adapters emit them: `last_name` is the shared
# resolver's field for "the name this seat resolved to", which for a
# statewide contest the adapter reduced with clean_display_name.
GOVERNOR_D = {"office": "governor", "district": None, "party": "D",
              "last_name": "Helena Buonanno Foulkes"}
GOVERNOR_R = {"office": "governor", "district": None, "party": "R",
              "last_name": "Aaron C. Guckian"}
TREASURER_D = {"office": "treasurer", "district": None, "party": "D",
               "last_name": "James A. Diossa"}


class TestCoverageStatus:
    def test_a_state_never_synced_is_not_yet_covered(self, db_session):
        _, coverage = _statewide_section(db_session, "RI", CYCLE)
        assert coverage["status"] == StatewideCoverageStatus.NOT_YET_COVERED
        assert coverage["checkedAt"] is None

    def test_a_synced_state_with_nominees_is_covered(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D])
        races, coverage = _statewide_section(db_session, "RI", CYCLE)
        assert coverage["status"] == StatewideCoverageStatus.COVERED
        assert coverage["sourceName"] == "Rhode Island Board of Elections"
        assert len(races) == 1

    def test_a_synced_state_with_no_nominees_is_confirmed_none(self, db_session):
        """The whole reason the marker exists. Zero rows plus a marker is
        a real claim; zero rows without one is an admission."""
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [])
        races, coverage = _statewide_section(db_session, "RI", CYCLE)
        assert races == []
        assert coverage["status"] == StatewideCoverageStatus.CONFIRMED_NONE

    def test_checked_at_carries_an_explicit_utc_offset(self, db_session):
        """An offset-less ISO string is parsed as LOCAL time by JS Date —
        the same bug elections._iso_utc exists to prevent everywhere else
        in this API."""
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D])
        _, coverage = _statewide_section(db_session, "RI", CYCLE)
        assert coverage["checkedAt"].endswith("Z")

    def test_coverage_is_per_cycle(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D])
        _, coverage = _statewide_section(db_session, "RI", CYCLE + 2)
        assert coverage["status"] == StatewideCoverageStatus.NOT_YET_COVERED


class TestOptIn:
    def test_a_state_that_has_not_opted_in_stores_nothing(self, db_session):
        """Every adapter returns only what its own state's feed contains,
        so a state nobody has checked returns zero executive contests —
        indistinguishable from a state that genuinely elects none. The
        flag is what makes zero a true claim, so without it there must be
        no claim at all."""
        stored = _sync_statewide_nominees(
            db_session, CYCLE, "GA", {"strategy": "tabular"}, [GOVERNOR_D],
        )
        assert stored == 0
        assert db_session.query(StatewideNominee).filter(
            StatewideNominee.state == "GA").count() == 0

    def test_a_state_that_has_not_opted_in_writes_no_marker(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "GA", {"strategy": "tabular"}, [])
        _, coverage = _statewide_section(db_session, "GA", CYCLE)
        assert coverage["status"] == StatewideCoverageStatus.NOT_YET_COVERED


class TestPersistence:
    def test_re_syncing_the_same_nominee_does_not_duplicate_them(self, db_session):
        for _ in range(3):
            _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D])
        assert db_session.query(StatewideNominee).count() == 1

    def test_a_replaced_nominee_overwrites_rather_than_accumulating(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D])
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [
            {**GOVERNOR_D, "last_name": "Someone Else"},
        ])
        rows = db_session.query(StatewideNominee).all()
        assert [r.display_name for r in rows] == ["Someone Else"]

    def test_a_withdrawn_nominee_is_deleted_not_left_behind(self, db_session):
        """The feed is the whole truth for (state, cycle) every run. A
        nominee who withdraws, or one that came from an amended re-post,
        must stop being shown as confirmed."""
        _sync_statewide_nominees(
            db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D, GOVERNOR_R, TREASURER_D],
        )
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D])
        races, _ = _statewide_section(db_session, "RI", CYCLE)
        assert [r["office"] for r in races] == ["governor"]
        assert [n["name"] for n in races[0]["nominees"]] == ["Helena Buonanno Foulkes"]

    def test_another_states_rows_are_untouched(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D])
        _sync_statewide_nominees(db_session, CYCLE, "VT", SOURCE, [])
        assert db_session.query(StatewideNominee).filter(
            StatewideNominee.state == "RI").count() == 1


class TestPayloadShape:
    def test_party_is_rendered_in_fecs_own_codes(self, db_session):
        """So the frontend colours a statewide nominee through the exact
        same majorPartyOf() every federal candidate goes through — two
        party vocabularies on one page is how the two drift apart."""
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D, GOVERNOR_R])
        races, _ = _statewide_section(db_session, "RI", CYCLE)
        assert {n["party"] for n in races[0]["nominees"]} == {"DEM", "REP"}

    def test_offices_come_back_in_ballot_order_not_insertion_order(self, db_session):
        _sync_statewide_nominees(
            db_session, CYCLE, "RI", SOURCE,
            [TREASURER_D, {"office": "attorney_general", "district": None,
                           "party": "D", "last_name": "Kimberly Ahern"}, GOVERNOR_D],
        )
        races, _ = _statewide_section(db_session, "RI", CYCLE)
        assert [r["office"] for r in races] == ["governor", "attorney_general", "treasurer"]

    def test_every_office_carries_a_human_label(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [GOVERNOR_D, TREASURER_D])
        races, _ = _statewide_section(db_session, "RI", CYCLE)
        assert [r["label"] for r in races] == ["Governor", "State Treasurer"]
