"""Michigan's November ballot, read from the Department of State's
Official Candidate Listing (state_candidates_certified_table with
format.report_grid).

tests/fixtures_mi_candidate_listing_2026.html is the real report
(mi-boe.entellitrak.com, electionType=GEN&electionYear=2026, fetched
2026-09-28; "721 Candidates as of Fri Sep 04"), with every office but a
dozen removed and its script/style blocks dropped -- the retained rows
are the state's own markup. It keeps the offices that test each gate:
a joint Governor ticket, a convention-nominated Attorney General, the
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
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.pipeline.fetch import state_candidates as sc
from app.pipeline.fetch import state_candidates_certified_table as ct
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


def _settled(monkeypatch, held):
    monkeypatch.setattr(ct, "primary_date", lambda state, year: held)


@pytest.mark.asyncio
@pytest.mark.parametrize("held, expected", [("2026-08-04", True), (None, False), ("2099-08-04", False)])
async def test_read_only_once_the_primary_has_settled(monkeypatch, held, expected):
    _settled(monkeypatch, held)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=PAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        records = await fetch_confirmed_candidates(client, 2026, "MI", SOURCE)
    assert (records is not None) is expected
    if expected:
        assert seen == [
            "https://mi-boe.entellitrak.com/etk-mi-boe-prod/page.request.do"
            "?page=page.miboePublicReport&electionType=GEN&electionYear=2026"
        ]
    else:
        assert seen == []


@pytest.mark.asyncio
async def test_another_years_report_is_refused(monkeypatch):
    _settled(monkeypatch, "2028-08-01")
    monkeypatch.setattr(ct, "date", type("D", (date,), {"today": classmethod(lambda cls: date(2028, 10, 1))}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=PAGE),
    )) as client:
        assert await fetch_confirmed_candidates(client, 2028, "MI", SOURCE) is None


def test_the_entry_is_the_ballot_and_google_only_supplements_it():
    assert SOURCE["strategy"] == "certified_table"
    assert SOURCE["general_ballot_complete"] is True
    assert SOURCE["general_list"]["strategy"] == "google_civic"
    assert len(SOURCE["general_list"]["house_addresses"]) == 13
