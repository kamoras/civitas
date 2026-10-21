"""Committee meetings and reports findable by committee, words and bill
(services/committee_record.py, GET /api/congress/committees and
/api/congress/committee-record)."""

import json

import pytest

from app.api import congress as congress_api
from app.models import CongressEvent
from app.pipeline.fetch.daily_digest import bill_ids, only_bill_id
from app.services import committee_record

MARKUP = ("Committee ordered favorably reported the following business items: S. 465, to require the "
          "Federal Energy Regulatory Commission to reform the interconnection queue process; S. 1254, to "
          "amend the Aquifer Recharge Flexibility Act; and H.R. 331, to make technical corrections.")
HEARING = ("Subcommittee on Western Hemisphere concluded a hearing to examine Nicaragua's political crisis, "
           "after receiving testimony from witnesses.")
REPORT = ("H.R. 10238, to require the Administrator of the Small Business Administration to disseminate "
          "information relating to cybersecurity matters, with an amendment (H. Rept. 119-851).")


@pytest.fixture
def events(db_session):
    rows = [
        CongressEvent(chamber="senate", date="2026-09-30", kind="committee", seq=1, source="digest",
                      name="Committee on Energy and Natural Resources", text=MARKUP, bill_id="S.465"),
        CongressEvent(chamber="senate", date="2026-09-30", kind="committee", seq=2, source="digest",
                      name="Committee on Foreign Relations", text=HEARING),
        CongressEvent(chamber="house", date="2026-10-13", kind="reported", seq=1, source="digest",
                      name="", text=REPORT, bill_id="HR.10238", pages="Pages H6039-40"),
        # A different Congress's S. 465: never this one's.
        CongressEvent(chamber="senate", date="2024-05-01", kind="committee", seq=1, source="digest",
                      name="Committee on Energy and Natural Resources", text="Committee held a hearing on S. 465.",
                      bill_id="S.465"),
        # A floor-log line naming the bill is not a committee entry.
        CongressEvent(chamber="senate", date="2026-09-30", kind="floor", seq=9, source="floor_log",
                      name="", text="S. 465 considered.", bill_id="S.465"),
        CongressEvent(chamber="senate", date="2026-09-29", kind="passed", seq=1, source="digest",
                      name="", text="Senate passed S. 465.", bill_id="S.465"),
    ]
    db_session.add_all(rows)
    db_session.commit()
    return db_session


def test_every_bill_a_markup_names_is_read():
    assert bill_ids(MARKUP) == ["S.465", "S.1254", "HR.331"]
    assert only_bill_id(" S. 465 ") == "S.465" and only_bill_id("S. 465 hearing") is None


def test_committees_count_their_meetings(events):
    assert committee_record.committees(events) == [
        {"chamber": "senate", "name": "Committee on Energy and Natural Resources", "meetings": 2,
         "latest": "2026-09-30"},
        {"chamber": "senate", "name": "Committee on Foreign Relations", "meetings": 1, "latest": "2026-09-30"},
    ]


def test_a_bill_finds_every_meeting_and_report_naming_it_in_its_congress(events):
    found = committee_record.search(events, bill="S.1254", congress=119)
    assert [e["date"] for e in found["entries"]] == ["2026-09-30"]
    (meeting,) = found["entries"]
    assert meeting["kind"] == "meeting" and meeting["committee"] == "Committee on Energy and Natural Resources"
    assert [b["billLabel"] for b in meeting["bills"]] == ["S. 465", "S. 1254", "H.R. 331"]
    assert meeting["text"] == MARKUP  # as the Digest printed it
    assert committee_record.search(events, bill="S.465", congress=118)["total"] == 1


def test_a_bills_entries_are_counted_in_one_templated_line(events):
    assert committee_record.search(events, bill="S.1254", congress=119)["headline"] == (
        "1 committee meeting in the Daily Digest names S. 1254")
    assert committee_record.search(events, bill="HR.10238")["headline"] == (
        "1 report in the Daily Digest names H.R. 10238")
    assert committee_record.search(events, bill="S.9999")["headline"] == (
        "No committee meeting or report in the Daily Digest names S. 9999")
    assert committee_record.search(events, q="Nicaragua")["headline"] is None


def test_a_typed_bill_number_is_read_as_the_bill(events):
    for typed in ("S. 465", "s.465"):
        found = committee_record.search(events, q=typed, congress=119)
        assert found["bill"] == {"billId": "S.465", "billLabel": "S. 465"}
        assert found["total"] == 1


def test_words_must_all_appear(events):
    assert committee_record.search(events, q="Nicaragua hearing")["total"] == 1
    assert committee_record.search(events, q="Nicaragua markup")["total"] == 0
    # A committee's own name counts as its words.
    assert committee_record.search(events, q="foreign relations")["total"] == 1


def test_a_report_names_its_number_and_no_committee(events):
    (report,) = committee_record.search(events, kind="report")["entries"]
    assert report["report"] == "H. Rept. 119-851" and report["committee"] is None
    assert report["congress"] == 119


def test_the_routes_serve_and_validate(events):
    body = json.loads(congress_api.committee_search(
        chamber="senate", committee="Committee on Foreign Relations", q=None, bill=None, kind=None,
        congress=None, page=1, per_page=20, db=events).body)
    assert body["total"] == 1 and body["entries"][0]["committee"] == "Committee on Foreign Relations"
    assert json.loads(congress_api.committees(db=events).body)["committees"][0]["meetings"] == 2
    with pytest.raises(congress_api.HTTPException):
        congress_api.committee_search(chamber=None, committee=None, q=None, bill="NOT A BILL", kind=None,
                                      congress=None, page=1, per_page=20, db=events)
