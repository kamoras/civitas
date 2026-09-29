"""A member sworn in mid-Congress is compared with the share of it they served (v6.23).

Legislative Effectiveness compares a member's bill credit for the current
Congress with the median member's, measured on members who have served all of
it so far. A special-election arrival has had only part of that time to
sponsor and advance bills: in September 2026, 11 current House members had
been sworn in after the 119th Congress convened, six of them in 2026. The bar
is prorated by the share of the Congress each has served.
"""
import copy

from app.models import Representative
from app.pipeline.analyze.score_calculator import (
    _les_component_score,
    compute_les_reference,
    congress_exposure,
)
from app.pipeline.fetch.house_clerk import parse_sworn_dates
from app.services.representative_service import upsert_representative


def _ref(pinned, measured_on="2026-09-29"):
    ref = copy.deepcopy(pinned)
    ref["house"]["measured_on"] = measured_on
    return ref


def _bills(n, law=0):
    return [
        {"billType": "hr", "congress": 119, "isLaw": i < law, "stage": "ENACTED" if i < law else "INTRODUCED"}
        for i in range(n)
    ]


class TestCongressExposure:
    def test_a_member_seated_when_the_congress_convened_served_all_of_it(self):
        assert congress_exposure("2025-01-03", 119, "2026-09-29") == 1.0

    def test_unknown_dates_change_nothing(self):
        assert congress_exposure(None, 119, "2026-09-29") == 1.0
        assert congress_exposure("not a date", 119, "2026-09-29") == 1.0
        assert congress_exposure("2026-06-10", None, "2026-09-29") == 1.0

    def test_a_special_election_arrival_served_a_share_of_it(self):
        # 111 of the Congress's 634 days so far.
        assert abs(congress_exposure("2026-06-10", 119, "2026-09-29") - 111 / 634) < 1e-9

    def test_the_share_is_capped_at_the_end_of_the_congress(self):
        assert abs(congress_exposure("2026-01-03", 119, "2030-01-01") - 0.5) < 0.01

    def test_a_member_sworn_after_the_measurement_served_none_of_it(self):
        assert congress_exposure("2026-10-01", 119, "2026-09-29") == 0.0


class TestProratedBar:
    def test_the_same_record_scores_higher_for_a_later_arrival(self, pinned_les_reference):
        ref = _ref(pinned_les_reference)
        bills = _bills(4)
        full, _ = _les_component_score(bills, "D", 1, ref, chamber="house")
        late, detail = _les_component_score(bills, "D", 1, ref, chamber="house", sworn_date="2026-06-10")
        assert late > full
        assert "prorated to the 18% of this Congress served since 2026-06-10" in detail

    def test_a_first_bill_beats_none_for_a_recent_arrival(self, pinned_les_reference):
        # Inaction never beats an attempt: a member four weeks in with one
        # bill must not read below the same member with no bills.
        ref = _ref(pinned_les_reference)
        none, _ = _les_component_score([], "D", 1, ref, chamber="house", sworn_date="2026-09-02")
        one, _ = _les_component_score(_bills(1), "D", 1, ref, chamber="house", sworn_date="2026-09-02")
        assert one >= none
        # With about 4% of the Congress to show for, no bills is near neutral.
        assert none > 45

    def test_a_member_seated_at_the_start_is_unchanged(self, pinned_les_reference):
        ref = _ref(pinned_les_reference)
        bills = _bills(4, law=1)
        assert (
            _les_component_score(bills, "D", 1, ref, chamber="house", sworn_date="2025-01-03")
            == _les_component_score(bills, "D", 1, ref, chamber="house")
        )

    def test_the_reference_records_when_it_was_measured(self):
        members = [(_bills(3), "D")] * 20 + [(_bills(5), "R")] * 20
        ref = compute_les_reference(members, 119, "R")
        assert ref["measured_on"]


_MEMBER_DATA = b"""<?xml version="1.0"?>
<MemberData><members>
 <member><statedistrict>AK00</statedistrict><member-info><bioguideID>B001323</bioguideID>
  <sworn-date date="20250103">January  3, 2025</sworn-date></member-info></member>
 <member><statedistrict>CA01</statedistrict><member-info><bioguideID>G000607</bioguideID>
  <sworn-date date="20260610">June 10, 2026</sworn-date></member-info></member>
 <member><statedistrict>FL20</statedistrict><member-info><bioguideID></bioguideID>
  <sworn-date date=""/></member-info></member>
</members></MemberData>"""


def test_the_clerk_list_gives_each_members_sworn_in_date():
    assert parse_sworn_dates(_MEMBER_DATA) == {"B001323": "2025-01-03", "G000607": "2026-06-10"}
    assert parse_sworn_dates(b"<not xml") == {}


def test_a_run_that_could_not_read_the_list_keeps_the_stored_date(db_session):
    base = {"id": "ca-1", "name": "Rep", "state": "CA", "district": 1, "party": "R"}
    upsert_representative(db_session, {**base, "swornDate": "2026-06-10"})
    upsert_representative(db_session, base)
    assert db_session.get(Representative, "ca-1").sworn_date == "2026-06-10"
