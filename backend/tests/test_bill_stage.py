"""Tests for legislative-stage classification (app.pipeline.analyze.bill_stage).

classify_bill_stage_from_actions reads Congress.gov's own structured
actionCode/type off each bill's action history — a lookup, not a
classification guess — so these are plain deterministic unit tests, no
embedding model involved.
"""

import pytest

from app.pipeline.analyze.bill_stage import classify_bill_stage_from_actions


def _action(action_type=None, action_code=None, text=""):
    return {"type": action_type, "actionCode": action_code, "text": text}


class TestIsLawShortCircuit:
    def test_is_law_short_circuits_to_enacted_regardless_of_actions(self):
        actions = [_action("IntroReferral", "H11100", "Referred to the Committee on Finance.")]
        assert classify_bill_stage_from_actions(actions, is_law=True) == "ENACTED"

    def test_is_law_short_circuits_even_with_no_actions(self):
        assert classify_bill_stage_from_actions([], is_law=True) == "ENACTED"


class TestFallback:
    def test_no_actions_falls_back_to_introduced(self):
        assert classify_bill_stage_from_actions([], is_law=False) == "INTRODUCED"

    def test_unrecognized_code_and_type_falls_back_to_introduced(self):
        actions = [_action("SomeNewType", "Z99999", "Something Congress.gov added later.")]
        assert classify_bill_stage_from_actions(actions, is_law=False) == "INTRODUCED"


class TestActionCodeLookup:
    """actions[0] (most recent) determines stage via its actionCode."""

    @pytest.mark.parametrize(
        "action_type, action_code, text, expected",
        [
            pytest.param("IntroReferral", "1000", "Introduced in House", "INTRODUCED", id="introduced"),
            pytest.param("IntroReferral", "H11100", "Referred to the House Committee on Ways and Means.", "IN_COMMITTEE", id="referred_to_committee"),
            pytest.param("Committee", "H15001", "Committee Consideration and Mark-up Session Held", "IN_COMMITTEE", id="committee_markup_held"),
            pytest.param("Calendars", "H12410", "Placed on the Union Calendar, Calendar No. 508.", "IN_COMMITTEE", id="placed_on_union_calendar"),
            pytest.param("Floor", "17000", "Passed/agreed to in Senate.", "PASSED_CHAMBER", id="passed_senate"),
            pytest.param("Floor", "8000", "Passed/agreed to in House.", "PASSED_CHAMBER", id="passed_house"),
            pytest.param("Floor", "H14000", "Received in the House.", "IN_OTHER_CHAMBER", id="received_in_house"),
            pytest.param("Floor", "H15000", "Held at the desk.", "IN_OTHER_CHAMBER", id="held_at_the_desk"),
            pytest.param("Floor", "E20000", "Presented to President.", "TO_PRESIDENT", id="presented_to_president"),
            pytest.param("President", "E40000", "Became Public Law No: 119-42.", "ENACTED", id="became_public_law"),
            pytest.param("President", "E30000", "Signed by President.", "ENACTED", id="signed_by_president"),
        ],
    )
    def test_action_code(self, action_type, action_code, text, expected):
        actions = [_action(action_type, action_code, text)]
        assert classify_bill_stage_from_actions(actions) == expected

    def test_ignores_older_actions_uses_latest_only(self):
        # actions[0] is the most recent (Congress.gov returns newest-first);
        # a bill that has since passed shouldn't read as still-introduced.
        actions = [
            _action("Floor", "8000", "Passed/agreed to in House."),
            _action("IntroReferral", "H11100", "Referred to the Committee on Ways and Means."),
            _action("IntroReferral", "1000", "Introduced in House"),
        ]
        assert classify_bill_stage_from_actions(actions) == "PASSED_CHAMBER"


class TestTypeAndTextFallback:
    """Covers actions with no actionCode — real Congress.gov data, not synthetic."""

    @pytest.mark.parametrize(
        "action_type, action_code, text, expected",
        [
            pytest.param("IntroReferral", None, "Read twice and referred to the Committee on the Judiciary.", "IN_COMMITTEE", id="senate_combined_read_and_refer_has_no_action_code"),
            pytest.param("IntroReferral", None, "Received in the Senate and Read twice and referred to the Committee on Finance.", "IN_COMMITTEE", id="senate_receipt_combined_with_referral_has_no_action_code"),
            pytest.param("IntroReferral", None, "Introduced in Senate", "INTRODUCED", id="bare_introduction_with_no_action_code"),
            pytest.param("Committee", None, "Ordered to be reported.", "IN_COMMITTEE", id="committee_type_with_no_action_code"),
            pytest.param("Calendars", None, "Placed on Senate Legislative Calendar under General Orders.", "IN_COMMITTEE", id="calendars_type_with_no_action_code"),
            pytest.param("BecameLaw", None, "Became Public Law No: 119-1.", "ENACTED", id="became_law_type_with_no_action_code"),
            pytest.param("President", "Z00000", "Vetoed by the President.", "VETOED", id="president_type_veto_text"),
            pytest.param("President", "Z00000", "Cleared for White House.", "TO_PRESIDENT", id="president_type_unrecognized_code_defaults_to_to_president"),
            # "Floor" is too coarse to disambiguate on type alone (it covers
            # passage, receipt, and presentation) — an unmapped Floor code
            # must not silently resolve to a specific wrong stage.
            pytest.param("Floor", "Z00000", "Some future Floor action Congress.gov added.", "INTRODUCED", id="unmapped_floor_code_falls_back_to_introduced_not_a_guess"),
        ],
    )
    def test_type_and_text_fallback(self, action_type, action_code, text, expected):
        actions = [_action(action_type, action_code, text)]
        assert classify_bill_stage_from_actions(actions) == expected
