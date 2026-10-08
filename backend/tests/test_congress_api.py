"""The Congress reports, built from a real Digest week (2026-09-21..27)
synced from fixtures, plus roll calls."""

import asyncio
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import CongressEvent, RollCall
from app.pipeline import congress_activity as ca
from app.services import congress_service as cs
from tests.congress_activity_helpers import _digest_responses, _fake_get


@pytest.fixture
def week_of_sept_21(db_session, monkeypatch):
    monkeypatch.setattr(ca, "_get", _fake_get(_digest_responses()))
    asyncio.run(ca.sync_digest(None, db_session, date(2026, 9, 24)))
    for number, (yeas, nays, bill) in {242: (77, 23, "S.4668"), 243: (74, 25, "S.4668"),
                                      244: (49, 50, "HCONRES.89")}.items():
        db_session.add(RollCall(chamber="senate", congress=119, session=2, number=number, date="2026-09-24",
                                question="On the question", title="", result="Agreed to", yeas=yeas,
                                nays=nays, bill_id=bill))
    db_session.add(RollCall(chamber="senate", congress=119, session=2, number=241, date="2026-09-23",
                            question="On the Nomination", result="Confirmed", yeas=50, nays=47))
    db_session.commit()
    return db_session


@pytest.fixture
def client(week_of_sept_21):
    app.dependency_overrides[get_db] = lambda: week_of_sept_21
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestDay:
    def test_a_final_day(self, week_of_sept_21):
        r = cs.day_report(week_of_sept_21, date(2026, 9, 24))
        senate, house = r["chambers"]["senate"], r["chambers"]["house"]
        assert senate["status"] == "final"
        assert senate["counts"]["billsPassed"] == 5
        assert senate["counts"]["resolutionsPassed"] == 4
        cleared = {e["billId"]: e["nextStep"] for e in senate["passed"]}
        assert (cleared["S.283"], cleared["S.3257"], cleared["HR.2388"]) == ("both", "house", "both")
        assert senate["counts"]["recordVotes"] == 3
        assert [e["billLabel"] for e in senate["failed"]] == ["H. Con. Res. 89"]
        assert house["minutesInSession"] == 3
        assert r["sentence"] == (
            "The Senate passed 5 bills, agreed to 4 resolutions and took 3 record votes. "
            "The House met for 3 minutes and took no record votes."
        )
        assert r["previousDay"] == "2026-09-23"
        assert senate["nextMeeting"] == "3 p.m., Monday, September 28"

    def test_a_day_with_nothing_recorded(self, week_of_sept_21):
        r = cs.day_report(week_of_sept_21, date(2026, 9, 25))
        assert {c["status"] for c in r["chambers"].values()} == {"no_record"}
        assert r["sentence"] == "No record of the Senate for this day yet. No record of the House for this day yet."

    def test_latest_day(self, week_of_sept_21, monkeypatch):
        monkeypatch.setattr(cs, "eastern_today", lambda: date(2026, 9, 27))
        assert cs.latest_day(week_of_sept_21) == date(2026, 9, 24)


class TestPeriods:
    @pytest.fixture(autouse=True)
    def today(self, freeze_utcnow):
        freeze_utcnow(datetime(2026, 9, 28, 16))

    def test_week(self, week_of_sept_21):
        r = cs.week_report(week_of_sept_21, date(2026, 9, 24))
        assert (r["start"], r["end"]) == ("2026-09-21", "2026-09-27")
        senate = r["totals"]["senate"]
        assert senate["daysInSession"] == 2  # the 23rd by its vote, the 24th by its Digest
        assert senate["recordVotes"] == 4
        # H.R. 2388 started in the House, so the Senate passing it means both;
        # S. 283 and S. 240 cleared when the Senate concurred in the House's
        # amendment (Congress.gov: "Presented to President.", 2026-10-05).
        assert [e["billId"] for e in r["passedBothChambers"]] == ["HR.2388", "S.283", "S.240"]
        assert [e["billId"] for e in r["passedOneChamber"]] == ["S.3257", "S.3258"]
        assert [v["number"] for v in r["closestVotes"]][:2] == [244, 241]
        assert r["previous"] is None  # the week before the first day on record
        assert r["next"] == "2026-09-28"
        assert len(r["days"]) == 7

    def test_month(self, week_of_sept_21):
        r = cs.month_report(week_of_sept_21, 2026, 9)
        assert (r["start"], r["end"]) == ("2026-09-01", "2026-09-30")
        assert [w["start"] for w in r["weeks"]][:2] == ["2026-09-01", "2026-09-07"]
        assert (r["previous"], r["next"]) == (None, None)  # nothing on record in August, nor yet in October
        assert "The Senate met 2 days" in r["sentence"]


@pytest.mark.parametrize("bill,chamber,both", [
    ("HR.2388", "senate", True), ("S.3257", "senate", False), ("SRES.902", "house", False),
    ("HCONRES.89", "senate", True), ("SJRES.1", "house", True), (None, "senate", False),
])
def test_passed_both_chambers(bill, chamber, both):
    assert cs.passed_both_chambers(bill, chamber) is both


@pytest.mark.parametrize("bill,chamber,text,both", [
    # The Record's wordings of concurring in the other chamber's amendment.
    ("HR.5371", "house", "The House concurred in the Senate amendment to H.R. 5371, making continuing appropriations", True),
    ("HR.1", "house", "The House agreed to the motion to concur in the Senate amendment to H.R. 1, to provide", True),
    ("HR.6500", "house", "Concurred in the Senate amendments to H.R. 6500, to extend duty-free treatment", True),
    ("S.1071", "senate", "Senate concurred in the amendment of the House of Representatives to S. 1071", True),
    # Concurring with a further amendment sends it back.
    ("HR.5", "house", "The House concurred in the Senate amendment to H.R. 5 with an amendment.", False),
    ("S.3257", "senate", "Senate passed S. 3257, to require the Administrator", False),
])
def test_a_concurrence_clears_both_chambers(bill, chamber, text, both):
    assert cs.passed_both_chambers(bill, chamber, text) is both


@pytest.mark.parametrize("text,n", [
    ("3 Coast Guard nominations in the rank of admiral.", 3),
    ("1 Marine Corps nomination in the rank of general.", 1),
    # A routine list's size isn't given: counted apart, as a list.
    ("Routine lists in the Air Force, Army, Marine Corps, Navy, and Space Force.", 0),
    ("A routine list in the Coast Guard.", 0),
    ("By 50 yeas to 47 nays (Vote No. EX. 241), Angela Veronica Colmenero, of Texas, to be ...", 1),
    ("Forty-two Air Force nominations in the rank of general.", 42),
])
def test_nominations_in(text, n):
    assert cs.nominations_in(text) == n


def test_bill_label():
    assert cs.bill_label("HCONRES.89") == "H. Con. Res. 89"
    assert cs.bill_label("S.4668") == "S. 4668"
    assert cs.bill_label("PN.1") is None


class TestRoutes:
    def test_day(self, client):
        r = client.get("/api/congress/day/2026-09-24")
        assert r.status_code == 200
        # S. 3257, S. 3258, H.R. 2388 passed; S. 283 and S. 240 cleared by concurrence.
        assert r.json()["chambers"]["senate"]["counts"]["billsPassed"] == 5

    def test_bad_date(self, client):
        assert client.get("/api/congress/day/2026-02-30").status_code == 422
        assert client.get("/api/congress/day/yesterday").status_code == 422
        assert client.get("/api/congress/month/2026-13").status_code == 422

    def test_week_and_month(self, client):
        assert client.get("/api/congress/week/2026-09-24").json()["start"] == "2026-09-21"
        assert client.get("/api/congress/month/2026-09").json()["end"] == "2026-09-30"

    def test_bill_days(self, week_of_sept_21):
        days = cs.bill_days(week_of_sept_21, "S.4668", 119)
        assert [d["date"] for d in days] == ["2026-09-24"]
        assert {e["kind"] for e in days[0]["entries"]} == {"vote"}


class TestDateBounds:
    """Week and month arithmetic ran off the calendar at year 1 and 9999 and
    answered 500; years before the 1st Congress (1789) can't have a record."""

    @pytest.mark.parametrize("url", [
        "/api/congress/week/0001-01-01", "/api/congress/week/9999-12-31",
        "/api/congress/month/0000-01", "/api/congress/month/9999-12",
        "/api/congress/day/1788-12-31", "/api/congress/month/1000-05",
    ])
    def test_out_of_range_is_422(self, client, url):
        assert client.get(url).status_code == 422

    def test_a_date_congress_could_have_met_but_with_no_record_here_is_a_404(self, client):
        # In range for the calendar (the 1st Congress, 1789), but before the
        # first day on record: nothing to report, so no page (record_span).
        assert client.get("/api/congress/month/1789-03").status_code == 404


class TestRecordSpan:
    """A report exists only for dates on record: from the first day either
    chamber is on record as meeting to today. Every other well-formed date
    answered a page of placeholders, and the week and month pages linked
    their neighbours without end, so a crawler walked dates into the next
    year and back toward 1789 (2026-10)."""

    @pytest.fixture(autouse=True)
    def today(self, freeze_utcnow):
        freeze_utcnow(datetime(2026, 9, 28, 16))

    def test_days_outside_the_record_are_404(self, client):
        first = cs.record_span(client.app.dependency_overrides[get_db]())[0]
        assert first == date(2026, 9, 23)  # the Senate's vote that day
        assert client.get("/api/congress/day/2026-09-23").status_code == 200
        assert client.get("/api/congress/day/2026-09-27").status_code == 200  # on record, didn't meet
        assert client.get("/api/congress/day/2026-09-29").status_code == 404  # tomorrow
        assert client.get("/api/congress/day/2027-04-27").status_code == 404
        assert client.get("/api/congress/day/2026-09-22").status_code == 404  # before the first day on record

    def test_a_week_or_month_overlapping_the_record_is_kept(self, client):
        assert client.get("/api/congress/week/2026-09-28").status_code == 200  # this week, so far
        assert client.get("/api/congress/week/2026-10-05").status_code == 404
        assert client.get("/api/congress/month/2026-09").status_code == 200
        assert client.get("/api/congress/month/2026-10").status_code == 404
        assert client.get("/api/congress/month/2026-08").status_code == 404

    def test_neighbours_are_linked_only_when_on_record(self, client):
        week = client.get("/api/congress/week/2026-09-24").json()
        assert week["previous"] is None and week["next"] == "2026-09-28"
        assert client.get("/api/congress/week/2026-09-28").json()["next"] is None
        month = client.get("/api/congress/month/2026-09").json()
        assert month["previous"] is None and month["next"] is None


def test_closest_votes_are_measured_from_what_each_vote_needed():
    cloture_short_by_one = RollCall(chamber="senate", yeas=59, nays=41, majority_requirement="3/5")
    simple_by_two = RollCall(chamber="senate", yeas=51, nays=49, majority_requirement="1/2")
    suspension_clear_by_three = RollCall(chamber="house", yeas=290, nays=140, majority_requirement="2/3")
    unstated = RollCall(chamber="house", yeas=220, nays=210, majority_requirement="")
    assert cs.votes_from_threshold(cloture_short_by_one) == 1
    assert cs.votes_from_threshold(simple_by_two) == 1
    assert round(cs.votes_from_threshold(suspension_clear_by_three), 2) == 3.33
    assert cs.votes_from_threshold(unstated) == 5



class TestNoRecordPublished:
    def test_a_day_behind_the_backfill_with_nothing_had_no_record(self, week_of_sept_21, monkeypatch):
        monkeypatch.setattr(cs, "digest_cursor", lambda db: date(2026, 9, 20))
        r = cs.day_report(week_of_sept_21, date(2026, 9, 19))
        assert {c["status"] for c in r["chambers"].values()} == {"no_record_published"}
        assert r["sentence"].startswith("No Congressional Record was published for this day.")

    def test_a_recent_day_the_last_run_found_absent(self, week_of_sept_21, monkeypatch):
        monkeypatch.setattr(cs, "digest_cursor", lambda db: date(2025, 1, 2))
        monkeypatch.setattr(cs, "eastern_today", lambda: date(2026, 9, 29))
        monkeypatch.setattr(cs, "last_run", lambda db: {"digests": {"2026-09-26": "absent", "2026-09-28": "absent"}})
        assert cs.day_report(week_of_sept_21, date(2026, 9, 26))["chambers"]["senate"]["status"] == "no_record_published"
        # Two days old: GPO may not have posted it yet.
        assert cs.day_report(week_of_sept_21, date(2026, 9, 28))["chambers"]["senate"]["status"] == "no_record"

    def test_a_day_with_a_record_is_unaffected(self, week_of_sept_21, monkeypatch):
        monkeypatch.setattr(cs, "digest_cursor", lambda db: date(2026, 9, 30))
        assert cs.day_report(week_of_sept_21, date(2026, 9, 24))["chambers"]["senate"]["status"] == "final"

    def test_the_week_strip_says_so_too(self, week_of_sept_21, monkeypatch):
        monkeypatch.setattr(cs, "digest_cursor", lambda db: date(2026, 9, 26))
        days = {d["date"]: d["noRecordPublished"] for d in cs.week_report(week_of_sept_21, date(2026, 9, 24))["days"]}
        assert days["2026-09-25"] is True and days["2026-09-24"] is False and days["2026-09-27"] is False


def test_cloture_counts_the_senators_sworn_that_day_not_a_fixed_hundred():
    # A vacancy (99 sworn: 58 yea, 40 nay, 1 not voting) lowers the bar to
    # 59.4, three-fifths of those duly chosen and sworn (Rule XXII).
    with_vacancy = RollCall(chamber="senate", yeas=58, nays=40, present=0, not_voting=1, majority_requirement="3/5")
    assert round(cs.votes_from_threshold(with_vacancy), 2) == 1.4
    full = RollCall(chamber="senate", yeas=58, nays=40, present=0, not_voting=2, majority_requirement="3/5")
    assert cs.votes_from_threshold(full) == 2


def test_bill_days_are_of_the_bills_congress(week_of_sept_21):
    # S. 4668 of the next Congress is another bill.
    db = week_of_sept_21
    db.add(RollCall(chamber="senate", congress=120, session=1, number=5, date="2027-02-02",
                    question="On Passage", result="Passed", yeas=60, nays=40, bill_id="S.4668"))
    db.add(CongressEvent(chamber="senate", date="2027-02-02", kind="passed", seq=0, name="", text="Senate passed S. 4668",
                         bill_id="S.4668", source="digest"))
    db.commit()
    assert [d["date"] for d in cs.bill_days(db, "S.4668", 119)] == ["2026-09-24"]
    assert [d["date"] for d in cs.bill_days(db, "S.4668", 120)] == ["2027-02-02"]


def test_routine_lists_are_named_not_counted():
    counts = {"billsPassed": 0, "resolutionsPassed": 0, "recordVotes": 0, "confirmed": 7, "confirmedLists": 1}
    day = {"status": "final", "counts": counts, "minutesInSession": None, "adjournmentText": ""}
    assert cs._chamber_sentence("senate", day) == "The Senate confirmed 7 nominations and routine lists."
    counts.update(confirmed=0)
    assert cs._chamber_sentence("senate", day) == "The Senate confirmed routine lists of nominations."
