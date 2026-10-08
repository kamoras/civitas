"""The Congress reports, built from a real Digest week (2026-09-21..27)
synced from fixtures, plus roll calls."""

import asyncio
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import RollCall
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
        assert senate["counts"]["billsPassed"] == 3
        assert senate["counts"]["resolutionsPassed"] == 4
        assert senate["counts"]["recordVotes"] == 3
        assert [e["billLabel"] for e in senate["failed"]] == ["H. Con. Res. 89"]
        assert house["minutesInSession"] == 3
        assert r["sentence"] == (
            "The Senate passed 3 bills, agreed to 4 resolutions and took 3 record votes. "
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
        # H.R. 2388 started in the House, so the Senate passing it means both.
        assert [e["billId"] for e in r["passedBothChambers"]] == ["HR.2388"]
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


@pytest.mark.parametrize("text,n", [
    ("3 Coast Guard nominations in the rank of admiral. A routine list in the Coast Guard.", 3),
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
        assert r.json()["chambers"]["senate"]["counts"]["billsPassed"] == 3

    def test_bad_date(self, client):
        assert client.get("/api/congress/day/2026-02-30").status_code == 422
        assert client.get("/api/congress/day/yesterday").status_code == 422
        assert client.get("/api/congress/month/2026-13").status_code == 422

    def test_week_and_month(self, client):
        assert client.get("/api/congress/week/2026-09-24").json()["start"] == "2026-09-21"
        assert client.get("/api/congress/month/2026-09").json()["end"] == "2026-09-30"

    def test_bill_days(self, week_of_sept_21):
        days = cs.bill_days(week_of_sept_21, "S.4668")
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


def test_became_law_from_the_law_list_and_the_bills_just_signed(db_session):
    from app.models import Senator, SponsoredBill
    from app.pipeline.cache import api_cache_set

    # Congress.gov's law list: one law whose sponsor has left Congress (no
    # sponsored-bill row), one whose latest action came after it became law.
    api_cache_set(db_session, "congress", "laws-119", {"laws": {
        "S.550": {"title": "A law", "law": "119-60", "kind": "Public", "date": "2025-12-30"},
        "HR.1043": {"title": "Pine Valley Project Act", "law": "119-68", "kind": "Public", "date": "2025-12-29"},
    }}, normal_ttl_hours=24 * 365)
    db_session.add(Senator(id="s1", name="Sen. Alpha", state="CA", party="D", is_current=True))
    for bill_id, action, day in (
        ("HR.1043", "By Senator Lee from Committee on Energy and Natural Resources filed written report.", "2026-02-11"),
        ("S.766", "Signed by President.", "2025-12-31"),  # signed, no law number listed yet
        ("HR.504", "The Chair directed the Clerk to notify the Senate of the action of the House.", "2025-12-31"),
    ):
        db_session.add(SponsoredBill(senator_id="s1", bill_id=bill_id, title="A bill", congress=119, is_law=True,
                                     latest_action=action, latest_action_date=day, bill_type=bill_id.split(".")[0]))
    db_session.commit()
    week = cs._became_law(db_session, date(2025, 12, 29), date(2026, 1, 4))
    assert [(b["billId"], b["date"]) for b in week] == [("HR.1043", "2025-12-29"), ("S.550", "2025-12-30"), ("S.766", "2025-12-31")]
    assert week[0]["text"] == "Became Public Law No: 119-68."
    # HR.1043 is not listed again in February under its later action.
    assert cs._became_law(db_session, date(2026, 2, 9), date(2026, 2, 15)) == []
