"""Tests for the parsing shared by every confirmed-nominee adapter
(state_candidates_common.py) — the rules that must hold identically no
matter which vendor's envelope the contest arrived in.
"""

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
