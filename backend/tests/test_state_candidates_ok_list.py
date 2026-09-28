"""Oklahoma's November ballot, read from the State Election Board's own
List of Elections (state_candidates_certified_table with format.outline_rows).

tests/fixtures_ok_election_list_2026.html is the real page
(hosting.okelections.gov/electionlist.html, fetched 2026-09-28, the file's
own Last-Modified 2026-09-24), trimmed to two counties and a handful of
their contests; every retained line is byte-for-byte the state's, CRLF
line ends and leading byte-order mark included. Adair County carries
congressional district 2, Tulsa County district 1, so between them the
fixture holds a county, a judicial, a municipal and a ballot-question
entry beside the federal and state offices -- each of which must be
refused.

The nominees were checked against the real 2026 results: Kevin Hern won
the Republican Senate primary outright, N'Kiyla Jasmine Thomas the
Democratic runoff on 2026-08-25, Mike Mazzei the Republican governor's
runoff; Mark Tedford (R) and John Croisant (D) are district 1's nominees
for Hern's open seat.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.pipeline.fetch import state_candidates as sc
from app.pipeline.fetch import state_candidates_common as common
from app.pipeline.fetch.state_candidates_certified_table import (
    _rows,
    fetch_confirmed_candidates,
    outline_rows,
    parse_certified_rows,
)

_HERE = Path(__file__).resolve().parent
PAGE = (_HERE / "fixtures_ok_election_list_2026.html").read_bytes()
SOURCE = json.loads((_HERE.parent / "app" / "data" / "state_candidate_sources.json").read_text())["states"]["OK"]


def _records(state_offices=True, page=PAGE):
    fmt = SOURCE["format"]
    return parse_certified_rows(_rows(page, "ok", fmt), fmt, state_offices)


def test_each_line_carries_the_lines_above_it_by_indent():
    rows = outline_rows(PAGE)
    assert {
        "outline_1": "CONGRESSIONAL OFFICERS",
        "outline_2": "UNITED STATES REPRESENTATIVE - DISTRICT 02",
        "outline_3": "RONNIE HOPKINS, INDEPENDENT",
    } in rows
    # A shallower line clears what was under the one before it.
    assert {"outline_1": "JUDICIAL RETENTION"} in rows
    assert {"outline_1": "JUDICIAL RETENTION", "outline_2": "SUPREME COURT DISTRICT 1"} in rows


def test_every_federal_name_on_the_ballot_independents_included():
    federal = {(r["office"], r["district"], r["display_name"], r["party"], r["last_name"])
               for r in _records() if r["office"] in ("S", "H")}
    assert federal == {
        ("S", None, "KEVIN HERN", "R", "HERN"),
        ("S", None, "N'KIYLA JASMINE THOMAS", "D", "THOMAS"),
        ("S", None, "SEVIER WHITE", "L", "WHITE"),
        ("S", None, "RON MEINHARDT", "I", "MEINHARDT"),
        ("S", None, "CURTIS STINNETT", "I", "STINNETT"),
        ("H", 1, "MARK TEDFORD", "R", "TEDFORD"),
        ("H", 1, "JOHN CROISANT", "D", "CROISANT"),
        ("H", 2, "JOSH BRECHEEN", "R", "BRECHEEN"),
        ("H", 2, "BRANDON WADE", "D", "WADE"),
        ("H", 2, "RONNIE HOPKINS", "I", "HOPKINS"),
    }


def test_state_offices_and_seats_but_nothing_local():
    records = _records()
    statewide = {(r["office"], r["party"], r["last_name"])
                 for r in records if r["office"] in sc.STATEWIDE_OFFICE_LABELS}
    assert statewide == {
        ("governor", "R", "MIKE MAZZEI"),
        ("governor", "D", "CYNDI MUNSON"),
        ("governor", "I", "ORLANDO LYNN BUSH"),
        ("governor", "I", "ROBERT E BROOKS SR"),
        ("governor", "I", "JERRY GRIFFIN"),
        ("lt_governor", "R", "T. W. SHANNON"),
        ("lt_governor", "D", "KELLY FORBES"),
        ("corporation_commissioner", "R", "BRAD BOLES"),
        ("corporation_commissioner", "D", "RHONDA EASTMAN"),
    }
    leg = {(r["office"], r["district"], r["party"], r["last_name"])
           for r in records if r["office"] in sc.STATE_LEG_CHAMBER_LABELS}
    assert leg == {
        ("upper", "4", "R", "TOM WOODS"), ("upper", "4", "D", "ELLEN CUFF"),
        ("lower", "24", "R", "CHRIS BANNING"), ("lower", "24", "I", "JOSHUA CONANT"),
    }
    # County commissioner, district judge, city council: none of them.
    everyone = {r.get("display_name") or r["last_name"] for r in records}
    assert not everyone & {"CHARLES E.W. BOECHER", "TOM SAWYER", "VANESSA HALL-HARPER"}


def test_without_the_opt_in_only_federal_rows():
    assert {r["office"] for r in _records(False)} == {"S", "H"}


def test_a_name_with_its_own_comma_keeps_it():
    fmt = SOURCE["format"]
    rows = [{"outline_2": "UNITED STATES REPRESENTATIVE - DISTRICT 05",
             "outline_3": "JOHN Q. PUBLIC, JR., REPUBLICAN"}]
    [record] = parse_certified_rows(rows, fmt)
    assert (record["display_name"], record["party"]) == ("JOHN Q. PUBLIC, JR.", "R")


@pytest.mark.asyncio
async def test_the_fixed_address_is_read_only_for_this_years_november_list(monkeypatch):
    # Early in the cycle: the ballot need not be final yet.
    monkeypatch.setattr(common, "ballot_final", lambda held, today=None: False)
    """The address always shows the NEXT election; in August it was the
    runoff's list, whose candidates are not November's."""
    august = PAGE.replace(b"NOVEMBER / 2026 LIST OF ELECTIONS", b"AUGUST / 2026 LIST OF ELECTIONS")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=PAGE),
    )) as client:
        assert await fetch_confirmed_candidates(client, 2026, "OK", SOURCE)
        # Another cycle's list: not published yet -- an empty answer, not a
        # failed fetch (which would report fetch_failed nightly and send the
        # sync to the crawler's spare source for months).
        assert await fetch_confirmed_candidates(client, 2028, "OK", SOURCE) == []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=august),
    )) as client:
        assert await fetch_confirmed_candidates(client, 2026, "OK", SOURCE) == []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"<html>a page with no list at all</html>"),
    )) as client:
        # Still a page that names no election: not yet.
        assert await fetch_confirmed_candidates(client, 2026, "OK", SOURCE) == []


def test_the_entry_is_the_ballot_and_google_only_supplements_it():
    assert SOURCE["strategy"] == "certified_table"
    assert SOURCE["general_ballot_complete"] is True
    assert SOURCE["statewide_offices"] is True
    assert SOURCE["general_list"]["strategy"] == "google_civic"


@pytest.mark.asyncio
async def test_a_list_still_not_published_once_ballots_are_mailed_is_a_failure(monkeypatch):
    """45 days out (UOCAVA) every state has mailed its ballot. A page that
    still names another election then is a moved or broken page, and must
    report fetch_failed rather than "not yet" for the rest of the cycle."""
    august = PAGE.replace(b"NOVEMBER / 2026 LIST OF ELECTIONS", b"AUGUST / 2026 LIST OF ELECTIONS")
    monkeypatch.setattr(common, "ballot_final", lambda held, today=None: True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=august),
    )) as client:
        assert await fetch_confirmed_candidates(client, 2026, "OK", SOURCE) is None


def test_not_yet_turns_into_a_failure_45_days_before_the_general():
    from datetime import date
    assert common.not_yet(2026, "OK", "test", today=date(2026, 9, 18)) == []
    assert common.not_yet(2026, "OK", "test", today=date(2026, 9, 19)) is None   # Nov 3 - 45 days
