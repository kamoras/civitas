"""Tests for legislative-stage classification (app.pipeline.analyze.bill_stage).

classify_bill_stage_from_actions reads Congress.gov's own structured
actionCode/type off each bill's action history — a lookup, not a
classification guess — so these are plain deterministic unit tests, no
embedding model involved.
"""

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

    def test_introduced(self):
        actions = [_action("IntroReferral", "1000", "Introduced in House")]
        assert classify_bill_stage_from_actions(actions) == "INTRODUCED"

    def test_referred_to_committee(self):
        actions = [_action("IntroReferral", "H11100", "Referred to the House Committee on Ways and Means.")]
        assert classify_bill_stage_from_actions(actions) == "IN_COMMITTEE"

    def test_committee_markup_held(self):
        actions = [_action("Committee", "H15001", "Committee Consideration and Mark-up Session Held")]
        assert classify_bill_stage_from_actions(actions) == "IN_COMMITTEE"

    def test_placed_on_union_calendar(self):
        actions = [_action("Calendars", "H12410", "Placed on the Union Calendar, Calendar No. 508.")]
        assert classify_bill_stage_from_actions(actions) == "IN_COMMITTEE"

    def test_passed_senate(self):
        actions = [_action("Floor", "17000", "Passed/agreed to in Senate.")]
        assert classify_bill_stage_from_actions(actions) == "PASSED_CHAMBER"

    def test_passed_house(self):
        actions = [_action("Floor", "8000", "Passed/agreed to in House.")]
        assert classify_bill_stage_from_actions(actions) == "PASSED_CHAMBER"

    def test_received_in_house(self):
        actions = [_action("Floor", "H14000", "Received in the House.")]
        assert classify_bill_stage_from_actions(actions) == "IN_OTHER_CHAMBER"

    def test_held_at_the_desk(self):
        actions = [_action("Floor", "H15000", "Held at the desk.")]
        assert classify_bill_stage_from_actions(actions) == "IN_OTHER_CHAMBER"

    def test_presented_to_president(self):
        actions = [_action("Floor", "E20000", "Presented to President.")]
        assert classify_bill_stage_from_actions(actions) == "TO_PRESIDENT"

    def test_became_public_law(self):
        actions = [_action("President", "E40000", "Became Public Law No: 119-42.")]
        assert classify_bill_stage_from_actions(actions) == "ENACTED"

    def test_signed_by_president(self):
        actions = [_action("President", "E30000", "Signed by President.")]
        assert classify_bill_stage_from_actions(actions) == "ENACTED"

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

    def test_senate_combined_read_and_refer_has_no_action_code(self):
        actions = [_action("IntroReferral", None, "Read twice and referred to the Committee on the Judiciary.")]
        assert classify_bill_stage_from_actions(actions) == "IN_COMMITTEE"

    def test_senate_receipt_combined_with_referral_has_no_action_code(self):
        actions = [_action(
            "IntroReferral", None,
            "Received in the Senate and Read twice and referred to the Committee on Finance.",
        )]
        assert classify_bill_stage_from_actions(actions) == "IN_COMMITTEE"

    def test_bare_introduction_with_no_action_code(self):
        actions = [_action("IntroReferral", None, "Introduced in Senate")]
        assert classify_bill_stage_from_actions(actions) == "INTRODUCED"

    def test_committee_type_with_no_action_code(self):
        actions = [_action("Committee", None, "Ordered to be reported.")]
        assert classify_bill_stage_from_actions(actions) == "IN_COMMITTEE"

    def test_calendars_type_with_no_action_code(self):
        actions = [_action("Calendars", None, "Placed on Senate Legislative Calendar under General Orders.")]
        assert classify_bill_stage_from_actions(actions) == "IN_COMMITTEE"

    def test_became_law_type_with_no_action_code(self):
        actions = [_action("BecameLaw", None, "Became Public Law No: 119-1.")]
        assert classify_bill_stage_from_actions(actions) == "ENACTED"

    def test_president_type_veto_text(self):
        actions = [_action("President", "Z00000", "Vetoed by the President.")]
        assert classify_bill_stage_from_actions(actions) == "VETOED"

    def test_president_type_unrecognized_code_defaults_to_to_president(self):
        actions = [_action("President", "Z00000", "Cleared for White House.")]
        assert classify_bill_stage_from_actions(actions) == "TO_PRESIDENT"

    def test_unmapped_floor_code_falls_back_to_introduced_not_a_guess(self):
        # "Floor" is too coarse to disambiguate on type alone (it covers
        # passage, receipt, and presentation) — an unmapped Floor code
        # must not silently resolve to a specific wrong stage.
        actions = [_action("Floor", "Z00000", "Some future Floor action Congress.gov added.")]
        assert classify_bill_stage_from_actions(actions) == "INTRODUCED"
