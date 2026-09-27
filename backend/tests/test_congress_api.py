"""The Congress reports, built from a real Digest week (2026-09-21..27)
synced from fixtures, plus roll calls."""

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import RollCall
from app.pipeline import congress_activity as ca
from app.services import congress_service as cs
from tests.test_congress_activity import FIX, _digest_responses, _fake_get


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
        assert r["previous"] == "2026-09-14"
        assert len(r["days"]) == 7

    def test_month(self, week_of_sept_21):
        r = cs.month_report(week_of_sept_21, 2026, 9)
        assert (r["start"], r["end"]) == ("2026-09-01", "2026-09-30")
        assert [w["start"] for w in r["weeks"]][:2] == ["2026-09-01", "2026-09-07"]
        assert (r["previous"], r["next"]) == ("2026-08", "2026-10")
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

    def test_fixtures_exist(self):
        assert (FIX / "daily_digest").is_dir()
