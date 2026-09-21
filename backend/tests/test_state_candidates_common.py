"""Tests for the parsing shared by every confirmed-nominee adapter
(state_candidates_common.py) — the rules that must hold identically no
matter which vendor's envelope the contest arrived in.
"""

import pytest

from app.pipeline.fetch import state_candidates_common as common


class TestParseOffice:
    def test_recognises_every_live_house_label_wording(self):
        """One wording per real feed already in production — Clarity/CO,
        NCSBE/NC and California's Statement of Vote all differ."""
        for label, district in [
            ("Representative to the 120th United States Congress - District 1 - Democratic Party", 1),
            ("US HOUSE OF REPRESENTATIVES DISTRICT 05 (REP)", 5),
            ("United States Representative District 10", 10),
        ]:
            assert common.parse_office(label) == ("H", district), label

    def test_recognises_senate_as_senate_or_senator(self):
        assert common.parse_office("United States Senator - Democratic Party") == ("S", None)
        assert common.parse_office("US SENATE (DEM)") == ("S", None)

    def test_recognises_the_bare_abbreviated_us_congress(self):
        """Arkansas's real label ("REP U.S. Congress District 02") uses
        the short "U.S." form with "Congress" — previously only
        "United States Congress" (spelled out) or "U.S. House/
        Representative" were recognised, so this real label parsed as
        None. "Congress" alone is already the decisive, state-legislature-
        proof signal this function relies on elsewhere; the gap was only
        in the abbreviation, not a new safety question."""
        assert common.parse_office("REP U.S. Congress District 02") == ("H", 2)
        assert common.parse_office("DEM U.S. Congress District 04") == ("H", 4)
        assert common.parse_office("U.S. Senate") == ("S", None)

    def test_congress_does_not_match_inside_a_longer_word(self):
        """A county-export convention groups a local race under its
        containing congressional district ("U.S. Congressional District 3
        County Commissioner") -- without a word boundary after "Congress",
        the abbreviated U.S. group added for Arkansas would misread that
        substring as a real federal contest."""
        assert common.parse_office("US Congressional Redistricting Commission") is None
        assert common.parse_office("U.S. Congressional District Court Judge") is None
        assert common.parse_office("U.S. Congressional District 3 County Commissioner") is None

    def test_leading_zero_district_is_not_octal_or_string(self):
        assert common.parse_office("US HOUSE OF REPRESENTATIVES DISTRICT 022") == ("H", 22)

    def test_congress_is_decisive_wherever_it_appears(self):
        """No state legislature is called a Congress, so these forms are
        safe — and a crawler meeting an unfamiliar state's labels needs
        them: Florida writes "Representative in Congress, District 2",
        Illinois just "1ST CONGRESS"."""
        assert common.parse_office("Representative in Congress, District 2") == ("H", 2)
        assert common.parse_office("1ST CONGRESS") == ("H", 1)
        assert common.parse_office("10TH CONGRESS") == ("H", 10)
        # No number at all is an at-large seat, not a parse failure.
        assert common.parse_office("Representative in Congress") == ("H", None)

    def test_recognises_the_bare_representative_to_congress(self):
        """Vermont's real label ("REPRESENTATIVE TO CONGRESS") uses "to"
        where Florida's uses "in" — no "U.S."/"United States" prefix
        either. Same word-boundary/no-qualifier tradeoff this function
        already accepted for "Representative in Congress" (see
        test_congress_is_decisive_wherever_it_appears): a state whose
        LABEL happens to contain this exact three-word run in an
        unrelated context (a committee name, a filing-system free-text
        field) would also be read as federal — an accepted, pre-existing
        risk class for this whole no-qualifier branch, not one newly
        opened by adding "to"."""
        assert common.parse_office("REPRESENTATIVE TO CONGRESS") == ("H", None)
        assert common.parse_office("Representative to Congress, District 2") == ("H", 2)

    def test_recognises_the_bare_senator_in_congress(self):
        """Rhode Island's real label ("DEM Senator in Congress") carries
        no "U.S."/"United States" prefix at all — the mirror of the
        "Representative in/to Congress" case above, and safe for the same
        reason: no STATE chamber is named "Congress"."""
        assert common.parse_office("DEM Senator in Congress") == ("S", None)
        assert common.parse_office("Senator to Congress") == ("S", None)

    def test_rhode_islands_general_assembly_is_not_congress(self):
        """The live regression control for the Senate branch above, and
        the sharpest one this system has: Rhode Island's STATE legislature
        is literally named the "General Assembly", so these sit on the
        very same primary ballot as the federal contests — 140 of that
        ballot's 192 contests — a few characters from the real thing."""
        assert common.parse_office("DEM Senator in General Assembly District 5") is None
        assert common.parse_office("REP Representative in General Assembly District 13") is None
        assert common.parse_office("DEM Senatorial District Committee District 13") is None
        assert common.parse_office("DEM Representative District Committee District 5") is None

    def test_a_bare_senator_label_is_still_not_federal(self):
        """The Senate branch must stay narrow: "Senator, District 5" is a
        STATE senate seat in most states and always has been."""
        assert common.parse_office("Senator, District 5") is None
        assert common.parse_office("State Senator District 12") is None
        assert common.parse_office("Senator in General Assembly") is None

    def test_an_ordinal_district_needs_the_chamber_to_be_federal_first(self):
        """"5th District" on its own belongs to no chamber in particular —
        recognising it alone would sweep in judicial and state races."""
        assert common.parse_office("5th District Judge") is None
        assert common.parse_office("Member, House of Representatives (2nd District)") is None
        assert common.parse_office("State Representative 3rd District") is None

    def test_the_ordinal_of_a_congress_is_not_a_district(self):
        """Colorado numbers the CONGRESS itself ("120th United States
        Congress"), which must never be read as district 120."""
        assert common.parse_office(
            "Representative to the 120th United States Congress - District 1 - Democratic Party",
        ) == ("H", 1)

    def test_state_races_that_look_federal_are_rejected(self):
        """The live regression control: North Carolina's own legislature
        uses "HOUSE OF REPRESENTATIVES DISTRICT nnn" too."""
        assert common.parse_office("NC HOUSE OF REPRESENTATIVES DISTRICT 022 (REP)") is None
        assert common.parse_office("State Assembly Member District 1") is None
        assert common.parse_office("State Senate District 10") is None
        assert common.parse_office("Governor") is None


class TestOfficeFromColumns:
    """The second route to the same answer, for a state whose LABEL can't
    identify a federal seat — Virginia's real 2026 rows. Every value comes
    from that state's config entry, so no state name appears in the code.
    """

    _SPEC = {
        "type_column": "DistrictType", "type_value": "congressional",
        "district_column": "DistrictName",
    }

    def test_reads_the_district_from_the_configured_columns(self):
        row = {"DistrictType": "congressional", "DistrictName": "02",
               "OfficeTitle": "Member, House of Representatives (2nd District)"}
        assert common.office_from_columns(row, self._SPEC) == ("H", 2)
        # And the label alone must still be refused — "House of
        # Representatives" without "U.S." is a state chamber in many states.
        assert common.parse_office(row["OfficeTitle"]) is None

    def test_a_row_the_marker_does_not_match_is_not_federal(self):
        row = {"DistrictType": "county", "DistrictName": "ARLINGTON COUNTY"}
        assert common.office_from_columns(row, self._SPEC) is None

    def test_no_spec_configured_means_this_route_is_off(self):
        assert common.office_from_columns({"DistrictType": "congressional"}, None) is None

    def test_a_congressional_row_with_no_number_is_an_at_large_seat(self):
        row = {"DistrictType": "congressional", "DistrictName": ""}
        assert common.office_from_columns(row, self._SPEC) == ("H", None)


class TestLastFirstNames:
    """Several states print the ballot name the way FEC files it. Taking
    the trailing token there is not a near miss — it is a different
    person's name."""

    def test_surname_is_what_precedes_the_comma(self):
        assert common.surname("CASE, Ed", last_first=True) == "CASE"
        assert common.surname("Darden, Dustin Thomas House", last_first=True) == "Darden"

    def test_a_multi_word_surname_survives(self):
        assert common.surname("Van Drew, Jefferson", last_first=True) == "Van Drew"

    def test_the_default_still_reads_first_last(self):
        assert common.surname("Ed Case") == "Case"

    def test_a_name_without_a_comma_falls_back_rather_than_emptying(self):
        assert common.surname("Ed Case", last_first=True) == "Case"


class TestRomanNumeralDistricts:
    """Hawaii numbers its congressional districts I and II."""

    def test_reads_a_roman_district(self):
        assert common.parse_office("U.S. Representative, Dist I") == ("H", 1)
        assert common.parse_office("U.S. Representative, Dist II") == ("H", 2)

    def test_still_refuses_a_state_race_written_the_same_way(self):
        assert common.parse_office("State Rep Dist IV") is None


class TestNormalizeParty:
    def test_reads_spelled_out_and_abbreviated_forms(self):
        assert common.normalize_party("... - Democratic Party") == "D"
        assert common.normalize_party("US SENATE (DEM)") == "D"
        assert common.normalize_party("(REP)") == "R"
        assert common.normalize_party("Democratic") == "D"

    def test_representative_is_not_mistaken_for_republican(self):
        """"Representative" contains "rep" — a word-boundary failure here
        would label every top-two House contest Republican."""
        assert common.normalize_party("United States Representative District 10") is None

    def test_unaffiliated_and_unknown_yield_none(self):
        assert common.normalize_party("No Party Preference") is None
        assert common.normalize_party("") is None


class TestSurname:
    def test_takes_trailing_token_and_drops_suffixes(self):
        assert common.surname("Melat Kiros") == "Kiros"
        assert common.surname("Dwayne L. Romero") == "Romero"
        assert common.surname("Robert Cruz Jr.") == "Cruz"

    def test_strips_a_parenthetical_incumbency_marker(self):
        """Georgia's ballot names carry "(I)" for incumbents — without
        stripping it, every sitting member's surname became "(I)"."""
        assert common.surname('Earl L. "Buddy" Carter (I)') == "Carter"
        assert common.surname("Sanford Bishop (I)") == "Bishop"

    def test_strips_rhode_islands_party_endorsement_asterisk(self):
        """Rhode Island appends "*" to the party-ENDORSED candidate on its
        real ballot. "Reed*" matches no FEC row, so leaving it on looks
        like a missing nominee rather than a parsing bug."""
        assert common.surname("John F. Reed*") == "Reed"
        assert common.surname("Stephen T. Skoly*") == "Skoly"
        assert common.surname("CASE, Ed*", last_first=True) == "CASE"

    def test_an_annotation_only_name_yields_nothing(self):
        assert common.surname("*") is None
        assert common.surname("(I)") is None


class TestPickNominees:
    def test_single_nominee_party_primary(self):
        won = common.pick_nominees([("A", 100), ("B", 50)], None, 1)
        assert [n for n, _ in won] == ["A"]

    def test_top_two_advances_both_even_when_same_party(self):
        """California regularly sends two candidates of the same party to
        the general; taking only the leader would drop a real option."""
        won = common.pick_nominees([("A", 100), ("B", 80), ("C", 10)], None, 2)
        assert [n for n, _ in won] == ["A", "B"]

    def test_zero_vote_candidates_never_advance(self):
        won = common.pick_nominees([("A", 100), ("B", 0)], None, 2)
        assert [n for n, _ in won] == ["A"]

    def test_tie_across_the_cutoff_truncates_to_those_strictly_above(self):
        """Who broke a tie for the last advancing slot is the state's to
        certify, not ours to guess."""
        won = common.pick_nominees([("A", 100), ("B", 50), ("C", 50)], None, 2)
        assert [n for n, _ in won] == ["A"]

    def test_tie_for_the_lead_in_a_one_winner_race_yields_nobody(self):
        assert common.pick_nominees([("A", 50), ("B", 50)], None, 1) == []

    def test_runoff_threshold_withholds_a_sub_threshold_leader(self):
        assert common.pick_nominees([("A", 40), ("B", 35), ("C", 25)], 50.0, 1) == []
        assert [n for n, _ in common.pick_nominees([("A", 40), ("B", 35), ("C", 25)], 30.0, 1)] == ["A"]

    def test_threshold_is_ignored_for_top_two_which_has_no_runoff(self):
        """A top-two state's leader on 40% has genuinely advanced — the
        runoff rule must not be applied to a race that has no runoff."""
        won = common.pick_nominees([("A", 40), ("B", 35), ("C", 25)], 50.0, 2)
        assert [n for n, _ in won] == ["A", "B"]

    def test_empty_field_advances_nobody(self):
        assert common.pick_nominees([], None, 2) == []
        assert common.pick_nominees([("A", 0)], None, 1) == []


class TestResolveConfirmedNominees:
    """The "group by seat, resolve via pick_nominee, shape the record"
    tail extracted from OR/VT/MA/KS/MS/TotalVote — see that function's
    own docstring for why."""

    def test_builds_a_record_per_resolvable_seat(self):
        by_seat = {
            ("H", 1, "D"): [("Smith", 100), ("Jones", 50)],
            ("S", None, "R"): [("Doe", 200)],
        }
        results = common.resolve_confirmed_nominees(by_seat, None)
        assert {"office": "H", "district": 1, "party": "D", "last_name": "Smith"} in results
        assert {"office": "S", "district": None, "party": "R", "last_name": "Doe"} in results
        assert len(results) == 2

    def test_a_seat_with_no_safe_winner_contributes_nothing(self):
        # A tie for the lead in a one-winner race.
        by_seat = {("H", 1, "D"): [("A", 50), ("B", 50)]}
        assert common.resolve_confirmed_nominees(by_seat, None) == []

    def test_runoff_threshold_is_forwarded_to_pick_nominee(self):
        by_seat = {("H", 1, "D"): [("A", 40), ("B", 35), ("C", 25)]}
        assert common.resolve_confirmed_nominees(by_seat, 50.0) == []
        result = common.resolve_confirmed_nominees(by_seat, 30.0)
        assert result == [{"office": "H", "district": 1, "party": "D", "last_name": "A"}]

    def test_name_transform_runs_only_after_a_winner_is_resolved(self):
        # Vermont's real shape: raw display names in, reduced to surname
        # only AFTER pick_nominee has already picked from the real votes
        # -- proves the transform doesn't affect ranking.
        by_seat = {("H", None, "D"): [("Becca Balint", 100), ("Someone Else", 90)]}
        result = common.resolve_confirmed_nominees(by_seat, None, name_transform=common.surname)
        assert result == [{"office": "H", "district": None, "party": "D", "last_name": "Balint"}]

    def test_a_transform_that_empties_the_name_drops_the_seat(self):
        by_seat = {("H", None, "D"): [("   ", 100)]}
        result = common.resolve_confirmed_nominees(by_seat, None, name_transform=common.surname)
        assert result == []

    def test_empty_grouping_returns_empty_list(self):
        assert common.resolve_confirmed_nominees({}, None) == []


class TestDiscoveryFailed:
    def test_is_a_real_exception_distinct_from_a_bare_exception(self):
        with pytest.raises(common.DiscoveryFailed):
            raise common.DiscoveryFailed("a genuine fetch failure")


class TestDistrictSortKey:
    """Districts sort in natural order, not lexical.

    Lexical order puts "10" before "9" — which would scatter a 75-seat
    chamber — and separates Minnesota's "10A"/"10B" pairs.
    """

    def test_numeric_order_not_string_order(self):
        assert sorted(["10", "9", "1", "100", "2"], key=common.district_sort_key) == [
            "1", "2", "9", "10", "100",
        ]

    def test_lettered_districts_sort_after_their_number(self):
        assert sorted(["10B", "9", "10A", "10"], key=common.district_sort_key) == [
            "9", "10", "10A", "10B",
        ]

    def test_an_unparseable_identifier_sorts_last_rather_than_raising(self):
        ordered = sorted(["5", "unknown", "1"], key=common.district_sort_key)
        assert ordered[-1] == "unknown"


class TestParseStateLegOffice:
    """Grounded in Rhode Island's real 2026 primary labels; the refusals
    are the three party-committee shapes that collide with them."""

    def test_both_chambers_resolve(self):
        assert common.parse_state_leg_office(
            "DEM Senator in General Assembly District 5") == ("upper", "5", None)
        assert common.parse_state_leg_office(
            "REP Representative in General Assembly District 13") == ("lower", "13", None)

    def test_leading_zeros_are_the_same_seat(self):
        assert common.parse_state_leg_office(
            "DEM Senator in General Assembly District 05") == ("upper", "5", None)

    def test_a_trailing_letter_is_part_of_the_district(self):
        """Minnesota names two house districts per senate district, "10A"
        and "10B". Dropping the letter would merge two real seats."""
        assert common.parse_state_leg_office(
            "Representative in General Assembly District 10A") == ("lower", "10A", None)
        assert common.parse_state_leg_office(
            "Representative in General Assembly District 10b") == ("lower", "10B", None)

    def test_party_committee_races_are_refused(self):
        for label in (
            "DEM Senatorial District Committee District 13",
            "DEM Representative District Committee District 5",
            "DEM State Committeewoman District 1",
            "DEM State Committeeman District 3",
            "DEM Ward Committee Cranston Ward 5",
        ):
            assert common.parse_state_leg_office(label) is None, label

    def test_federal_and_municipal_contests_are_refused(self):
        for label in (
            "DEM Senator in Congress",
            "DEM Representative in Congress District 1",
            "DEM Pawtucket: City Council Pawtucket District 1",
            "DEM Governor",
        ):
            assert common.parse_state_leg_office(label) is None, label

    def test_a_seat_without_a_district_is_not_publishable(self):
        """It cannot be told apart from the other 74."""
        assert common.parse_state_leg_office("DEM Senator in General Assembly") is None


class TestStatewideOfficeCountyTraps:
    """Real labels off Minnesota's 2026 primary export that contain a
    statewide office's exact words but are county offices."""

    def test_state_auditor_is_statewide_but_county_auditor_is_not(self):
        assert common.parse_statewide_office("State Auditor") == ("auditor", None)
        assert common.parse_statewide_office("County Auditor/Treasurer") is None

    def test_county_attorney_is_not_the_attorney_general(self):
        assert common.parse_statewide_office("Attorney General") == ("attorney_general", None)
        assert common.parse_statewide_office("County Attorney") is None

    def test_a_joint_governor_ticket_is_the_top_of_the_ticket(self):
        """Minnesota prints one contest for both offices."""
        assert common.parse_statewide_office("Governor & Lt Governor") == ("governor", None)

    def test_a_bare_auditor_is_refused(self):
        """Unqualified, it is a county office in most states."""
        assert common.parse_statewide_office("Auditor") is None


class TestStateLegChamberForms:
    """Minnesota's plain forms, alongside Rhode Island's."""

    def test_minnesota_forms_resolve(self):
        assert common.parse_state_leg_office("State Senator District 10") == ("upper", "10", None)
        assert common.parse_state_leg_office(
            "State Representative District 10A") == ("lower", "10A", None)

    def test_a_judicial_district_contest_is_refused(self):
        """It names a district and would otherwise look like a seat."""
        assert common.parse_state_leg_office("Judge - Nh District Court 7") is None

    def test_a_county_commissioner_district_is_refused(self):
        assert common.parse_state_leg_office("County Commissioner District 1") is None
        assert common.parse_state_leg_office(
            "Special Election for County Commissioner District 3") is None

    def test_a_bare_senator_district_stays_refused(self):
        """Without the "State" qualifier this is a congressional seat in
        any state that prints its federal races that way."""
        assert common.parse_state_leg_office("Senator, District 5") is None


class TestStatewideOfficePhrases:
    """Four statewide constitutional offices that the general locality
    gate was refusing on a word it is right to distrust in general.

    Every label is real, off Georgia's 2026 primary, where these were
    being missed while Governor/Lt Gov/AG/Secretary of State resolved —
    so the page would have dropped its "statewide executive contests"
    omission while still omitting four of them.
    """

    def test_the_commissioner_offices_resolve(self):
        assert common.parse_statewide_office(
            "Commissioner of Insurance - Rep") == ("insurance_commissioner", None)
        assert common.parse_statewide_office(
            "Commissioner of Agriculture - Dem") == ("agriculture_commissioner", None)
        assert common.parse_statewide_office(
            "Commissioner of Labor - Rep") == ("labor_commissioner", None)

    def test_either_word_order_works(self):
        assert common.parse_statewide_office(
            "Insurance Commissioner") == ("insurance_commissioner", None)

    def test_the_school_superintendent_resolves_in_both_common_wordings(self):
        assert common.parse_statewide_office(
            "State School Superintendent - Dem") == ("school_superintendent", None)
        assert common.parse_statewide_office(
            "Superintendent of Public Instruction") == ("school_superintendent", None)

    def test_a_county_commissioner_is_still_refused(self):
        """"commissioner" is in the locality gate precisely because of
        these; the new phrases must not reopen that door."""
        assert common.parse_statewide_office("County Commissioner District 1") is None
        assert common.parse_statewide_office("County Park Commissioner District 3") is None

    def test_a_school_committee_is_still_refused(self):
        assert common.parse_statewide_office(
            "DEM North Providence: School Committee At-Large") is None
        assert common.parse_statewide_office("School Committee City of Pawtucket") is None

    def test_a_locality_marker_beats_the_phrase(self):
        """A county really can have a school superintendent."""
        assert common.parse_statewide_office("County School Superintendent") is None
        assert common.parse_statewide_office("Cranston: Commissioner of Labor") is None

    def test_a_statewide_body_seated_by_district_carries_its_seat(self):
        """Georgia's Public Service Commission is elected statewide but
        held by district, and runs District 3 and District 5 as separate
        contests. Without the seat they are one office."""
        assert common.parse_statewide_office(
            "PSC - District 3 - Dem") == ("public_service_commission", "3")
        assert common.parse_statewide_office(
            "Public Service Commissioner District 2") == ("public_service_commission", "2")

    def test_an_ordinary_statewide_office_never_carries_a_seat(self):
        """There is one Secretary of State, so a seat number is never
        attached to one."""
        assert common.parse_statewide_office("Secretary of State") == (
            "secretary_of_state", None)

    def test_a_district_beside_a_seatless_office_is_refused_outright(self):
        """"Secretary of State - District 4" is not a real contest, and
        the general locality gate distrusts "district" — so the label is
        refused rather than read as the statewide office. Only the
        offices that genuinely have seats bypass that gate, and they do
        it through _STATEWIDE_PHRASES."""
        assert common.parse_statewide_office("Secretary of State - District 4") is None

    def test_every_office_code_has_a_label(self):
        """The API renders from this map; a code without one would show
        a blank heading."""
        for code, _pattern in common._STATEWIDE_PHRASES:
            assert code in common.STATEWIDE_OFFICE_LABELS
        for code, _pattern in common._STATEWIDE_OFFICES:
            assert code in common.STATEWIDE_OFFICE_LABELS


class TestMultiMemberDistricts:
    """Some states elect several members from ONE district, and the seat
    is what tells their contests apart.

    Idaho runs "District 1 Seat A" and "Seat B"; Washington runs
    "Pos. 1" and "Pos. 2" of a Legislative District. Without the seat
    both collapse onto one district identifier and one of the two real
    contests disappears.

    This is a different thing from Minnesota's "10A"/"10B", and Census
    settles which is which: Minnesota has 134 lower-chamber polygons
    named 10A, 10B, ..., while Idaho has 35 and Washington 49, numbered
    plainly. So Minnesota's letter belongs to the district and Idaho's
    belongs to the seat.
    """

    def test_idaho_seats_share_a_district(self):
        assert common.parse_state_leg_office(
            "State Representative District 1 Seat A - Republican") == ("lower", "1", "A")
        assert common.parse_state_leg_office(
            "State Representative District 1 Seat B - Democratic") == ("lower", "1", "B")

    def test_washington_positions_share_a_district(self):
        assert common.parse_state_leg_office(
            "State Representative Pos. 2 - Legislative District 5") == ("lower", "5", "2")

    def test_a_washington_senate_seat_has_no_position(self):
        assert common.parse_state_leg_office(
            "State Senator - Legislative District 5") == ("upper", "5", None)

    def test_minnesotas_letter_stays_on_the_district(self):
        """10A is its own district with its own boundaries, so the
        letter must NOT become a seat — the town crosswalk is keyed on
        the district and would resolve to the wrong polygon."""
        assert common.parse_state_leg_office(
            "State Representative District 10A") == ("lower", "10A", None)

    def test_single_member_states_carry_no_seat(self):
        assert common.parse_state_leg_office(
            "DEM Senator in General Assembly District 5") == ("upper", "5", None)


class TestDistrictLabel:
    def test_a_lone_district_reads_as_its_number(self):
        assert common.district_label("5", None) == "5"
        assert common.district_label("10A", None) == "10A"

    def test_a_lettered_seat_appends(self):
        """Idaho writes them exactly this way."""
        assert common.district_label("1", "A") == "1A"

    def test_a_numbered_position_is_hyphenated(self):
        """"52" would be a different district; "5-2" cannot be read that
        way."""
        assert common.district_label("5", "2") == "5-2"
