"""Voter deadlines: where each stands is read on the state's own clock."""

import importlib.util
import json
import pathlib
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import voter_calendar
from app.state_names import STATE_NAMES

ELECTION = date(2026, 11, 3)


def _src(url="https://sos.example.gov/vote"):
    return {"type": "official", "publisher": "Secretary of State", "url": url, "quote": "q"}


@pytest.fixture
def calendar(monkeypatch, tmp_path):
    """Point the module at a calendar holding `states`."""
    def write(states, election="2026-11-03"):
        for entry in states.values():
            for item in entry.get("items") or []:
                item.setdefault("verification", {"status": "verified", "checkedOn": "2026-10-13"})
        path = tmp_path / "cal.json"
        path.write_text(json.dumps({"electionDate": election, "states": states}))
        monkeypatch.setattr(voter_calendar, "_PATH", path)
        monkeypatch.setattr(voter_calendar, "_cache", None)
    return write


WA = {
    "timeZone": "America/Los_Angeles",
    "items": [
        {"kind": "registration", "method": "online", "rule": "8 days before.", "daysBefore": 8,
         "date": "2026-10-26", "countedDate": "2026-10-26", "source": _src()},
        {"kind": "registration", "method": "in_person", "rule": "Until 8 p.m. on Election Day.",
         "date": "2026-11-03", "time": "20:00", "source": _src()},
        {"kind": "ballot_return", "method": "mail", "basis": "postmarked", "rule": "Postmarked by Election Day.",
         "date": "2026-11-03", "endDate": "2026-11-13", "source": _src()},
    ],
}


def _utc(*args):
    return datetime(*args)


def test_a_deadline_closes_at_the_end_of_the_day_where_the_voter_is(calendar):
    """11 p.m. Pacific on Oct 26 is already Oct 27 in UTC: still open, today."""
    calendar({"WA": WA})
    late = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 10, 27, 6, 30))
    online = late["items"][0]
    assert late["localToday"] == "2026-10-26"
    assert online["status"] == "open" and online["daysLeft"] == 0 and online["countdown"] == "today"
    assert online["sentence"] == "Online registration closes Oct 26"
    after = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 10, 27, 7, 30))
    assert after["items"][0]["status"] == "closed"
    assert after["items"][0]["sentence"] == "Online registration closed Oct 26"


def test_a_countdown_counts_local_days(calendar):
    calendar({"WA": WA})
    cal = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 10, 20, 18))
    assert cal["next"]["kind"] == "registration" and cal["next"]["method"] == "online"
    assert (cal["next"]["daysLeft"], cal["next"]["countdown"]) == (6, "6 days")
    assert cal["next"]["daysBefore"] == 8
    assert cal["registrationSummary"] == (
        "Online registration closes Oct 26 (6 days left); In-person registration closes Nov 3, 8 p.m. (14 days left)."
    )


def test_a_stated_hour_is_the_deadline(calendar):
    """Registration in person until 8 p.m. Pacific on Election Day."""
    calendar({"WA": WA})
    before = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 11, 4, 3, 59))
    assert before["next"]["method"] == "in_person"
    assert before["next"]["sentence"] == "In-person registration closes Nov 3, 8 p.m."
    after = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 11, 4, 4, 1))
    assert all(i["status"] == "closed" for i in after["items"] if i["kind"] == "registration")


def test_a_passed_deadline_says_so_and_names_what_is_still_open(calendar):
    calendar({"WA": WA})
    cal = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 10, 30, 18))
    assert cal["registrationSummary"] == (
        "Online registration closed Oct 26. Still open: "
        "In-person registration closes Nov 3, 8 p.m. (4 days left)."
    )
    # Never dropped from the list.
    assert [i["status"] for i in cal["items"]][:1] == ["closed"]
    done = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 11, 5))
    assert done["registrationSummary"].endswith("No way to register for this election is still open.")
    assert done["next"] is None


def test_a_postmark_deadline_names_the_arrival_date(calendar):
    calendar({"WA": WA})
    cal = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 11, 1, 18))
    ret = next(i for i in cal["items"] if i["kind"] == "ballot_return")
    assert ret["sentence"] == "Mail ballots must be postmarked by Nov 3 and arrive by Nov 13"


def test_an_early_voting_window_opens_runs_and_ends(calendar):
    calendar({"TX": {"timeZone": "America/Denver", "items": [
        {"kind": "early_voting", "rule": "Oct 19 to Oct 30.", "date": "2026-10-19", "endDate": "2026-10-30",
         "source": _src()}]}})
    def at(*a):
        return voter_calendar.calendar_for_state("TX", ELECTION, _utc(*a))["items"][0]
    soon = at(2026, 10, 13, 18)
    assert (soon["status"], soon["countdown"]) == ("upcoming", "6 days")
    assert soon["sentence"] == "Early in-person voting opens Oct 19, runs to Oct 30"
    now = at(2026, 10, 28, 18)
    assert (now["status"], now["sentence"]) == ("open", "Early in-person voting is open until Oct 30")
    # Midnight Mountain on Oct 31 is 06:00 UTC: El Paso's last day has ended.
    assert at(2026, 10, 31, 5, 59)["status"] == "open"
    assert at(2026, 10, 31, 6, 1)["status"] == "closed"


def test_the_frozen_backend_clock_is_the_default(calendar, freeze_utcnow):
    calendar({"WA": WA})
    freeze_utcnow(datetime(2026, 10, 27, 7, 30))
    assert voter_calendar.calendar_for_state("WA", ELECTION)["items"][0]["status"] == "closed"


def test_no_calendar_for_another_election(calendar):
    calendar({"WA": WA})
    assert voter_calendar.calendar_for_state("WA", date(2028, 11, 7), _utc(2026, 10, 1)) is None
    assert voter_calendar.calendar_for_state("OR", ELECTION, _utc(2026, 10, 1)) is None


def test_next_deadlines_are_soonest_first(calendar):
    calendar({"WA": WA, "AZ": {"timeZone": "America/Phoenix", "items": [
        {"kind": "absentee_request", "method": "any", "basis": "received", "rule": "11 days before.",
         "date": "2026-10-23", "time": "17:00", "source": _src()}]}})
    rows = voter_calendar.next_deadlines(ELECTION, _utc(2026, 10, 20, 18))
    assert [r["state"] for r in rows] == ["AZ", "WA"]
    assert rows[0]["sentence"] == "Mail ballot requests close Oct 23, 5 p.m. (must arrive)"
    assert rows[0]["countdown"] == "3 days"


def test_noon_and_an_online_deadline_read_plainly(calendar):
    calendar({"MT": {"timeZone": "America/Denver", "items": [
        {"kind": "absentee_request", "method": "any", "basis": "received", "rule": "Noon the day before.",
         "date": "2026-11-02", "time": "12:00", "source": _src()},
        {"kind": "registration", "method": "online", "basis": "received", "rule": "Online by Oct 13.",
         "date": "2026-10-13", "source": _src()}]}})
    cal = voter_calendar.calendar_for_state("MT", ELECTION, _utc(2026, 10, 12, 18))
    assert [i["sentence"] for i in cal["items"]] == [
        "Online registration closes Oct 13", "Mail ballot requests close Nov 2, noon (must arrive)",
    ]


def test_a_state_read_from_no_official_source_lists_no_dates_and_says_so(calendar):
    calendar({"WA": WA, "NY": {"timeZone": "America/New_York", "items": [], "unread": True}})
    cal = voter_calendar.calendar_for_state("NY", ELECTION, _utc(2026, 10, 20, 18))
    assert cal["items"] == [] and cal["next"] is None and cal["registrationSummary"] is None
    assert cal["unread"] == (
        "We couldn't read New York's voter deadlines from its election board, so we don't list any."
    )
    assert voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 10, 20, 18))["unread"] is None
    # Nor in the homepage strip.
    assert [r["state"] for r in voter_calendar.next_deadlines(ELECTION, _utc(2026, 10, 20, 18))] == ["WA"]


# ── The generator ────────────────────────────────────────────────

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "build_election_calendar.py"


@pytest.fixture
def generator():
    spec = importlib.util.spec_from_file_location("build_election_calendar", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Pages:
    """A client whose GET answers each URL with (status, html)."""

    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kwargs):
        status, body = self.pages[url]
        return httpx.Response(status, content=body.encode(), headers={"content-type": "text/html"},
                              request=httpx.Request("GET", url))


def _sources(states):
    return {"electionDate": "2026-11-03", "states": states}


def _reg(url, quote):
    return {"kind": "registration", "method": "online", "rule": "8 days before.", "daysBefore": 8,
            "source": {"type": "official", "publisher": "SOS", "url": url, "quote": quote}}


def test_the_generator_writes_a_verified_item(generator):
    page = "<p>Register online at least 8 days before the election.</p>"
    out = generator.build(_sources({"WA": {"timeZone": "America/Los_Angeles", "items": [
        _reg("https://sos.example.gov/a", "at least 8 days before the election")]}}),
        _Pages({"https://sos.example.gov/a": (200, page)}), None)
    item = out["states"]["WA"]["items"][0]
    assert item["date"] == "2026-10-26" and item["verification"]["status"] == "verified"


@pytest.mark.parametrize("pages, quote", [
    ({"https://sos.example.gov/a": (200, "<p>Some other page.</p>")}, "at least 8 days before"),
    ({"https://sos.example.gov/a": (403, "<h1>Access Denied</h1>")}, "at least 8 days before"),
    ({"https://sos.example.gov/a": (200, "<p>at least 8 days before</p>")}, ""),
])
def test_the_generator_refuses_an_item_it_could_not_verify(generator, pages, quote):
    """A quote missing from its page, a page that refuses the read, and an item with
    no quote at all: nothing is written from memory."""
    with pytest.raises(SystemExit):
        generator.build(_sources({"NY": {"timeZone": "America/New_York", "items": [
            _reg("https://sos.example.gov/a", quote)]}}), _Pages(pages), None)


def test_the_generator_marks_a_state_with_no_readable_source(generator):
    out = generator.build(_sources({"NY": {"timeZone": "America/New_York", "unread": "walled"}}),
                          _Pages({}), None)
    assert out["states"]["NY"] == {"timeZone": "America/New_York", "items": [], "unread": True}
    with pytest.raises(SystemExit):
        generator.build(_sources({"NY": {"timeZone": "America/New_York"}}), _Pages({}), None)


# ── The bundled file ─────────────────────────────────────────────

_AGGREGATORS = ("vote.org", "ncsl.org", "ballotpedia", "usa.gov", "justia", "findlaw", "casetext",
                "cornell.edu", "wikipedia", "vote411", "rockthevote", "headcount")


def test_the_bundled_calendar_covers_every_state_from_its_own_sources():
    data = json.loads(voter_calendar._PATH.read_text())
    assert data["_source"]
    assert data["electionDate"] == "2026-11-03"
    assert set(data["states"]) == set(STATE_NAMES)
    for code, entry in data["states"].items():
        ZoneInfo(entry["timeZone"])
        if entry.get("unread"):
            assert entry["items"] == [], code
            continue
        kinds = {i["kind"] for i in entry["items"]}
        assert "registration" in kinds or "same_day_registration" in kinds or code == "ND", code
        for item in entry["items"]:
            host = urlsplit(item["source"]["url"]).hostname or ""
            assert not any(a in host for a in _AGGREGATORS), (code, host)
            assert item["source"]["type"] in ("official", "statute")
            # Never "unverified": the generator refuses to write one.
            assert item["verification"]["status"] in ("verified", "statute")
            assert item["verification"]["checkedOn"]
            date.fromisoformat(item["date"])
            if item.get("daysBefore") is not None:
                assert item["countedDate"]
            assert item["rule"]
