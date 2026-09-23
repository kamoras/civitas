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
    JudicialCoverageStatus,
    StatewideCoverageStatus,
    _judicial_marker,
    _judicial_section,
    _state_leg_section,
    _statewide_marker,
    _statewide_section,
)
from app.models import JudicialNominee, StateLegNominee, StatewideNominee
from app.pipeline.fetch.state_candidates import (
    _sync_judicial_nominees,
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


class TestMultiMemberSeats:
    """Idaho and Washington elect two representatives from one district.
    Both seats share a geography, so both share a town list — but they
    are two separate contests with two separate nominees.
    """

    def _seat(self, district, seat, name, party="D"):
        return {"office": "lower", "district": district, "seat": seat,
                "party": party, "last_name": name}

    def test_two_seats_of_one_district_are_separate_rows(self, db_session):
        _sync_state_leg_nominees(db_session, CYCLE, "ID", SOURCE, [
            self._seat("1", "A", "Seat A Person"),
            self._seat("1", "B", "Seat B Person"),
        ])
        assert db_session.query(StateLegNominee).count() == 2

    def test_each_seat_renders_with_its_own_label(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "ID", SOURCE, [])
        _sync_state_leg_nominees(db_session, CYCLE, "ID", SOURCE, [
            self._seat("1", "B", "Seat B Person"),
            self._seat("1", "A", "Seat A Person"),
        ])
        section = _state_leg_section(db_session, "ID", CYCLE, _statewide_marker(db_session, "ID", CYCLE))
        assert [d["district"] for d in section[0]["districts"]] == ["1A", "1B"]

    def test_washington_positions_render_hyphenated(self, db_session):
        _sync_statewide_nominees(db_session, CYCLE, "WA", SOURCE, [])
        _sync_state_leg_nominees(db_session, CYCLE, "WA", SOURCE, [
            self._seat("5", "1", "Position One"), self._seat("5", "2", "Position Two"),
        ])
        section = _state_leg_section(db_session, "WA", CYCLE, _statewide_marker(db_session, "WA", CYCLE))
        assert [d["district"] for d in section[0]["districts"]] == ["5-1", "5-2"]

    def test_both_seats_resolve_the_same_towns(self, db_session):
        """They are one geography. The crosswalk is keyed on the bare
        district for exactly this reason."""
        _sync_statewide_nominees(db_session, CYCLE, "ID", SOURCE, [])
        _sync_state_leg_nominees(db_session, CYCLE, "ID", SOURCE, [
            self._seat("1", "A", "Seat A Person"), self._seat("1", "B", "Seat B Person"),
        ])
        section = _state_leg_section(db_session, "ID", CYCLE, _statewide_marker(db_session, "ID", CYCLE))
        towns = [d["towns"] for d in section[0]["districts"]]
        assert towns[0] == towns[1]

    def test_a_withdrawn_seat_does_not_take_its_sibling(self, db_session):
        _sync_state_leg_nominees(db_session, CYCLE, "ID", SOURCE, [
            self._seat("1", "A", "Seat A Person"), self._seat("1", "B", "Seat B Person"),
        ])
        _sync_state_leg_nominees(db_session, CYCLE, "ID", SOURCE, [
            self._seat("1", "A", "Seat A Person"),
        ])
        rows = db_session.query(StateLegNominee).all()
        assert [(r.district, r.seat) for r in rows] == [("1", "A")]


JUDICIAL_SOURCE = {
    "strategy": "tabular",
    "source_name": "NC State Board of Elections",
    "statewide_offices": True,
    "judicial_offices": True,
}


def _judicial(court, district, seat, party, name):
    return {"office": court, "district": district, "seat": seat,
            "party": party, "last_name": name}


class TestJudicialOptIn:
    """judicial_offices is a SEPARATE claim from statewide_offices, and
    deliberately not implied by it.

    statewide_offices asserts only that a state's real labels were read.
    judicial_offices asserts something stronger — that this state's
    judicial primaries NOMINATE rather than ELECT — because a
    non-partisan judicial election usually elects a majority winner
    outright, and publishing one as a November candidate would be
    confidently wrong. Georgia's 92 judgeships parse through the same
    gate and must stay unpublished for exactly that reason.
    """

    RECORDS = [_judicial("district", "3", "2", "R", "Lloyd Williams")]

    def test_statewide_opt_in_alone_publishes_nothing(self, db_session):
        source = {**SOURCE, "statewide_offices": True}   # no judicial_offices
        assert _sync_judicial_nominees(
            db_session, CYCLE, "GA", source, self.RECORDS) == 0
        assert db_session.query(JudicialNominee).count() == 0

    def test_its_own_opt_in_publishes(self, db_session):
        assert _sync_judicial_nominees(
            db_session, CYCLE, "NC", JUDICIAL_SOURCE, self.RECORDS) == 1
        row = db_session.query(JudicialNominee).one()
        assert (row.court, row.district, row.seat, row.party) == ("district", "3", "2", "R")
        assert row.display_name == "Lloyd Williams"


class TestJudicialPersistence:
    def test_an_appellate_seat_stores_a_null_district(self, db_session):
        _sync_judicial_nominees(db_session, CYCLE, "NC", JUDICIAL_SOURCE,
                                [_judicial("appeals", None, "4", "R", "Michael C. Byrne")])
        row = db_session.query(JudicialNominee).one()
        assert row.district is None and row.seat == "4"

    def test_a_seat_absent_from_the_next_run_is_deleted(self, db_session):
        """The feed is the whole truth for (state, cycle) every run, same
        as both sibling tables."""
        _sync_judicial_nominees(db_session, CYCLE, "NC", JUDICIAL_SOURCE, [
            _judicial("district", "3", "2", "R", "Lloyd Williams"),
            _judicial("district", "14", "3", "D", "Sherry Miller"),
        ])
        assert db_session.query(JudicialNominee).count() == 2

        _sync_judicial_nominees(db_session, CYCLE, "NC", JUDICIAL_SOURCE,
                                [_judicial("district", "3", "2", "R", "Lloyd Williams")])
        assert [r.display_name for r in db_session.query(JudicialNominee).all()] == [
            "Lloyd Williams"]

    def test_a_duplicate_record_in_one_run_does_not_blow_up(self, db_session):
        """autoflush=False means the lookup cannot see a row added moments
        ago, so a feed listing one nominee twice would queue two
        identical rows and fail the unique constraint at commit — the
        shape that took the coverage refresh down."""
        stored = _sync_judicial_nominees(db_session, CYCLE, "NC", JUDICIAL_SOURCE, [
            _judicial("district", "3", "2", "R", "Lloyd Williams"),
            _judicial("district", "3", "2", "R", "Lloyd Williams"),
        ])
        assert stored == 1
        assert db_session.query(JudicialNominee).count() == 1

    def test_two_courts_can_share_a_seat_number(self, db_session):
        """District seat 1 and Superior seat 1 are different benches —
        which is why `court` is part of the key."""
        _sync_judicial_nominees(db_session, CYCLE, "NC", JUDICIAL_SOURCE, [
            _judicial("district", "3", "1", "R", "One Judge"),
            _judicial("superior", "3", "1", "D", "Another Judge"),
        ])
        assert db_session.query(JudicialNominee).count() == 2


class TestJudicialSection:
    def test_seats_are_grouped_by_court_in_seniority_order(self, db_session):
        _sync_judicial_nominees(db_session, CYCLE, "NC", JUDICIAL_SOURCE, [
            _judicial("district", "14", "3", "D", "Sherry Miller"),
            _judicial("appeals", None, "4", "R", "Michael C. Byrne"),
            _judicial("supreme", None, "1", "D", "A Justice"),
        ])
        out, _ = _judicial_section(db_session, "NC", CYCLE, {"checkedAt": "x", "count": 3})
        assert [s["court"] for s in out] == ["supreme", "appeals", "district"]

    def test_a_trial_seat_names_its_district_and_an_appellate_one_does_not(self, db_session):
        _sync_judicial_nominees(db_session, CYCLE, "NC", JUDICIAL_SOURCE, [
            _judicial("district", "14", "3", "D", "Sherry Miller"),
            _judicial("appeals", None, "4", "R", "Michael C. Byrne"),
        ])
        out = {s["court"]: s["seats"] for s in _judicial_section(
            db_session, "NC", CYCLE, {"checkedAt": "x", "count": 2})[0]}
        assert out["district"][0]["seat"] == "District 14, Seat 3"
        assert out["appeals"][0]["seat"] == "Seat 4"

    def test_a_state_that_never_opted_in_has_no_section(self, db_session):
        races, coverage = _judicial_section(db_session, "GA", CYCLE, None)
        assert races == []
        assert coverage["status"] == JudicialCoverageStatus.NOT_YET_COVERED


class TestJudicialCoverageStatus:
    """The distinction that lets Idaho be published at all.

    A state whose judicial seats were ALL decided in its primary has
    genuinely zero November contests. Idaho is exactly that: its three
    matched contests were unopposed, so all three were elected in May
    under Idaho Code 34-1217. Rendering that identically to "nobody has
    read this state's judicial statute yet" tells a reader there is
    nothing to research when the truth is that the races are over.
    """

    def test_never_checked_is_not_yet_covered(self, db_session):
        races, coverage = _judicial_section(db_session, "GA", CYCLE, None)
        assert races == []
        assert coverage["status"] == JudicialCoverageStatus.NOT_YET_COVERED
        assert coverage["checkedAt"] is None

    def test_checked_with_no_seats_is_confirmed_none(self, db_session):
        """Idaho's real shape — checked, and the answer is none."""
        marker = {"checkedAt": "2026-09-22T00:00:00Z", "count": 0,
                  "sourceName": "Idaho Secretary of State"}
        races, coverage = _judicial_section(db_session, "ID", CYCLE, marker)
        assert races == []
        assert coverage["status"] == JudicialCoverageStatus.CONFIRMED_NONE
        assert coverage["checkedAt"] == "2026-09-22T00:00:00Z"
        assert coverage["sourceName"] == "Idaho Secretary of State"

    def test_checked_with_seats_is_covered(self, db_session):
        _sync_judicial_nominees(db_session, CYCLE, "NC", JUDICIAL_SOURCE,
                                [_judicial("district", "3", "2", "R", "Lloyd Williams")])
        marker = {"checkedAt": "2026-09-22T00:00:00Z", "count": 1, "sourceName": "NC"}
        races, coverage = _judicial_section(db_session, "NC", CYCLE, marker)
        assert races and coverage["status"] == JudicialCoverageStatus.COVERED

    def test_the_sync_writes_a_marker_even_with_nothing_to_store(self, db_session):
        """The load-bearing case: without this, a state with zero
        November judicial contests is indistinguishable from one nobody
        looked at."""
        stored = _sync_judicial_nominees(
            db_session, CYCLE, "ID",
            {**JUDICIAL_SOURCE, "source_name": "Idaho Secretary of State"}, [])
        assert stored == 0
        marker = _judicial_marker(db_session, "ID", CYCLE)
        assert marker is not None, "an empty result must still record that we looked"
        assert marker["count"] == 0

    def test_a_state_that_never_opted_in_writes_no_marker(self, db_session):
        """Opt-out must stay distinguishable from checked-and-empty."""
        assert _sync_judicial_nominees(
            db_session, CYCLE, "GA", {**SOURCE, "judicial_offices": False}, []) == 0
        assert _judicial_marker(db_session, "GA", CYCLE) is None
