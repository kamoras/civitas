"""Persistence and coverage semantics for statewide-executive nominees
(state_candidates._sync_statewide_nominees + elections._statewide_section).

The point of nearly every test here is one distinction: an empty
statewide section on a state's ballot page can mean "this state elects
no executive officers this cycle" or "nobody has taught us this state's
feed yet", and rendering them identically tells a reader in the second
case that there is nothing to research. Same null-is-not-zero discipline
MeasureCoverage already enforces for ballot measures.
"""

from app.api.elections import (
    StatewideCoverageStatus,
    _state_leg_section,
    _statewide_marker,
    _statewide_section,
)
from app.models import StateLegNominee, StatewideNominee
from app.pipeline.fetch.state_candidates import (
    _sync_state_leg_nominees,
    _sync_statewide_nominees,
)

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


LOWER_13_R = {"office": "lower", "district": "13", "party": "R", "last_name": "Derick A. Reels"}
LOWER_13_D = {"office": "lower", "district": "13", "party": "D", "last_name": "Someone Else"}
UPPER_5_D = {"office": "upper", "district": "5", "party": "D", "last_name": "Samuel W. Bell"}


class TestStateLegislativePersistence:
    def test_seats_are_keyed_per_district_not_per_chamber(self, db_session):
        """Two seats in the same chamber and party are different rows.
        Keying on chamber alone would collapse all 75 of a state's House
        seats onto one."""
        _sync_state_leg_nominees(db_session, CYCLE, "RI", SOURCE, [
            LOWER_13_R, {**LOWER_13_R, "district": "14", "last_name": "Another Person"},
        ])
        assert db_session.query(StateLegNominee).count() == 2

    def test_both_parties_in_one_seat_are_kept(self, db_session):
        _sync_state_leg_nominees(db_session, CYCLE, "RI", SOURCE, [LOWER_13_R, LOWER_13_D])
        assert db_session.query(StateLegNominee).count() == 2

    def test_the_same_district_number_in_each_chamber_does_not_collide(self, db_session):
        """Chambers number their districts independently — upper 5 and
        lower 5 are unrelated seats in different places."""
        _sync_state_leg_nominees(db_session, CYCLE, "RI", SOURCE, [
            UPPER_5_D, {**UPPER_5_D, "office": "lower", "last_name": "Lower Five"},
        ])
        rows = {(r.chamber, r.district): r.display_name
                for r in db_session.query(StateLegNominee).all()}
        assert rows == {("upper", "5"): "Samuel W. Bell", ("lower", "5"): "Lower Five"}

    def test_a_withdrawn_nominee_is_deleted(self, db_session):
        _sync_state_leg_nominees(db_session, CYCLE, "RI", SOURCE, [LOWER_13_R, UPPER_5_D])
        _sync_state_leg_nominees(db_session, CYCLE, "RI", SOURCE, [UPPER_5_D])
        rows = db_session.query(StateLegNominee).all()
        assert [(r.chamber, r.district) for r in rows] == [("upper", "5")]

    def test_a_state_that_has_not_opted_in_stores_nothing(self, db_session):
        stored = _sync_state_leg_nominees(
            db_session, CYCLE, "GA", {"strategy": "tabular"}, [LOWER_13_R],
        )
        assert stored == 0
        assert db_session.query(StateLegNominee).count() == 0


class TestStateLegislativeSection:
    def _section(self, db):
        return _state_leg_section(db, "RI", CYCLE, _statewide_marker(db, "RI", CYCLE))

    def test_nothing_renders_before_the_state_has_been_checked(self, db_session):
        """Rows without a marker cannot happen through the pipeline, but
        the section must still refuse to claim coverage it has no record
        of — the marker is the claim, not the rows."""
        assert self._section(db_session) == []

    def test_chambers_come_back_separately_and_in_district_order(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [])  # writes the marker
        _sync_state_leg_nominees(db_session, CYCLE, "RI", SOURCE, [
            {**LOWER_13_R, "district": "9"}, LOWER_13_R, UPPER_5_D,
        ])
        section = self._section(db_session)
        assert [c["chamber"] for c in section] == ["upper", "lower"]
        assert [c["label"] for c in section] == ["State Senate", "State House"]
        lower = next(c for c in section if c["chamber"] == "lower")
        assert [d["district"] for d in lower["districts"]] == ["9", "13"]

    def test_a_district_carries_the_towns_it_covers(self, db_session):
        """The whole point of the crosswalk: a reader finds their seat by
        a place they know, never by being asked where they live."""
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [])
        _sync_state_leg_nominees(db_session, CYCLE, "RI", SOURCE, [UPPER_5_D])
        district = self._section(db_session)[0]["districts"][0]
        assert district["towns"], "RI-upper-5 should resolve to at least one town"
        assert all(isinstance(t, str) and t for t in district["towns"])

    def test_party_uses_the_same_codes_as_every_other_race(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "RI", SOURCE, [])
        _sync_state_leg_nominees(db_session, CYCLE, "RI", SOURCE, [LOWER_13_R, LOWER_13_D])
        nominees = self._section(db_session)[0]["districts"][0]["nominees"]
        assert {n["party"] for n in nominees} == {"DEM", "REP"}


class TestTopTwoSameParty:
    """California and Washington run one all-party contest and advance
    the top TWO, who can be of the same party — and an unaffiliated
    candidate normalises to no party at all, so two of them in one
    contest share a party value entirely.

    Keyed on seat-and-party alone, the second nominee silently replaced
    the first and the page showed one name where the ballot has two.
    """

    def test_two_same_party_nominees_for_one_statewide_office_both_survive(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "CA", SOURCE, [
            {"office": "governor", "district": None, "party": "D", "last_name": "First Democrat"},
            {"office": "governor", "district": None, "party": "D", "last_name": "Second Democrat"},
        ])
        races, _ = _statewide_section(db_session, "CA", CYCLE)
        assert sorted(n["name"] for n in races[0]["nominees"]) == [
            "First Democrat", "Second Democrat",
        ]

    def test_two_unaffiliated_nominees_for_one_seat_both_survive(self, db_session):
        """Both normalise to no party, so they differ only by name."""
        _sync_state_leg_nominees(db_session, CYCLE, "CA", SOURCE, [
            {"office": "lower", "district": "4", "party": "", "last_name": "No Party One"},
            {"office": "lower", "district": "4", "party": "", "last_name": "No Party Two"},
        ])
        assert db_session.query(StateLegNominee).count() == 2

    def test_a_renamed_nominee_still_leaves_exactly_one_row(self, db_session):
        """Name is part of the key now, so a correction would add rather
        than update — the delete-what-is-no-longer-reported pass is what
        keeps that from accumulating."""
        _sync_state_leg_nominees(db_session, CYCLE, "CA", SOURCE, [
            {"office": "lower", "district": "4", "party": "D", "last_name": "Mispelled Name"},
        ])
        _sync_state_leg_nominees(db_session, CYCLE, "CA", SOURCE, [
            {"office": "lower", "district": "4", "party": "D", "last_name": "Corrected Name"},
        ])
        rows = db_session.query(StateLegNominee).all()
        assert [r.display_name for r in rows] == ["Corrected Name"]


PSC_3_D = {"office": "public_service_commission", "district": "3", "party": "D",
           "last_name": "Third District Dem"}
PSC_5_D = {"office": "public_service_commission", "district": "5", "party": "D",
           "last_name": "Fifth District Dem"}


class TestStatewideBodySeatedByDistrict:
    """Georgia's Public Service Commission is elected statewide, but a
    commissioner holds the seat for a district and each seat is its own
    contest. Without a district on the row, "PSC - District 3" and
    "PSC - District 5" are one office and the second overwrites the
    first — a real Georgia ballot has both.
    """

    def test_two_seats_of_one_body_are_separate_rows(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "GA", SOURCE, [PSC_3_D, PSC_5_D])
        assert db_session.query(StatewideNominee).count() == 2

    def test_each_seat_renders_with_its_district(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "GA", SOURCE, [PSC_5_D, PSC_3_D])
        races, _ = _statewide_section(db_session, "GA", CYCLE)
        assert [r["label"] for r in races] == [
            "Public Service Commission, District 3",
            "Public Service Commission, District 5",
        ]
        assert [r["office"] for r in races] == [
            "public_service_commission-3", "public_service_commission-5",
        ]

    def test_an_office_without_a_seat_keeps_its_plain_label(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "GA", SOURCE, [
            {"office": "governor", "district": None, "party": "D", "last_name": "A Governor"},
        ])
        races, _ = _statewide_section(db_session, "GA", CYCLE)
        assert races[0]["label"] == "Governor"
        assert races[0]["office"] == "governor"

    def test_seats_sort_numerically(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "GA", SOURCE, [
            {**PSC_3_D, "district": "10"}, PSC_3_D, {**PSC_3_D, "district": "2"},
        ])
        races, _ = _statewide_section(db_session, "GA", CYCLE)
        assert [r["label"].split("District ")[1] for r in races] == ["2", "3", "10"]
