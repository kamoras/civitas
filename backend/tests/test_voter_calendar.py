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


# ── The next election, without anyone editing the sources ────────

def _carry_sources():
    return {"electionDate": "2026-11-03", "states": {
        "WA": {"timeZone": "America/Los_Angeles", "items": [
            # A count from election day, cited from a statute: carries.
            {"kind": "registration", "method": "online", "rule": "8 days before.", "daysBefore": 8,
             "date": "2026-10-26", "source": {"type": "statute", "publisher": "RCW", "url": "https://law.example.gov/a",
                                              "quote": "at least eight days before the election"}},
            # A state page printing this election's date: re-dated.
            {"kind": "ballot_return", "method": "mail", "basis": "received", "rule": "By noon on Election Day.",
             "date": "2026-11-03", "time": "12:00",
             "source": {"type": "official", "publisher": "SOS", "url": "https://sos.example.gov/b",
                        "quote": "must be received by noon on election day, tuesday, november 3, 2026."}},
            # A rule written as a calendar date: never carried.
            {"kind": "absentee_request", "method": "any", "rule": "Request by October 27.", "date": "2026-10-27",
             "source": {"type": "official", "publisher": "SOS", "url": "https://sos.example.gov/c", "quote": "q"}},
        ]},
        # 29 days before 2028-11-07 is Monday, October 9, 2028: Columbus Day.
        "AZ": {"timeZone": "America/Phoenix", "items": [
            {"kind": "registration", "method": "any", "rule": "29 days before.", "daysBefore": 29,
             "date": "2026-10-05", "source": {"type": "statute", "publisher": "ARS", "url": "https://law.example.gov/z",
                                              "quote": "the twenty-ninth day preceding the date of the election"}},
        ]},
    }}


def test_rules_carry_to_the_next_election_by_whole_weeks(generator):
    out, dropped = generator.carry(_carry_sources(), date(2028, 11, 7))
    wa = out["states"]["WA"]
    assert [i["date"] for i in wa["items"]] == ["2028-10-30", "2028-11-07"]
    assert wa["items"][1]["source"]["quote"] == "must be received by noon on election day, tuesday, november 7, 2028."
    assert wa["incomplete"] is True and "calendar date" in dropped["WA"][0]
    # A count that would land on a federal holiday is left to the state.
    assert "unread" in out["states"]["AZ"] and "federal holiday" in dropped["AZ"][0]
    with pytest.raises(ValueError):
        generator.carry(_carry_sources(), date(2028, 11, 8))


def test_a_carried_item_is_kept_only_where_its_page_says_it_for_the_new_election(generator):
    sources, _ = generator.carry(_carry_sources(), date(2028, 11, 7))
    pages = _Pages({
        "https://law.example.gov/a": (200, "<p>At least eight days before the election.</p>"),
        # The state's page still shows the old election: not verified for the new one.
        "https://sos.example.gov/b": (200, "<p>Must be received by noon on Election Day, Tuesday, November 3, 2026.</p>"),
    })
    out = generator.build({**sources, "states": {"WA": sources["states"]["WA"]}}, pages, None, strict=False)
    assert out["electionDate"] == "2028-11-07"
    assert [i["date"] for i in out["states"]["WA"]["items"]] == ["2028-10-30"]
    assert out["states"]["WA"]["incomplete"] is True


def test_a_calendar_missing_some_deadlines_says_so(calendar):
    calendar({"WA": {**WA, "incomplete": True}})
    note = voter_calendar.calendar_for_state("WA", ELECTION, _utc(2026, 10, 1, 12))["note"]
    assert "Not every one of Washington's deadlines is confirmed" in note


def test_the_refreshed_calendar_wins_over_the_bundled_one(calendar):
    calendar({"WA": WA})
    refreshed = pathlib.Path(voter_calendar.runtime_data_path(voter_calendar._PATH.name))
    refreshed.parent.mkdir(parents=True, exist_ok=True)
    refreshed.write_text(json.dumps({"electionDate": "2028-11-07", "states": {"OR": {**WA}}}))
    assert voter_calendar.calendar_for_state("WA", ELECTION) is None
    assert voter_calendar.calendar_for_state("OR", date(2028, 11, 7), _utc(2028, 10, 1, 12)) is not None
    refreshed.unlink()


# ── Registering: where, by when, and who closes soon ─────────────

class _Redirecting(_Pages):
    """A client whose GET answers each URL with (status, html[, final url, headers])."""

    def get(self, url, **kwargs):
        status, body, *rest = self.pages[url]
        final = (rest[0] if rest else None) or url
        headers = {"content-type": "text/html", **(rest[1] if len(rest) > 1 else {})}
        return httpx.Response(status, content=body.encode(), headers=headers,
                              request=httpx.Request("GET", final))


_REG_PAGE = "<h1>Register to vote online</h1>"


@pytest.mark.parametrize("url, page, cited, status", [
    # Answers on a .gov host and reads as a registration page.
    ("https://olvr.sos.example.gov/", (200, _REG_PAGE), set(), "verified"),
    # A state.xx.us host is a state's own.
    ("https://www.sec.state.ma.us/ovr/", (200, _REG_PAGE), set(), "verified"),
    # A host outside .gov the state's own deadline citations already use (D.C.'s dcboe.org).
    ("https://www.dcboe.org/register", (200, _REG_PAGE), {"www.dcboe.org"}, "verified"),
    # A bot wall's challenge: kept, unconfirmed.
    ("https://mvic.sos.example.gov/", (403, "<title>Just a moment...</title>", None, {"cf-mitigated": "challenge"}),
     set(), "blocked"),
])
def test_a_register_page_is_checked_before_it_is_linked(generator, url, page, cited, status):
    reg, why = generator.check_register({"url": url, "publisher": "SOS"}, cited, _Redirecting({url: page}),
                                        None, "2026-10-16")
    assert why is None and reg["status"] == status and reg["url"] == url and reg["checkedOn"] == "2026-10-16"


@pytest.mark.parametrize("url, page", [
    ("https://olvr.sos.example.gov/", (404, "<h1>Not found</h1>")),  # a dead link
    ("https://registertovote.example.com/", (200, _REG_PAGE)),  # not a government host
    ("https://olvr.sos.example.gov/", (200, _REG_PAGE, "https://ads.example.com/register")),  # lands elsewhere
    ("https://olvr.sos.example.gov/", (200, "<h1>Welcome</h1>")),  # not a registration page
    ("https://olvr.sos.example.gov/", (403, "<h1>Forbidden</h1>")),  # refused, not walled
])
def test_a_register_page_that_fails_its_check_is_refused(generator, url, page):
    reg, why = generator.check_register({"url": url}, set(), _Redirecting({url: page}), None, "2026-10-16")
    assert reg is None and why


def test_a_state_with_no_registration_is_held_to_its_quote(generator):
    url = "https://www.sos.nd.example.gov/info.pdf"
    entry = {"none": True, "url": url, "quote": "north dakota does not have voter registration."}
    ok = _Pages({url: (200, "<p>North Dakota does not have voter registration.</p>")})
    reg, why = generator.check_register(entry, set(), ok, None, "2026-10-16")
    assert why is None and reg["none"] is True
    reg, why = generator.check_register(entry, set(), _Pages({url: (200, "<p>Vote!</p>")}), None, "2026-10-16")
    assert reg is None and "quote" in why


def test_the_generator_names_every_failed_deadline_and_never_links_a_dead_register_page(generator, capsys):
    """Every unverified deadline in one run, and nothing written; a register
    page that fails its check is left out (vote.gov instead) and named, but
    doesn't hold back the deadlines."""
    pages = _Pages({"https://sos.example.gov/a": (200, "<p>at least 8 days before</p>"),
                    "https://sos.example.gov/b": (200, "<p>x</p>"),
                    "https://olvr.sos.example.gov/": (404, "")})
    states = {
        code: {"timeZone": "America/Los_Angeles", "items": [_reg("https://sos.example.gov/b", "missing")]}
        for code in ("OR", "WA")
    }
    with pytest.raises(SystemExit) as exc:
        generator.build(_sources(states), pages, None)
    assert "OR[0]" in str(exc.value) and "WA[0]" in str(exc.value)
    out = generator.build(_sources({"WA": {
        "timeZone": "America/Los_Angeles", "items": [_reg("https://sos.example.gov/a", "at least 8 days before")],
        "register": {"url": "https://olvr.sos.example.gov/"}}}), pages, None)
    assert "register" not in out["states"]["WA"] and out["states"]["WA"]["items"]
    assert "WA register: not linked" in capsys.readouterr().err
    assert out["builtOn"]


def test_a_newer_bundled_calendar_wins_over_an_older_refresh(calendar):
    """A calendar shipped in the image after the last refresh (a state added,
    a citation fixed) is used at once; a refresh built later wins again."""
    calendar({"WA": WA})
    bundled = json.loads(voter_calendar._PATH.read_text())
    voter_calendar._PATH.write_text(json.dumps({**bundled, "builtOn": "2026-10-16"}))
    refreshed = pathlib.Path(voter_calendar.runtime_data_path(voter_calendar._PATH.name))
    refreshed.parent.mkdir(parents=True, exist_ok=True)
    refreshed.write_text(json.dumps({"electionDate": "2026-11-03", "states": {"OR": {**WA}}}))
    assert voter_calendar.calendar_for_state("WA", ELECTION) is not None
    refreshed.write_text(json.dumps({"electionDate": "2026-11-03", "builtOn": "2026-10-20",
                                     "states": {"OR": {**WA}}}))
    assert voter_calendar.calendar_for_state("WA", ELECTION) is None
    assert voter_calendar.calendar_for_state("OR", ELECTION) is not None
    refreshed.unlink()


def _reg_state(zone, *, online="2026-10-19", same_day=None, none=False, register=None):
    items = [{"kind": "registration", "method": "online", "rule": "15 days before.", "date": online,
              "source": _src()},
             {"kind": "registration", "method": "mail", "basis": "received", "rule": "Must arrive.",
              "date": online, "source": _src()}]
    if same_day:
        items.append({"kind": "same_day_registration", "method": "in_person", "rule": same_day[2],
                      "date": same_day[0], "endDate": same_day[1], "source": _src()})
    entry = {"timeZone": zone, "items": [] if none else items}
    if register:
        entry["register"] = register
    if none:
        entry["register"] = {"none": True, "url": "https://sos.nd.example.gov/info.pdf", "status": "verified"}
        entry["items"] = [{"kind": "absentee_request", "method": "any", "rule": "r", "date": "2026-11-02",
                           "source": _src()}]
    return entry


_ZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix",
          "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu"]


@pytest.mark.parametrize("zone", _ZONES)
def test_days_left_to_register_count_on_the_state_clock_around_midnight(calendar, freeze_utcnow, zone):
    """One minute before midnight on the deadline, where the voter is, it is
    the last day; one minute after, registration has closed, whatever UTC says."""
    calendar({"XX": _reg_state(zone)})
    tz = ZoneInfo(zone)

    def at(local):
        freeze_utcnow(local.replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None))
        return voter_calendar.calendar_for_state("XX", ELECTION)["registration"]

    day_before = at(datetime(2026, 10, 18, 23, 59))
    assert day_before["daysLeft"] == 1 and day_before["countdown"] == "tomorrow"
    last = at(datetime(2026, 10, 19, 23, 59))
    assert last["status"] == "open" and last["daysLeft"] == 0 and last["countdown"] == "today"
    assert last["sentence"] == "Register online by Mon, Oct 19"
    after = at(datetime(2026, 10, 20, 0, 1))
    assert after == {"status": "closed", "sentence": "Registration for this election has closed.",
                     "closesOn": None, "daysLeft": None, "countdown": None, "sameDay": None, "source": None}


def test_the_closing_soon_list_is_the_next_ten_days_on_each_state_clock(calendar):
    calendar({
        "AA": _reg_state("America/New_York", online="2026-10-16"),  # today: listed
        "BB": _reg_state("America/New_York", online="2026-10-26"),  # 10 days: listed
        "CC": _reg_state("America/New_York", online="2026-10-27"),  # 11 days: not yet
        "DD": _reg_state("America/New_York", online="2026-10-15"),  # closed: not listed
        # Same-day registration only, regular closed: nothing to close soon.
        "EE": _reg_state("America/New_York", online="2026-10-10",
                         same_day=("2026-10-11", "2026-11-03", "Register at your polling place.")),
        # Counted on Honolulu's clock, five or six hours behind Eastern.
        "HI": _reg_state("Pacific/Honolulu", online="2026-10-26"),
    })
    now = _utc(2026, 10, 16, 23, 30)  # 7:30 p.m. Eastern, 1:30 p.m. in Honolulu
    rows = voter_calendar.registration_closing(ELECTION, now)
    assert [(r["state"], r["daysLeft"]) for r in rows] == [("AA", 0), ("BB", 10), ("HI", 10)]
    # The edge moves with the clock: 1 a.m. Eastern on Oct 17 has closed AA,
    # and brings CC to 10 days.
    later = voter_calendar.registration_closing(ELECTION, _utc(2026, 10, 17, 5))
    assert [r["state"] for r in later] == ["BB", "HI", "CC"]
    assert voter_calendar.CLOSING_SOON_DAYS == 10


def test_each_closing_state_carries_how_and_where_to_register(calendar):
    reg = {"url": "https://olvr.sos.example.gov/", "publisher": "SOS", "status": "blocked"}
    calendar({"MI": _reg_state("America/Detroit", register=reg,
                               same_day=("2026-10-20", "2026-11-03", "From 14 days before, register at your clerk."))})
    (row,) = voter_calendar.registration_closing(ELECTION, _utc(2026, 10, 16, 18))
    assert row["sentence"] == "Register online by Mon, Oct 19" and row["countdown"] == "3 days"
    assert row["sameDay"] == "From 14 days before, register at your clerk."
    # Behind a bot wall: linked, but not vouched for.
    assert row["register"] == {"url": reg["url"], "publisher": "SOS", "confirmed": False, "statePage": True}


def test_north_dakota_and_same_day_registration_say_so_plainly(calendar):
    data = {"electionDate": "2026-11-03",
            "registerFallback": {"url": "https://vote.gov/", "publisher": "Vote.gov", "status": "blocked"},
            "states": {
                "ND": _reg_state("America/Chicago", none=True),
                "NH": {"timeZone": "America/New_York", "items": [
                    {"kind": "same_day_registration", "method": "in_person", "date": "2026-11-03",
                     "endDate": "2026-11-03", "rule": "Register at your polling place on Election Day and vote.",
                     "source": _src()}]},
                "MN": _reg_state("America/Chicago", online="2026-10-13", same_day=(
                    "2026-10-14", "2026-11-03", "Register at your polling place on Election Day.")),
            }}
    calendar(data["states"])
    voter_calendar._PATH.write_text(json.dumps({**data, "states": json.loads(voter_calendar._PATH.read_text())["states"]}))
    now = _utc(2026, 10, 16, 18)
    nd = voter_calendar.calendar_for_state("ND", ELECTION, now)
    assert nd["registration"]["sentence"] == "North Dakota does not have voter registration."
    assert nd["registration"]["status"] == "none" and nd["register"] is None
    nh = voter_calendar.calendar_for_state("NH", ELECTION, now)
    assert nh["registration"]["status"] == "same_day"
    assert nh["registration"]["sentence"] == "Register at your polling place on Election Day and vote."
    # No state page on file: vote.gov, unconfirmed and not the state's own.
    assert nh["register"] == {"url": "https://vote.gov/", "publisher": "Vote.gov", "confirmed": False,
                              "statePage": False}
    mn = voter_calendar.calendar_for_state("MN", ELECTION, now)
    assert mn["registration"]["sentence"] == (
        "Regular registration has closed. Register at your polling place on Election Day.")
    assert [r["state"] for r in voter_calendar.registration_closing(ELECTION, now)] == []


def test_every_state_in_the_bundled_calendar_has_somewhere_to_register():
    data = json.loads(voter_calendar._PATH.read_text())
    assert data["registerFallback"]["url"] == "https://vote.gov/"
    for code, entry in data["states"].items():
        reg = entry.get("register")
        if reg is None:
            continue  # vote.gov
        assert reg["status"] in ("verified", "blocked"), code
        host = urlsplit(reg["url"]).hostname or ""
        cited = {urlsplit(i["source"]["url"]).hostname for i in entry["items"]}
        assert host.endswith(".gov") or host.endswith(".us") or host in cited, (code, host)
    assert data["states"]["ND"]["register"]["none"] is True
    assert not data["states"]["NY"].get("unread") and data["states"]["NY"]["items"]
