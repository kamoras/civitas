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
        "display_name": '"Jamie" Davis', "party_label": "DEM",
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


# --- The sync: ballot-only rows, and a certified ballot being authoritative ---

from unittest.mock import AsyncMock  # noqa: E402

from app.models import Candidate, Race  # noqa: E402
from app.pipeline.fetch import state_candidates as sc  # noqa: E402


def _race(db, race_id, state, office="S", district=None):
    db.add(Race(id=race_id, cycle_year=2026, office=office, state=state, district=district, is_special=False))


def _db_cand(db, cid, race_id, name, party, **kw):
    db.add(Candidate(id=cid, race_id=race_id, name=name, party=party, **kw))


@pytest.fixture()
def only(monkeypatch):
    async def no_calendar(client, cycle):
        return {}
    monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)

    def scope(state, records):
        monkeypatch.setattr(sc, "configured_states", lambda: {state})
        strategy = sc.source_for_state(state)["strategy"]
        monkeypatch.setitem(sc.STRATEGIES, strategy, AsyncMock(return_value=records))
    return scope


def _rec(office, district, party, last, display):
    return {"office": office, "district": district, "party": party,
            "last_name": last, "display_name": display}


@pytest.mark.asyncio
async def test_a_certified_ballot_unconfirms_a_nominee_who_withdrew(db_session, only):
    # Maine 2026: Platner won the primary, withdrew, Jackson replaced him.
    # The sticky flag used to keep Platner on the page beside Jackson.
    _race(db_session, "2026-SEN-LA", "LA")
    _db_cand(db_session, "S6ME00001", "2026-SEN-LA", "PLATNER, GRAHAM", "DEM", confirmed_general=True)
    _db_cand(db_session, "S6ME00002", "2026-SEN-LA", "JACKSON, TROY", "DEM")
    _db_cand(db_session, "S6ME00003", "2026-SEN-LA", "COLLINS, SUSAN M.", "REP", confirmed_general=True)
    db_session.commit()
    only("LA", [_rec("S", None, "D", "Jackson", "Troy D. Jackson"),
                _rec("S", None, "R", "Collins", "Susan M. Collins")])

    results = await sc.sync_confirmed_candidates(db_session, None, 2026)

    flags = {c.id: c.confirmed_general for c in db_session.query(Candidate)}
    assert flags == {"S6ME00001": False, "S6ME00002": True, "S6ME00003": True}
    assert results["LA"]["unconfirmed"] == 1


@pytest.mark.asyncio
async def test_a_ballot_candidate_with_no_fec_filing_is_shown(db_session, only):
    _race(db_session, "2026-HOUSE-LA-2", "LA", office="H", district=2)
    _db_cand(db_session, "H6LA02001", "2026-HOUSE-LA-2", "CARTER, TROY A. SR.", "DEM")
    db_session.commit()
    only("LA", [_rec("H", 2, "D", "Carter", "Troy A. Carter, Sr."),
                _rec("H", 2, "I", "Beach", 'Walter "Rocky" Beach')])

    results = await sc.sync_confirmed_candidates(db_session, None, 2026)

    beach = db_session.query(Candidate).filter(Candidate.id.startswith("ballot:")).one()
    assert beach.name == 'BEACH, WALTER "ROCKY"'
    assert beach.party == "IND"
    assert beach.confirmed_general is True
    assert beach.fec_filed is False
    assert results["LA"]["ballotOnly"] == 1
    assert results["LA"]["unmatched"] == 0


@pytest.mark.asyncio
async def test_a_ballot_only_row_gives_way_once_the_candidate_files(db_session, only):
    _race(db_session, "2026-HOUSE-LA-2", "LA", office="H", district=2)
    db_session.commit()
    only("LA", [_rec("H", 2, "I", "Beach", 'Walter "Rocky" Beach')])
    await sc.sync_confirmed_candidates(db_session, None, 2026)
    assert db_session.query(Candidate).filter(Candidate.id.startswith("ballot:")).count() == 1

    # He files with the FEC; the next run matches the real record, and the
    # placeholder must not survive beside it (or win the match).
    _db_cand(db_session, "H6LA02099", "2026-HOUSE-LA-2", "BEACH, WALTER", "IND")
    db_session.commit()
    await sc.sync_confirmed_candidates(db_session, None, 2026)

    rows = {c.id: c.confirmed_general for c in db_session.query(Candidate)}
    assert rows == {"H6LA02099": True}


@pytest.mark.asyncio
async def test_a_failed_fetch_leaves_ballot_only_rows_alone(db_session, only):
    _race(db_session, "2026-HOUSE-LA-2", "LA", office="H", district=2)
    db_session.commit()
    only("LA", [_rec("H", 2, "I", "Beach", 'Walter "Rocky" Beach')])
    await sc.sync_confirmed_candidates(db_session, None, 2026)

    only("LA", None)  # the source is down: that says nothing about the ballot
    await sc.sync_confirmed_candidates(db_session, None, 2026)
    assert db_session.query(Candidate).filter(Candidate.id.startswith("ballot:")).count() == 1


@pytest.mark.asyncio
async def test_a_non_candidate_row_never_becomes_a_person(db_session, only):
    _race(db_session, "2026-HOUSE-LA-2", "LA", office="H", district=2)
    db_session.commit()
    only("LA", [_rec("H", 2, None, "Scattering", "Write-In Scattering"),
                _rec("H", 2, "R", "Smith", "Smith")])  # a surname alone is not a ballot entry

    results = await sc.sync_confirmed_candidates(db_session, None, 2026)

    assert db_session.query(Candidate).count() == 0
    assert results["LA"]["unmatched"] == 2


@pytest.mark.asyncio
async def test_primary_results_never_unconfirm_anyone(db_session, only):
    # Pennsylvania reads primary results: a later fetch that doesn't list a
    # nominee is not evidence they left the ballot.
    _race(db_session, "2026-SEN-PA", "PA")
    _db_cand(db_session, "S6PA00001", "2026-SEN-PA", "NOMINEE, JANE", "DEM", confirmed_general=True)
    db_session.commit()
    only("PA", [_rec("S", None, "R", "Other", "Some Other")])

    await sc.sync_confirmed_candidates(db_session, None, 2026)

    assert db_session.query(Candidate).filter(Candidate.id == "S6PA00001").one().confirmed_general is True


def test_match_folds_accents_and_reads_married_names():
    sanchez = _c("SANCHEZ, LINDA", "DEM", "a")
    assert _match_candidate([sanchez, _c("LEE, AL", "REP", "b")], "Sánchez", "D") is sanchez
    hinson = _c("ARENHOLZ, ASHLEY HINSON", "REP", "c")
    assert _match_candidate([hinson, _c("TUREK, JOSHUA", "DEM", "d")], "Hinson", "R") is hinson


def test_match_uses_the_given_name_between_two_same_party_namesakes():
    eric, mayra = _c("FLORES, ERIC", "REP", "e"), _c("FLORES, MAYRA NOHEMI", "REP", "m")
    assert _match_candidate([eric, mayra], "FLORES", "R", "Mayra Flores") is mayra
    assert _match_candidate([eric, mayra], "FLORES", "R") is None


def test_match_tolerates_one_transposed_letter_only_with_the_given_name():
    fec = _c("DAUGHTERY, BRANDON", "LIB", "x")
    assert _match_candidate([fec], "Daugherty", "L", "Brandon Coulter Daugherty") is fec
    assert _match_candidate([fec], "Daugherty", "L") is None
    assert _match_candidate([fec], "Daugherty", "L", "Kevin Daugherty") is None


def _c(name, party, cid):
    return SimpleNamespace(name=name, party=party, id=cid, has_raised_funds=False, contributions=0)


# --- Wisconsin: the certified canvass, and a state's own fallback source ---

from app.pipeline.fetch.state_candidates_canvass_summary_pdf import parse_canvass  # noqa: E402

WI_LINES = """WEC Canvass Reporting System
Office GOVERNOR Total Votes: 1,283,728
Party: Republican Total Votes: 491,013
Winner 468,019 95.32% Tom Tiffany Republican
Office REPRESENTATIVE IN CONGRESS DISTRICT 1 Total Votes: 122,554
Party: Republican Total Votes: 51,119
Winner 50,915 99.6% Bryan Steil Republican
204 .4%
SCATTERING
Party: Democratic 71,403
Total Votes:
15,455 21.64% Peter Burgelis Democrat
Winner 31,494 44.11% Mitchell Berman Democrat
Party: REPRESENTATIVE IN CONGRESS DISTRICT 1 - Constitution Total Votes: 13
Winner 13 100% SCATTERING
Office REPRESENTATIVE IN CONGRESS DISTRICT 2 Total Votes: 165,383
Party: Republican Total Votes: 1,179
Winner 1,179 100% SCATTERING
Party: Democratic Total Votes: 164,183
Winner 144,365 87.93% Mark Pocan Democrat
Report Generated - 8/27/2026 11:44:51 AM Page 5 of 79
Office REPRESENTATIVE IN CONGRESS DISTRICT 2 Total Votes: 165,383
Party: Democratic Total Votes: 164,183
208 .13% SCATTERING
Office REPRESENTATIVE IN CONGRESS DISTRICT 6 Total Votes: 150,000
Party: Wisconsin Green Total Votes: 62
Winner 62 100% Matthew Arndt Wisconsin
Green
Party: Libertarian Total Votes: 9
Winner 9 100% Democrat
""".splitlines()


def test_canvass_reads_the_states_own_winner_marks():
    got = {(r["district"], r["party"], r["display_name"]) for r in parse_canvass(WI_LINES)}
    assert got == {
        (1, "R", "Bryan Steil"),
        (1, "D", "Mitchell Berman"),   # the marked winner, not the first name listed
        (2, "D", "Mark Pocan"),        # once, though a page break repeats the office
        (6, "G", "Matthew Arndt"),     # party wrapped onto the next line
    }
    # Governor is not federal; SCATTERING is not a person; a Winner line
    # whose name was lost ("Winner 9 100% Democrat") names nobody.


@pytest.mark.asyncio
async def test_a_states_fallback_runs_when_its_source_returns_nothing(db_session, monkeypatch):
    async def no_calendar(client, cycle):
        return {}
    monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
    monkeypatch.setattr(sc, "configured_states", lambda: {"WI"})
    monkeypatch.setitem(sc.STRATEGIES, "canvass_summary_pdf", AsyncMock(return_value=None))
    fallback = AsyncMock(return_value=[_rec("H", 2, "D", "Pocan", "Mark Pocan")])
    monkeypatch.setitem(sc.STRATEGIES, "google_civic", fallback)
    _race(db_session, "2026-HOUSE-WI-2", "WI", office="H", district=2)
    _db_cand(db_session, "H8WI02156", "2026-HOUSE-WI-2", "POCAN, MARK", "DEM")
    db_session.commit()

    results = await sc.sync_confirmed_candidates(db_session, None, 2026)

    assert fallback.await_count == 1
    assert results["WI"]["confirmed"] == 1


# --- Which source answered decides "confirmed" vs "nominees" ---

from app.api import elections as elections_api  # noqa: E402


def test_a_complete_ballot_readmits_no_unopposed_filer(db_session):
    _race(db_session, "2026-SEN-CO", "CO")
    _db_cand(db_session, "S6CO1", "2026-SEN-CO", "HICKENLOOPER, JOHN", "DEM", confirmed_general=True)
    _db_cand(db_session, "S6CO2", "2026-SEN-CO", "SOLO, REP FILER", "REP", incumbent_challenge="C")
    db_session.commit()
    race = db_session.query(Race).filter(Race.id == "2026-SEN-CO").one()

    assert {c.id for c in elections_api._confirmed_or_all(race.candidates, "CO", False)} == {"S6CO1", "S6CO2"}
    # A party missing from a certified ballot has nobody on it.
    assert {c.id for c in elections_api._confirmed_or_all(race.candidates, "CO", True)} == {"S6CO1"}


@pytest.mark.asyncio
async def test_a_fallback_answer_is_labelled_nominees_not_confirmed(db_session, monkeypatch):
    async def no_calendar(client, cycle):
        return {}
    monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
    monkeypatch.setattr(sc, "configured_states", lambda: {"CO"})
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=None))
    monkeypatch.setitem(sc.STRATEGIES, "clarity", AsyncMock(return_value=[_rec("S", None, "D", "Hickenlooper", "John Hickenlooper")]))
    _race(db_session, "2026-SEN-CO", "CO")
    _db_cand(db_session, "S6CO1", "2026-SEN-CO", "HICKENLOOPER, JOHN", "DEM")
    db_session.commit()

    await sc.sync_confirmed_candidates(db_session, None, 2026)

    # CO's entry claims a complete ballot, but tonight its fallback (primary
    # results) answered, so the page must say "nominees".
    assert elections_api._ballot_complete(db_session, "CO", 2026) is False
