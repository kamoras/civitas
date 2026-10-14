"""An issue's contact and record surface: who decides, a state's members
with their phones and their votes on the issue's roll calls, the bill a
roll-call issue is about, members named in the reporting, and the
anonymous count of action taps."""

import inspect
from datetime import datetime
import json

import pytest
from fastapi import HTTPException, Response

from app.api import visits
from app.api.action import _build_issue_response, bill_decider, get_issue_members
from app.api.admin import admin_action_taps
from app.api.visits import track_action
from app.issue_ids import to_public_id
from app.models import ActionIssue, ExploreDocument, Representative, RollCall, RollCallPosition, Senator, SponsoredBill
from app.pipeline.analyze.action_center import _find_related_senators
from app.pipeline.analyze.early_signal import _vote_bill_entries
from tests.visits_helpers import _drain_queue_and_write

VOTE_URL = "https://clerk.house.gov/evs/2026/roll314.xml"


@pytest.mark.parametrize("bill_id, stage, body", [
    ("S.12", "REFERRED", "A Senate committee"),
    ("HR.9", "ON_FLOOR", "The full House"),
    ("HR.9", "PASSED_CHAMBER", "The Senate"),
    ("S.12", "IN_OTHER_CHAMBER", "The House"),
    ("HR.9", "PASSED_BOTH", "Congress, then the President"),
    ("SJRES.3", "TO_PRESIDENT", "The President"),
    # A simple resolution never leaves its chamber; a concurrent one is
    # never signed.
    ("HRES.4", "PASSED_CHAMBER", None),
    ("HCONRES.5", "TO_PRESIDENT", None),
    ("S.12", "ENACTED", None),
])
def test_who_acts_next_follows_the_bills_type_and_stage(bill_id, stage, body):
    assert bill_decider(bill_id, stage) == body


def _roll_call_issue(db):
    issue = ActionIssue(
        date="2026-09-30", rank=1, title="House vote on S 2403: Passed, 401-14", summary="s",
        is_current=True, status="developing", source_type="house_roll_call_vote",
        primary_source_url=VOTE_URL,
        # As early_signal stored it before 2026-10-14: no link, no Congress.
        related_bill_ids=json.dumps([{"name": "S. 2403", "id": "S.2403"}]),
    )
    db.add(issue)
    db.flush()
    return issue


def test_a_roll_call_issue_lists_its_bill_with_who_acts_next(db_session):
    db_session.add(Senator(id="ann-ash", name="Ann Ash", state="VT", party="D", is_current=True))
    db_session.flush()
    db_session.add(SponsoredBill(senator_id="ann-ash", bill_id="S.2403", title="A bill", congress=119,
                                 stage="IN_OTHER_CHAMBER", latest_action="Passed House", latest_action_date="2026-09-30"))
    issue = _roll_call_issue(db_session)

    body = _build_issue_response(issue, db_session)

    (bill,) = body["relatedBills"]
    assert bill["url"] == "https://www.congress.gov/bill/119th-congress/senate-bill/2403"
    assert (bill["stageName"], bill["latestAction"]) == ("In Other Chamber", "Passed House")
    assert body["deciders"] == [{"body": "The House", "basis": "S. 2403 · In Other Chamber"}]


def test_early_signal_records_the_bill_with_its_link():
    assert _vote_bill_entries({"congress": 119}, "S.2403") == [{
        "name": "S. 2403", "id": "S.2403", "congress": 119,
        "url": "https://www.congress.gov/bill/119th-congress/senate-bill/2403",
    }]
    assert _vote_bill_entries({"congress": 119}, None) == []


def test_an_open_comment_period_names_its_agency_as_the_decider(db_session, monkeypatch):
    monkeypatch.setattr("app.api.action.comment_period_today", lambda: "2026-10-14")
    doc = ExploreDocument(doc_type="proposed_rule", source="federal_register", title="A proposed rule", date="2026-10-01",
                          agency_name="Environmental Protection Agency", comment_url="https://example.gov/c",
                          comments_close_on="2026-10-30")
    db_session.add(doc)
    db_session.flush()
    issue = ActionIssue(date="2026-10-14", rank=1, title="t", summary="s", is_current=True,
                        related_explore_ids=json.dumps([doc.id]))
    db_session.add(issue)
    db_session.flush()

    body = _build_issue_response(issue, db_session)

    assert body["deciders"] == [{"body": "Environmental Protection Agency",
                                 "basis": "taking public comments until October 30"}]
    assert body["relatedExploreDocs"][0]["agencyName"] == "Environmental Protection Agency"


def test_a_states_members_with_phones_and_their_votes_on_the_issue(db_session):
    db_session.add_all([
        Senator(id="ann-ash", name="Ann Ash", state="VT", party="D", is_current=True,
                office_phone="(202) 224-0001", contact_form_url="https://ash.senate.gov/contact"),
        Senator(id="old-oak", name="Old Oak", state="VT", party="R", is_current=False),
        Representative(id="bo-birch", name="Bo Birch", state="VT", party="I", district=0, is_current=True,
                       bioguide_id="B000001", office_phone="(202) 225-0002"),
        Representative(id="cy-cedar", name="Cy Cedar", state="NH", party="R", district=1, is_current=True,
                       bioguide_id="C000001"),
    ])
    issue = _roll_call_issue(db_session)
    rc = RollCall(chamber="house", congress=119, session=2, number=314, date="2026-09-30",
                  question="On Passage", result="Passed", bill_id="S.2403", source_url=VOTE_URL)
    db_session.add(rc)
    db_session.flush()
    db_session.add_all([
        RollCallPosition(roll_call_id=rc.id, member_id="B000001", last_name="Birch", state="VT", position="Yea"),
        RollCallPosition(roll_call_id=rc.id, member_id="C000001", last_name="Cedar", state="NH", position="Nay"),
    ])
    db_session.commit()

    out = get_issue_members(to_public_id(issue.id), Response(), state="vt", db=db_session)

    assert out.state == "VT"
    assert [(v.number, v.bill_label) for v in out.votes] == [(314, "S. 2403")]
    assert [(m.id, m.chamber, m.office_phone, m.positions) for m in out.members] == [
        ("ann-ash", "senate", "(202) 224-0001", {}),
        ("bo-birch", "house", "(202) 225-0002", {"house-119-2-314": "Yea"}),
    ]
    assert _build_issue_response(issue, db_session)["voteCount"] == 1


def test_members_refuses_what_is_not_a_state(db_session):
    issue = _roll_call_issue(db_session)
    with pytest.raises(HTTPException) as e:
        get_issue_members(to_public_id(issue.id), Response(), state="ZZ", db=db_session)
    assert e.value.status_code == 404


def test_members_named_in_the_reporting_by_full_name_only(db_session):
    """The issue's text names nobody; its article does. A full name there
    links the member, a bare surname there does not."""
    db_session.add_all([
        Senator(id="ann-ash", name="Ann Ash", state="VT", party="D", is_current=True),
        Senator(id="dee-dogwood", name="Dee Dogwood", state="NH", party="R", is_current=True),
    ])
    db_session.commit()

    found = _find_related_senators(
        "Senators call for a reversal", "Senators call for a reversal.", [], db_session,
        article_texts=["Senators call for a reversal. Sen. Ann Ash led the letter; Dogwood signed it."],
    )

    assert [(m["id"], m["match_reason"]) for m in found] == [("ann-ash", "named in the reporting")]


async def test_a_tap_is_counted_with_nothing_about_who_tapped(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 14, 12))
    issue = ActionIssue(date="2026-10-14", rank=1, title="An issue", summary="s", is_current=True)
    db_session.add(issue)
    db_session.commit()
    public_id = to_public_id(issue.id)

    await track_action(issue=public_id, kind="call")
    await track_action(issue=public_id, kind="call")
    await track_action(issue="not-an-issue", kind="call")
    assert _drain_queue_and_write(db_session) == 2

    out = admin_action_taps(days=1, db=db_session, main_db=db_session)
    assert out["totals"]["call"] == 2
    assert out["issues"] == [{"publicId": public_id, "title": "An issue",
                              **{k: (2 if k == "call" else 0) for k in out["kinds"]}}]
    # The endpoint takes no Request: it cannot read an address or a header.
    assert set(inspect.signature(track_action).parameters) == {"issue", "kind"}
    assert visits.ACTION_TAP_KINDS == ("pick", "call", "write", "copy", "comment", "committee", "bill")
