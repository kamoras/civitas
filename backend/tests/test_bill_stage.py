"""Tests for legislative-stage classification (app.pipeline.analyze.bill_stage).

classify_bill_stage_from_actions reads Congress.gov's own structured
actionCode/type off each bill's action history — a lookup, not a
classification guess — so these are plain deterministic unit tests, no
embedding model involved.
"""

import pytest

from app.config_definitions import BillStage
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
            pytest.param("IntroReferral", "H11100", "Referred to the House Committee on Ways and Means.", "REFERRED", id="referred_to_committee"),
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
            pytest.param("IntroReferral", None, "Read twice and referred to the Committee on the Judiciary.", "REFERRED", id="senate_combined_read_and_refer_has_no_action_code"),
            pytest.param("IntroReferral", None, "Received in the Senate and Read twice and referred to the Committee on Finance.", "REFERRED", id="senate_receipt_combined_with_referral_has_no_action_code"),
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


class TestMaxOverHistory:
    """2026-07 fix: stage = furthest reached, not latest action."""

    def test_second_chamber_referral_does_not_regress(self):
        # Newest-first: passed the House, then referred in the Senate —
        # the normal path for EVERY bill that passes its origin chamber.
        # Latest-action classification regressed this to IN_COMMITTEE,
        # docking LES credit exactly when the bill advanced.
        actions = [
            {"type": "IntroReferral",
             "text": "Received in the Senate and Read twice and referred to the Committee on Finance."},
            {"actionCode": "8000", "type": "Floor", "text": "Passed/agreed to in House."},
            {"actionCode": "H11100", "type": "IntroReferral", "text": "Referred to committee."},
            {"actionCode": "1000", "type": "IntroReferral", "text": "Introduced in House"},
        ]
        assert classify_bill_stage_from_actions(actions) == BillStage.IN_OTHER_CHAMBER

    def test_failed_override_does_not_regress_veto(self):
        actions = [
            {"type": "Floor", "text": "Two-thirds not in favor, override of the veto failed."},
            {"type": "President", "text": "Vetoed by President."},
            {"actionCode": "28000", "type": "President", "text": "Presented to President."},
            {"actionCode": "17000", "type": "Floor", "text": "Passed/agreed to in Senate."},
        ]
        assert classify_bill_stage_from_actions(actions) == BillStage.VETOED

    def test_mere_referral_with_nothing_else_stays_referred(self):
        # 2026-07 fix: automatic referral alone (no hearing, markup, or
        # report) is REFERRED, not IN_COMMITTEE — see module docstring.
        actions = [
            {"actionCode": "H11100", "type": "IntroReferral", "text": "Referred to committee."},
            {"actionCode": "1000", "type": "IntroReferral", "text": "Introduced in House"},
        ]
        assert classify_bill_stage_from_actions(actions) == BillStage.REFERRED

    def test_genuine_committee_action_after_referral_outranks_it(self):
        actions = [
            {"type": "Committee", "text": "Ordered to be reported."},
            {"actionCode": "H11100", "type": "IntroReferral", "text": "Referred to committee."},
            {"actionCode": "1000", "type": "IntroReferral", "text": "Introduced in House"},
        ]
        assert classify_bill_stage_from_actions(actions) == BillStage.IN_COMMITTEE


class TestReferredVsInCommitteeRealData:
    """2026-07 audit of live production data: every one of a sample of
    Senate bills classified IN_COMMITTEE under the old scheme had exactly
    two actions ever — introduction and the automatic referral — nothing
    else (real example below, verbatim). Compared against real Senate
    bills that DID get genuine committee action (also verbatim, from the
    same audit) to confirm the two are now distinguishable."""

    def test_real_senate_bill_with_only_automatic_referral(self):
        # Real action history for a Blackburn-sponsored bill stuck with
        # zero engagement beyond the automatic first step.
        actions = [
            {"actionCode": None, "type": "IntroReferral",
             "text": "Read twice and referred to the Committee on the Judiciary."},
            {"actionCode": "10000", "type": "IntroReferral", "text": "Introduced in Senate"},
        ]
        assert classify_bill_stage_from_actions(actions) == BillStage.REFERRED

    def test_real_senate_bill_reported_out_of_committee(self):
        # Real action history for a bill that was actually reported out
        # of committee and calendared — genuine, non-automatic progress.
        actions = [
            {"actionCode": None, "type": "Calendars",
             "text": "Placed on Senate Legislative Calendar under General Orders. Calendar No. 43."},
            {"actionCode": "14000", "type": "Committee",
             "text": "Committee on the Judiciary. Reported by Senator Grassley with an amendment."},
            {"actionCode": None, "type": "Committee",
             "text": "Committee on the Judiciary. Ordered to be reported with an amendment favorably."},
            {"actionCode": None, "type": "IntroReferral",
             "text": "Read twice and referred to the Committee on the Judiciary."},
            {"actionCode": "10000", "type": "IntroReferral", "text": "Introduced in Senate"},
        ]
        assert classify_bill_stage_from_actions(actions) == BillStage.IN_COMMITTEE

    def test_real_senate_bill_discharged_by_unanimous_consent(self):
        actions = [
            {"actionCode": "14500", "type": "Committee",
             "text": "Senate Committee on Energy and Natural Resources discharged by Unanimous Consent."},
            {"actionCode": None, "type": "IntroReferral",
             "text": "Read twice and referred to the Committee on Energy and Natural Resources."},
            {"actionCode": "10000", "type": "IntroReferral", "text": "Introduced in Senate"},
        ]
        assert classify_bill_stage_from_actions(actions) == BillStage.IN_COMMITTEE
