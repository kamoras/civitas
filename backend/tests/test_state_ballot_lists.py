"""The three certified-November-ballot adapters added 2026-09-26 (LA
voterportal, SC vrems, MO certified_pdf), and the two matcher fixes the
live verification of them exposed. Fixtures are trimmed copies of what
each state actually served that day."""

from types import SimpleNamespace

import httpx
import pytest

from app.pipeline.fetch.state_candidates import _match_candidate
from app.pipeline.fetch.state_candidates_certified_pdf import parse_certification
from app.pipeline.fetch.state_candidates_voterportal import (
    fetch_confirmed_candidates as fetch_voterportal,
)
from app.pipeline.fetch.state_candidates_vrems import (
    _general_election_id,
    fetch_confirmed_candidates as fetch_vrems,
)

LA_URL = "https://portal.test/Data"

LA_DATES = {"Dates": {"Date": [
    {"PKElectionID": "344", "ElectionDate": "11/03/2026", "ResultsOfficial": "0"},
    {"PKElectionID": "343", "ElectionDate": "06/27/2026", "ResultsOfficial": "1"},
]}}

LA_RACES = {"Races": {"Race": [
    {"ID": "1", "SpecificTitle": "U. S. Senator",
     "Choice": [{"ID": "a", "Desc": '"Jamie" Davis (DEM)'}, {"ID": "b", "Desc": "Julia Letlow (REP)"}]},
    {"ID": "2", "SpecificTitle": "U. S. Representative -- 2nd Congressional District",
     "Choice": [{"ID": "c", "Desc": "Troy A. Carter, Sr. (DEM)"},
                {"ID": "d", "Desc": 'Walter "Rocky" Beach (NOPTY)'},
                {"ID": "e", "Desc": "Lisa Ballay (LBT)"}]},
    # A one-candidate race comes back as a bare object, not a list.
    {"ID": "3", "SpecificTitle": "U. S. Representative -- 9th Congressional District",
     "Choice": {"ID": "f", "Desc": "Solo Person (REP)"}},
    {"ID": "4", "SpecificTitle": "Mayor -- City of DeRidder",
     "Choice": [{"ID": "g", "Desc": "Not Federal (DEM)"}]},
]}}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_voterportal_reads_the_staged_general_ballot():
    def handler(request):
        blob = request.url.params["blob"]
        if blob == "ElectionDates.htm":
            return httpx.Response(200, json=LA_DATES)
        assert blob == "20261103/RacesCandidates_Multiparish.htm"
        return httpx.Response(200, json=LA_RACES)

    async with _client(handler) as client:
        records = await fetch_voterportal(client, 2026, "LA", {"data_url": LA_URL})

    by_name = {r["display_name"]: r for r in records}
    assert set(by_name) == {'"Jamie" Davis', "Julia Letlow", "Troy A. Carter, Sr.",
                            'Walter "Rocky" Beach', "Lisa Ballay", "Solo Person"}
    assert by_name['"Jamie" Davis'] == {
        "office": "S", "district": None, "party": "D", "last_name": "Davis",
        "display_name": '"Jamie" Davis',
    }
    assert by_name["Troy A. Carter, Sr."]["last_name"] == "Carter"
    # No-party is an ordinary independent on a certified ballot.
    assert by_name['Walter "Rocky" Beach']["party"] == "I"
    assert by_name["Solo Person"]["district"] == 9


@pytest.mark.asyncio
async def test_voterportal_says_cannot_tell_before_the_general_is_staged():
    dates = {"Dates": {"Date": [{"ElectionDate": "06/27/2026"}]}}

    async with _client(lambda r: httpx.Response(200, json=dates)) as client:
        assert await fetch_voterportal(client, 2026, "LA", {"data_url": LA_URL}) is None


SC_BASE = "https://vrems.test"

SC_ELECTIONS = [
    {"electionId": "22596", "electionName": "Statewide General Election",
     "electionDate": "2026-11-03T00:00:00"},
    {"electionId": "22760", "electionName": "US Senate Special Republican Primary",
     "electionDate": "2026-08-11T00:00:00"},
]

SC_SEARCH_PAGE = """<html><body><form id="searchForm">
<input type="hidden" id="ElectionId" name="ElectionId" value="22596" />
<input type="hidden" id="ExportFileName" name="ExportFileName" value="x" />
<select id="SelectedOffice" name="SelectedOffice">
<option value="-1">All</option><option value="463">Governor and Lieutenant Governor</option>
<option value="376">U.S. Senate</option><option value="378">U.S. House of Representatives</option>
<option value="380">State House of Representatives</option></select>
</form></body></html>"""

_HEAD = ("<table id=\"gridCandidateSearch\"><thead><tr><th>Office</th><th>Associated Counties</th>"
         "<th>Name on Ballot</th><th>Running Mate</th><th>Party</th><th>Location of Filing</th>"
         "<th>Candidate Status</th></tr></thead><tbody>")


def _sc_row(office, name, party, status="Active"):
    return (f"<tr><td>{office}</td><td></td><td><a href='#'>{name}</a></td><td></td>"
            f"<td>{party}</td><td>State</td><td>{status}</td></tr>")


SC_TABLES = {
    "376": _HEAD + _sc_row("U.S. Senate", "Darline Graham", "Republican")
    + _sc_row("U.S. Senate", "Mark Hackett", "Constitution") + "</tbody></table>",
    "378": _HEAD + _sc_row("U.S. House of Representatives, District 2", "Joe Wilson", "Republican")
    + _sc_row("U.S. House of Representatives, District 2", "Pat Petition", "Petition")
    + _sc_row("U.S. House of Representatives, District 2", "Gone Away", "Democratic", "Withdrew(2)")
    + "</tbody></table>",
}


@pytest.mark.asyncio
async def test_vrems_reads_every_federal_office_on_the_general_ballot():
    asked = []

    def handler(request):
        if request.url.path == "/Candidate/GetElections":
            return httpx.Response(200, json=SC_ELECTIONS)
        if request.method == "GET":
            return httpx.Response(200, text=SC_SEARCH_PAGE)
        body = request.content.decode()
        office = next(o for o in SC_TABLES if f'name="SelectedOffice"\r\n\r\n{o}\r\n' in body)
        asked.append(office)
        assert "Active" in body
        return httpx.Response(200, text=SC_TABLES[office])

    async with _client(handler) as client:
        records = await fetch_vrems(client, 2026, "SC", {"base_url": SC_BASE})

    # Only the offices parse_office recognises as federal were asked for.
    assert asked == ["376", "378"]
    got = {(r["office"], r["district"], r["display_name"], r["party"]) for r in records}
    assert got == {
        ("S", None, "Darline Graham", "R"),
        ("S", None, "Mark Hackett", "C"),
        ("H", 2, "Joe Wilson", "R"),
        ("H", 2, "Pat Petition", "I"),
    }


def test_vrems_refuses_to_guess_between_two_generals():
    two = SC_ELECTIONS + [{"electionId": "9", "electionName": "Municipal General Election",
                           "electionDate": "2026-11-03T00:00:00"}]
    assert _general_election_id(SC_ELECTIONS, 2026) == "22596"
    assert _general_election_id(two, 2026) is None


MO_LINES = """Certificate of Candidates for
REPUBLICAN PARTY EMBLEM
REPUBLICAN CANDIDATES
For State Auditor
Scott Fitzpatrick
For U.S. Representative
District 1, Paul Berry III
District 5, Rick Brattin
For State Senator
District 2, Nick Schroer
7
DEMOCRATIC CANDIDATES
For U.S. Senator
Jane Example
For U.S. Representative
District 5, Emanuel Cleaver, II
LIBERTARIAN CANDIDATES
For U.S. Representative
District 5, Randall (Randy) Langkraehr
INDEPENDENT CANDIDATES
For U.S. Representative
District 2, Indy Pendent
JUDICIAL CANDIDATES
For U.S. Representative
District 3, Should Not Appear
""".splitlines()


def test_certified_pdf_reads_each_line_under_its_party_and_office():
    got = {(r["office"], r["district"], r["party"], r["display_name"]) for r in parse_certification(MO_LINES)}
    assert got == {
        ("H", 1, "R", "Paul Berry III"),
        ("H", 5, "R", "Rick Brattin"),
        ("S", None, "D", "Jane Example"),
        ("H", 5, "D", "Emanuel Cleaver, II"),
        ("H", 5, "L", "Randall Langkraehr"),  # annotation stripped, as everywhere
        ("H", 2, "I", "Indy Pendent"),
    }


def _cand(name, party, cid, raised=0):
    return SimpleNamespace(name=name, party=party, id=cid,
                           has_raised_funds=raised > 0, contributions=raised)


def test_match_strips_a_suffix_filed_on_the_fec_surname():
    # FEC files "CLEAVER II, EMANUEL"; the ballot prints "Emanuel Cleaver, II".
    cleaver = _cand("CLEAVER II, EMANUEL", "DEM", "H4MO05234")
    assert _match_candidate([cleaver, _cand("BRATTIN, RICHARD", "REP", "x")], "Cleaver", "D") is cleaver


def test_match_confirms_one_person_filed_under_two_fec_ids():
    # MO-1 2026: one Paul Berry, two FEC candidate ids.
    stale = _cand("BERRY, PAUL", "REP", "H2MO01111")
    live = _cand("BERRY, PAUL", "REP", "H6MO01222", raised=150_000)
    assert _match_candidate([stale, live, _cand("BELL, WESLEY", "DEM", "b")], "Berry", "R") is live


def test_match_still_refuses_two_different_people():
    a = _cand("SULLIVAN, DAN", "REP", "a")
    b = _cand("SULLIVAN, JOE", "REP", "b")
    assert _match_candidate([a, b], "Sullivan", "R") is None
