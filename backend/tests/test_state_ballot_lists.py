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


# --- certified_table options Tennessee needed ---

from app.pipeline.fetch.state_candidates_certified_table import (  # noqa: E402
    fetch_confirmed_candidates as fetch_certified_table,
    parse_certified_rows,
)
from app.pipeline.fetch.state_candidates_common import parse_office  # noqa: E402


def test_united_states_house_is_a_federal_label():
    assert parse_office("United States House of Representatives District 1") == ("H", 1)
    # A bare "House of Representatives" is still a state chamber's name.
    assert parse_office("House of Representatives District 4") is None


def test_certified_rows_by_office_label_and_deduplicated():
    rows = [
        {"Office": "United States Senate", "Candidate": "Bill Hagerty", "Party Name": "Republican"},
        {"Office": "United States Senate", "Candidate": "Tharon Chandler", "Party Name": "Independent"},
        {"Office": "United States House of Representatives District 1", "Candidate": "Diana Harshbarger", "Party Name": "Republican"},
        {"Office": "United States House of Representatives District 1", "Candidate": "Diana Harshbarger", "Party Name": "Republican"},
        {"Office": "Governor", "Candidate": "Not Federal", "Party Name": "Democratic"},
    ]
    fmt = {"office_column": "Office", "office_parse": True, "party_column": "Party Name", "name_columns": ["Candidate"]}
    got = {(r["office"], r["district"], r["display_name"], r["party"]) for r in parse_certified_rows(rows, fmt)}
    assert got == {("S", None, "Bill Hagerty", "R"), ("S", None, "Tharon Chandler", "I"), ("H", 1, "Diana Harshbarger", "R")}


@pytest.mark.asyncio
async def test_every_listed_file_is_required():
    # A Senate list without its House list is half a ballot, and half a
    # ballot would unconfirm real nominees.
    page = '<a href="/s/USSenate_Nov2026.xlsx">x</a>'  # the House file is missing

    def handler(request):
        return httpx.Response(200, text=page)

    source = {"discovery": {"page_url": "https://sos.test/{year}-lists",
                            "link_regexes": ['href="([^"]*USSenate_Nov{year}\\.xlsx)"',
                                             'href="([^"]*USHouse_Nov{year}\\.xlsx)"']},
              "format": {"office_column": "Office", "office_parse": True,
                         "party_column": "Party", "name_columns": ["Candidate"]}}
    async with _client(handler) as client:
        assert await fetch_certified_table(client, 2026, "TN", source) is None


@pytest.mark.asyncio
async def test_a_certified_general_list_decides_federal_races_over_primary_results(db_session, monkeypatch):
    # Maine 2026 in miniature: the primary results still name Platner, the
    # certified list names Jackson. The list runs first and alone decides;
    # Platner is never confirmed, not even for a moment within the run.
    async def no_calendar(client, cycle):
        return {}
    monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
    monkeypatch.setattr(sc, "configured_states", lambda: {"ME"})
    monkeypatch.setitem(sc.STRATEGIES, "me_results", AsyncMock(return_value=[_rec("S", None, "D", "Platner", "Graham Platner")]))
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=[_rec("S", None, "D", "Jackson", "Troy D. Jackson")]))
    _race(db_session, "2026-SEN-ME", "ME")
    _db_cand(db_session, "S6ME1", "2026-SEN-ME", "PLATNER, GRAHAM", "DEM")
    _db_cand(db_session, "S6ME2", "2026-SEN-ME", "JACKSON, TROY", "DEM")
    db_session.commit()

    await sc.sync_confirmed_candidates(db_session, None, 2026)

    flags = {c.id: c.confirmed_general for c in db_session.query(Candidate)}
    assert flags == {"S6ME1": False, "S6ME2": True}
    assert elections_api._ballot_complete(db_session, "ME", 2026) is True


# --- Florida's candidate list ---

from app.pipeline.fetch.state_candidates_dos_canlist import parse_canlist  # noqa: E402

FL_PAGE = """<html><body>
<b>United States Senator</b>
<table class="results"><tr><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td><a>Moody</a>, <a>Ashley</a> (REP) *Incumbent</td><td>Qualified</td><td>Won</td><td></td></tr>
<tr><td>Gleason, Chris (REP)</td><td>Defeated</td><td>Eliminated</td><td></td></tr>
<tr><td>Gillespie, Neil J. (NPA)</td><td>Qualified</td><td></td><td></td></tr>
<tr><td>Toulme, Alix Christopher (WRI)</td><td>Qualified</td><td></td><td></td></tr>
</table>
<b>United States Representative</b>
<table class="results"><tr><th>District</th><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td>1</td><td>Patronis, Jimmy (REP) *Incumbent</td><td>Qualified</td><td>Won</td><td></td></tr>
<tr><td></td><td>Valimont, Gay (DEM)</td><td>Qualified</td><td>Unopposed</td><td></td></tr>
<tr><td></td><td>Barnes, Henry L. "Rick" (DEM)</td><td>Withdrew</td><td></td><td></td></tr>
<tr><td>10</td><td>Frost, Maxwell Alejandro (DEM) *Incumbent</td><td>Unopposed</td><td>Unopposed</td><td>Unopposed</td></tr>
</table></body></html>"""


def test_florida_list_keeps_the_ballot_and_carries_the_district_forward():
    got = {(r["office"], r["district"], r["display_name"], r["party"]) for r in parse_canlist(FL_PAGE)}
    assert got == {
        ("S", None, "Ashley Moody", "R"),
        ("S", None, "Neil J. Gillespie", "I"),       # no-party is an ordinary entry
        ("H", 1, "Jimmy Patronis", "R"),
        ("H", 1, "Gay Valimont", "D"),               # district carried from the row above
        ("H", 10, "Maxwell Alejandro Frost", "D"),   # unopposed: the seat's only candidate
    }
    # Defeated, withdrawn and declared write-ins are not on the ballot.


from app.pipeline.fetch.state_candidates_certified_table import pdf_table_rows  # noqa: E402


def _words(*cells, top):
    """One printed row: (x0, text) per word, each word 20pt wide."""
    return [{"x0": x, "x1": x + 20, "top": top, "bottom": top + 8, "text": t} for x, t in cells]


def test_pdf_table_reads_cells_under_their_headings_and_carries_the_office():
    # Iowa's layout in miniature: headings centred over left-aligned data,
    # the office printed once per group, a governor's group after the
    # federal ones and a footer at the foot of the page. Hinson's short
    # address reaches no heading; Turek's full one shows where the Address
    # column starts, so Hinson's still lands there and not in her name.
    header = _words((50, "Office"), (160, "Party"), (226, "Ballot"), (247, "Name"), (386, "Address"), top=80)
    page = header + [
        *_words((8, "United"), (29, "States"), (50, "Senator"), (156, "Republican"), (201, "Ashley"),
                (222, "Hinson"), (302, "PO"), (323, "Box"), top=90),
        *_words((156, "Democratic"), (201, "Josh"), (222, "Turek"), (302, "PO"), (323, "Box"),
                (344, "1005,"), (365, "Council"), (386, "Bluffs"), top=110),
        *_words((8, "Governor"), (156, "Republican"), (201, "Zach"), (222, "Lahn"), top=130),
        *_words((156, "Democratic"), (201, "Rob"), (222, "Sand"), top=150),
        *_words((8, "Ballot"), (29, "vacancies"), (50, "may"), (71, "be"), (92, "filled"), top=170),
    ]
    fmt = {"office_column": "Office", "office_parse": True, "office_fill_down": True,
           "party_column": "Party", "name_columns": ["Ballot Name"]}
    rows = pdf_table_rows([page], ["Office", "Party", "Ballot Name"])
    assert rows[0]["Ballot Name"] == "Ashley Hinson" and rows[0]["Address"] == "PO Box"
    got = [(r["office"], r["display_name"], r["party"]) for r in parse_certified_rows(rows, fmt)]
    # Rob Sand, under Governor, never inherits the Senate office above it.
    assert got == [("S", "Ashley Hinson", "R"), ("S", "Josh Turek", "D")]


def test_certified_rows_read_the_district_column_after_the_office_and_keep_only_active():
    # Maryland: the office and the district are separate columns, and a
    # withdrawn or failed petitioner stays in the file with a status.
    rows = [
        {"Office Name": "Representative in Congress", "Contest": "Congressional District 5",
         "Last": "Hall", "First": "Mildred Marie", "Party": "Other Candidates", "Status": "Active"},
        {"Office Name": "Representative in Congress", "Contest": "Congressional District 5",
         "Last": "Jordan", "First": "Brian S.", "Party": "Unaffiliated",
         "Status": "Failed to Submit Required Number of Signatures - 08/11/2026"},
        {"Office Name": "State Senator", "Contest": "Legislative District 1",
         "Last": "McKay", "First": "Mike", "Party": "Republican", "Status": "Active"},
        {"Office Name": "U.S. Senator", "Contest": "State Of Maryland",
         "Last": "Osborn", "First": "Dan", "Party": "By Petition", "Status": "Active"},
    ]
    fmt = {"office_column": "Office Name", "office_parse": True, "district_column": "Contest",
           "party_column": "Party", "surname_column": "Last", "name_columns": ["First", "Last"],
           "status_column": "Status", "status_values": ["Active"]}
    got = {(r["office"], r["district"], r["display_name"], r["party"]) for r in parse_certified_rows(rows, fmt)}
    assert got == {("H", 5, "Mildred Marie Hall", None), ("S", None, "Dan Osborn", "I")}


def test_match_reads_a_two_word_ballot_surname_filed_as_one_word():
    delaney = _cand("DELANEY, APRIL MCCLAIN", "DEM", "H4MD06")
    assert _match_candidate([delaney, _cand("FICKER, ROBIN", "REP", "H0MD06")], "McClain Delaney", "D",
                            "April McClain Delaney") is delaney
