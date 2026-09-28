"""Michigan's November ballot, read from the Department of State's
Official Candidate Listing (state_candidates_certified_table with
format.report_grid).

tests/fixtures_mi_candidate_listing_2026.html is the real report
(mi-boe.entellitrak.com, electionType=GEN&electionYear=2026, fetched
2026-09-28; "721 Candidates as of Fri Sep 04"), with every office but a
dozen removed and its script/style blocks dropped -- the retained rows
are the state's own markup. It keeps the offices that test each gate:
a joint Governor ticket, the convention-nominated Secretary of State
and Attorney General, the
Senate race, three House districts (one with four DISQ rows, one headed
"Files In WAYNE County"), two statewide boards of which one must be
refused ("Governor of Wayne State University" is not the governor),
two Senate districts, a House district and the Supreme Court.

The federal names were checked against the real 2026 results: Abdul
El-Sayed and Mike Rogers won the August 4 Senate primaries; Donavan
McKinney unseated Shri Thanedar in the 13th; William Lawrence won the
7th's Democratic primary.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.pipeline.fetch import state_candidates as sc
from app.pipeline.fetch import state_candidates_certified_table as ct
from app.pipeline.fetch import state_candidates_common as common
from app.pipeline.fetch.state_candidates_certified_table import (
    _rows,
    fetch_confirmed_candidates,
    parse_certified_rows,
    report_grid_rows,
)

_HERE = Path(__file__).resolve().parent
PAGE = (_HERE / "fixtures_mi_candidate_listing_2026.html").read_bytes()
SOURCE = json.loads((_HERE.parent / "app" / "data" / "state_candidate_sources.json").read_text())["states"]["MI"]


def _records(state_offices=True):
    fmt = SOURCE["format"]
    return parse_certified_rows(_rows(PAGE, "mi", fmt), fmt, state_offices)


def test_each_value_lands_under_the_column_it_starts_in():
    rows = report_grid_rows(PAGE, ["Status", "Party / Incumbent", "Candidate Name"])
    assert rows[0] == {
        "Party / Incumbent": "Democratic Party",
        "Candidate Name": "Benson, Jocelyn / Brinks, Winnie",
        "Filed On": "04/16/2026",
        "Filing Method": "Petitions",
        "heading": "Governor / Lt. Governor 4 Year Term (1) Position",
    }
    assert {"Status": "DISQ", "Party / Incumbent": "Working Class Party", "Candidate Name": "Thibodeau, Felix",
            "Filed On": "06/08/2026", "Filing Method": "Convention",
            "heading": "7th District Representative in Congress 2 Year Term (1) Position"} in rows


def test_every_federal_name_minor_parties_included_and_disqualified_dropped():
    federal = {(r["office"], r["district"], r["display_name"], r["party"])
               for r in _records() if r["office"] in ("S", "H")}
    assert federal == {
        ("S", None, "Abdul El-Sayed", "D"),
        ("S", None, "Mike Rogers", "R"),
        ("S", None, "Lydia Christensen", "L"),
        ("S", None, "Tim Long", None),             # U.S. Taxpayers: kept, by its printed label
        ("S", None, "Douglas P. Marsh", "G"),
        ("S", None, "Walter P. Kristy", None),     # Natural Law
        ("H", 1, "Callie Barr", "D"),
        ("H", 1, "Jack Bergman", "R"),
        ("H", 1, "Arnett Satterla", "L"),
        ("H", 1, "Doc Kovaly", None),
        ("H", 1, "LaVeta Davenport", "G"),
        ("H", 1, "Liz Hakola", None),
        ("H", 1, "Zebulon Featherly", "I"),        # No Party Affiliation
        ("H", 7, "William Lawrence", "D"),
        ("H", 7, "Tom Barrett", "R"),
        ("H", 7, "Shane Dedrick", "G"),
        ("H", 13, "Donavan McKinney", "D"),
        ("H", 13, "T.P. Nykoriak", "R"),
        ("H", 13, "Chris Dardzinski", None),
        ("H", 13, "Raelyn Light", "G"),
        ("H", 13, "Simone R. Coleman", None),
    }
    labels = {r["display_name"]: r["party_label"] for r in _records() if r["office"] in ("S", "H")}
    assert labels["Tim Long"] == "U.S. Taxpayers Party"


def test_state_offices_seats_and_what_is_refused():
    records = _records()
    statewide = {(r["office"], r["party"], r["last_name"])
                 for r in records if r["office"] in sc.STATEWIDE_OFFICE_LABELS}
    assert {(o, p, n) for o, p, n in statewide if o == "governor"} == {
        ("governor", "D", "Jocelyn Benson and Winnie Brinks"),
        ("governor", "R", "John James and Jay DeBoyer"),
        ("governor", "L", "Anthony Hudson and Beau Parmenter"),
        ("governor", "O", "Donna Brandenburg and Robert Donald Cowper II"),
        ("governor", "G", "Douglas Campbell and Bobbie Clay"),
    }
    # Convention nominees, which no primary result could name.
    assert ("attorney_general", "D", "Eli Savit") in statewide
    assert ("attorney_general", "R", "Doug Lloyd") in statewide
    assert ("university_regent", "R", "Lena Epstein") in statewide
    leg = {(r["office"], r["district"]) for r in records if r["office"] in sc.STATE_LEG_CHAMBER_LABELS}
    assert leg == {("upper", "1"), ("upper", "12"), ("lower", "110")}
    everyone = {r.get("display_name") or r["last_name"] for r in records}
    # Wayne State's board and the Supreme Court are not read at all.
    assert not any(r["office"] == "governor" and "Akeel" in r["last_name"] for r in records)
    assert not everyone & {"Shereef Akeel", "Andy Anuzis", "Megan Kathleen Cavanagh", "Noah P. Hood"}


def test_university_boards_are_never_the_governor():
    from app.pipeline.fetch.state_candidates_common import parse_statewide_office, parse_state_leg_office
    assert parse_statewide_office("Governor of Wayne State University") is None
    assert parse_statewide_office("Trustee of Michigan State University") is None
    assert parse_statewide_office("Regent of the University of Michigan") == ("university_regent", None)
    assert parse_state_leg_office("1st District Representative in State Legislature") == ("lower", "1", None)


def _grid():
    return report_grid_rows(PAGE, ["Status", "Party / Incumbent", "Candidate Name"])


_US_HOUSE_1 = "1st District Representative in Congress 2 Year Term (1) Position"


def test_a_list_still_holding_primary_filers_is_not_the_ballot_yet(monkeypatch):
    """The report exists all cycle and lists every filer from filing day,
    so before the August primary is canvassed a party can hold two
    candidates for one seat. That list is not the November ballot: it is
    answered [] (not yet) -- until the ballot must be final, when a list
    still holding primary filers is broken (None, fetch_failed)."""
    rows = _grid()
    loser = {"Party / Incumbent": "Democratic Party", "Candidate Name": "Doe, Jane",   # an extra D filer
             "Filed On": "04/21/2026", "Filing Method": "Petitions", "heading": _US_HOUSE_1}
    assert ct._records("MI", rows, SOURCE["format"], True, 2026) not in ([], None)
    monkeypatch.setattr(common, "ballot_final", lambda held, today=None: False)
    assert ct._records("MI", [*rows, loser], SOURCE["format"], True, 2026) == []
    monkeypatch.setattr(common, "ballot_final", lambda held, today=None: True)
    assert ct._records("MI", [*rows, loser], SOURCE["format"], True, 2026) is None


def test_a_layout_with_no_seat_count_is_a_failure_not_an_open_gate():
    """If the report stops printing "(N) Position(s)", the over-fill check
    cannot run; reading on would let a pre-primary filing list through as
    the ballot."""
    rows = [{**r, "heading": r["heading"].replace(" (1) Position", "")} for r in _grid()]
    assert ct._records("MI", rows, SOURCE["format"], True, 2026) is None


def test_independents_and_multi_seat_boards_are_not_overfilled():
    rows = _grid()
    # Two No Party Affiliation candidates may run for one seat.
    extra = {"Party / Incumbent": "No Party Affiliation", "Candidate Name": "Roe, Sam",
             "Filed On": "07/16/2026", "Filing Method": "Petitions", "heading": _US_HOUSE_1}
    assert ct._overfilled([*rows, extra], SOURCE["format"]) is None
    # "(2) Positions": two Democrats for the Regents is the ballot (it is in the fixture).
    assert ct._overfilled(rows, SOURCE["format"]) is None


def test_state_offices_wait_for_the_convention_nominees():
    """2026-08-25..08-30: the Republicans had nominated (08-24), the
    Democrats' convention was 08-31. The list named Benson/Brinks but no
    Democratic Secretary of State, so only federal rows are read and the
    state offices are marked incomplete (the sync keeps what it had)."""
    rows = [r for r in _grid() if not (r["heading"].startswith("Attorney General")
                                       and r.get("Party / Incumbent") == "Democratic Party")]
    records = ct._records("MI", rows, SOURCE["format"], True)
    assert records.state_offices_incomplete is True
    assert {r["office"] for r in records} == {"S", "H"}
    assert ("S", "Abdul El-Sayed") in {(r["office"], r["display_name"]) for r in records}
    complete = ct._records("MI", _grid(), SOURCE["format"], True)
    assert not getattr(complete, "state_offices_incomplete", False)
    assert "attorney_general" in {r["office"] for r in complete}


@pytest.mark.asyncio
async def test_the_listing_is_fetched_by_year_and_another_years_is_not_yet():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=PAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        records = await fetch_confirmed_candidates(client, 2026, "MI", SOURCE)
        later = await fetch_confirmed_candidates(client, 2028, "MI", SOURCE)
    assert records
    assert seen[0] == ("https://mi-boe.entellitrak.com/etk-mi-boe-prod/page.request.do"
                       "?page=page.miboePublicReport&electionType=GEN&electionYear=2026")
    assert later == []   # the page names November 3, 2026: not published for 2028 yet


def test_an_independent_governor_ticket_never_holds_the_state_offices():
    """A petition independent (or a minor party fielding only a governor)
    has no convention slate; waiting for its Secretary of State would hold
    every state office back all cycle, silently."""
    indie = {"Party / Incumbent": "No Party Affiliation", "Candidate Name": "Roe, Sam / Poe, Ann",
             "Filed On": "07/16/2026", "Filing Method": "Petitions",
             "heading": "Governor / Lt. Governor 4 Year Term (1) Position"}
    lone = {"Party / Incumbent": "Working Class Party", "Candidate Name": "Hale, Pat / Doe, Kim",
            "Filed On": "06/08/2026", "Filing Method": "Convention",
            "heading": "Governor / Lt. Governor 4 Year Term (1) Position"}
    records = ct._records("MI", [*_grid(), indie, lone], SOURCE["format"], True, 2026)
    assert not getattr(records, "state_offices_incomplete", False)
    assert ("governor", "I") in {(r["office"], r["party"]) for r in records}


def test_the_entry_is_the_ballot_and_google_only_supplements_it():
    assert SOURCE["strategy"] == "certified_table"
    assert SOURCE["general_ballot_complete"] is True
    assert SOURCE["general_list"]["strategy"] == "google_civic"
    assert len(SOURCE["general_list"]["house_addresses"]) == 13
