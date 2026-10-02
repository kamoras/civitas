"""State offices read from a certified November candidate list
(state_candidates_certified_table with `statewide_offices` on the
general_list entry) — Wyoming, New Mexico and Tennessee.

Every fixture row below is real, copied from the state's own 2026 list as
fetched on 2026-09-28 and trimmed to a handful of contests; only the
columns the adapter never reads (addresses, phones, emails, websites) are
blanked:

  WY  sos.wyo.gov/Elections/Docs/2026/2026_WY_General_Election_Candidates.csv
  NM  candidateportal.servis.sos.state.nm.us/CandidateList.aspx?cty=99
      (served as UTF-8 with no charset in the page)
  TN  sos.tn.gov/elections/2026-candidate-lists (Governor_Nov2026.xlsx,
      TNHouse_Nov2026.xlsx, USHouse_Nov2026.xlsx), as _xlsx_rows reads them

Each state's `format` is taken from the real sources file, so a change
there is tested here too.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.elections import StatewideCoverageStatus, _statewide_marker, _statewide_section
from app.models import Race, StateLegNominee, StatewideNominee
from app.pipeline.fetch import state_candidates as sc
from app.pipeline.fetch.state_candidates_certified_table import (
    _rows,
    fetch_confirmed_candidates,
    parse_certified_rows,
)

_SOURCES = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "data" / "state_candidate_sources.json").read_text()
)["states"]


def _general(state):
    return _SOURCES[state]["general_list"]


def _split(records):
    federal = {(r["office"], r["district"], r["display_name"], r["party"])
               for r in records if r["office"] in ("S", "H")}
    statewide = {(r["office"], r["party"], r["last_name"])
                 for r in records if r["office"] in sc.STATEWIDE_OFFICE_LABELS}
    leg = {(r["office"], r["district"], r["party"], r["last_name"])
           for r in records if r["office"] in sc.STATE_LEG_CHAMBER_LABELS}
    return federal, statewide, leg


# ── Wyoming ──────────────────────────────────────────────────────────

WY_CSV = '''"Election","Office Sought","Party Affiliation","Candidate Last Name","Candidate First Name","Candidate Middle Name","Candidate Suffix","Mailing Address","Mailing City State & Zip","Campaign Telephone","Email Address","Web Address","Date Filed","Date Withdrawn","Ballot Name"
"2026 GENERAL ELECTION","UNITED STATES SENATOR","REP","HAGEMAN","HARRIET","MAXINE","","","","","","","05/15/2026","","Harriet Hageman"
"2026 GENERAL ELECTION","UNITED STATES REPRESENTATIVE","CT","HAGGIT","JEFFREY","A.","","","","","","","07/17/2026","","Jeffrey Haggit"
"2026 GENERAL ELECTION","GOVERNOR","REP","BARLOW","ERIC","","","","","","","","05/16/2026","","Eric Barlow"
"2026 GENERAL ELECTION","GOVERNOR","DEM","CASNER","KENNETH","R","","","","","","","05/14/2026","","Kenneth R. Casner"
"2026 GENERAL ELECTION","GOVERNOR","CT","BEXTEL","REBECCA","","","","","","","","07/17/2026","","Rebecca Bextel"
"2026 GENERAL ELECTION","SECRETARY OF STATE","REP","SHORT","ROBERT","","","","","","","","05/20/2026","","Robert Short"
"2026 GENERAL ELECTION","STATE AUDITOR","REP","RACINES","KRISTI","","","","","","","","05/14/2026","","Kristi Racines"
"2026 GENERAL ELECTION","STATE TREASURER","REP","MEIER","CURTIS","E","JR","","","","","","05/19/2026","","Curt Meier"
"2026 GENERAL ELECTION","SUPERINTENDENT OF PUBLIC INSTRUCTION","DEM","CORDOVA","ANA","","","","","","","","05/15/2026","","Ana Cordova"
"2026 GENERAL ELECTION","STATE SENATOR 11","CT","VAN MATRE","DONALD","G","","","","","","","07/17/2026","","Don VanMatre"
"2026 GENERAL ELECTION","STATE REPRESENTATIVE 01","REP","CLAYCOMB","TROY","","","","","","","","05/14/2026","","Troy Claycomb"
"2026 GENERAL ELECTION","STATE REPRESENTATIVE 58","LBR","PORAMBO","JOSEPH","STANLEY","","","","","","","08/17/2026","","Joseph Porambo"
"2026 GENERAL ELECTION","SC-02 - JUSTICE OF THE SUPREME COURT","","Hill","Bridget","","","","","","","","06/01/2026","","Bridget Hill"
'''


class TestWyoming:
    def _records(self, state_offices=True):
        fmt = _general("WY")["format"]
        return parse_certified_rows(_rows(WY_CSV.encode(), "wy.csv", fmt), fmt, state_offices)

    def test_every_executive_contest_on_the_roster(self):
        _, statewide, _ = _split(self._records())
        assert statewide == {
            ("governor", "R", "Eric Barlow"),
            ("governor", "D", "Kenneth R. Casner"),
            ("governor", "C", "Rebecca Bextel"),       # Constitution: on the ballot, no primary
            ("secretary_of_state", "R", "Robert Short"),
            ("auditor", "R", "Kristi Racines"),
            ("treasurer", "R", "Curt Meier"),
            ("school_superintendent", "D", "Ana Cordova"),
        }

    def test_seats_printed_with_no_word_district(self):
        _, _, leg = _split(self._records())
        assert leg == {
            ("upper", "11", "C", "Don VanMatre"),
            ("lower", "1", "R", "Troy Claycomb"),
            ("lower", "58", "L", "Joseph Porambo"),
        }

    def test_a_retention_justice_is_neither_federal_nor_state_office(self):
        assert not any("Hill" in r["last_name"] or "Hill" in r.get("display_name", "")
                       for r in self._records())

    def test_federal_records_are_unchanged_by_the_opt_in(self):
        assert _split(self._records())[0] == _split(self._records(False))[0] == {
            ("S", None, "Harriet Hageman", "R"), ("H", None, "Jeffrey Haggit", "C"),
        }

    def test_without_the_opt_in_no_state_record(self):
        assert all(r["office"] in ("S", "H") for r in self._records(False))

    @pytest.mark.asyncio
    async def test_end_to_end_from_the_elections_page(self):
        def handler(request):
            if request.url.path == "/Elections/Default.aspx":
                return httpx.Response(200, text="<a href='/Elections/Docs/2026/2026_WY_General_Election_Candidates.csv'>csv</a>")
            return httpx.Response(200, content=WY_CSV.encode())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            records = await fetch_confirmed_candidates(client, 2026, "WY", _general("WY"))
        assert ("governor", "R", "Eric Barlow") in _split(records)[1]


# ── New Mexico ───────────────────────────────────────────────────────

_NM_HEAD = ["Contest", "District", "Filing County", "Name", "First Name", "Middle Name", "Last Name",
            "Party", "Email", "Status"]
_NM_ROWS = [
    ("United States Senator", "", "", "BEN", "R", "LUJAN", "DEM", "Qualified"),
    ("United States Representative", "DISTRICT 1", "", "MELANIE", "ANN", "STANSBURY", "DEM", "Qualified"),
    ("Governor", "", "", "INSLEY", "", "EVANS, SR", "DTS", "Disqualified"),
    ("Governor and Lieutenant Governor", "", "", "GREGGORY D HULL", "AND", "DAVID M GALLEGOS", "REP", "Qualified"),
    ("Governor and Lieutenant Governor", "", "", "DEB HAALAND", "AND", "STEPHANIE GARCIA RICHARD", "DEM", "Qualified"),
    ("Lieutenant Governor", "", "", "MAGGIE", "", "TOULOUSE OLIVER", "DEM", "Withdrawn"),
    ("Secretary of State", "", "", "AMANDA", "", "LÓPEZ ASKIN", "DEM", "Qualified"),
    ("State Auditor", "", "", "MICHAEL", "J", "VIGIL", "FWD", "Disqualified"),
    ("Commissioner of Public Lands", "", "", "JUAN DE JESUS", "", "SANCHEZ, III", "DEM", "Qualified"),
    ("State Representative", "DISTRICT 22", "Bernalillo", "ZACHARY", "P", "WITHERS", "DTS", "Qualified"),
    ("County Sheriff", "", "Bernalillo", "JOHN", "D", "ALLEN", "DEM", "Qualified"),
    ("Public Education Commissioner", "DISTRICT 3", "Bernalillo", "JACOB", "A", "TRUJILLO", "DEM", "Qualified"),
    ("State Senator", "DISTRICT 33", "Lincoln", "REX", "A", "WILSON", "REP", "Qualified"),
]


def _nm_page() -> bytes:
    cell = "".join
    head = cell(f"<th>{h}</th>" for h in _NM_HEAD)
    body = cell(
        "<tr>" + cell(f"<td>{v}</td>" for v in (c, d, f, f"{a} {b} {last}", a, b, last, p, "", s)) + "</tr>"
        for c, d, f, a, b, last, p, s in _NM_ROWS
    )
    # No <meta charset>, exactly like the portal: the bytes are UTF-8.
    return (f"<html><head><title>2026 General Election Contest/Candidate List</title></head>"
            f"<body><table><tr>{head}</tr>{body}</table></body></html>").encode("utf-8")


class TestNewMexico:
    def _records(self):
        fmt = _general("NM")["format"]
        return parse_certified_rows(_rows(_nm_page(), "nm", fmt), fmt, True)

    def test_the_joint_ticket_is_the_governors_contest_and_only_qualified_rows_count(self):
        _, statewide, _ = _split(self._records())
        assert statewide == {
            ("governor", "R", "GREGGORY D HULL AND DAVID M GALLEGOS"),
            ("governor", "D", "DEB HAALAND AND STEPHANIE GARCIA RICHARD"),
            ("secretary_of_state", "D", "AMANDA LÓPEZ ASKIN"),   # UTF-8, not "LÃ\x93PEZ"
            ("public_lands_commissioner", "D", "JUAN DE JESUS SANCHEZ, III"),
            ("public_education_commission", "D", "JACOB A TRUJILLO"),
        }

    def test_a_county_office_is_refused_and_a_district_seated_commission_is_read(self):
        # A county sheriff is refused. The Public Education Commission is a
        # statewide body seated by district: listed per district, like the
        # legislature (the one rule for district-seated bodies).
        records = self._records()
        _, _, leg = _split(records)
        assert leg == {("lower", "22", "I", "ZACHARY P WITHERS"), ("upper", "33", "R", "REX A WILSON")}
        assert {(r["office"], r["district"], r["party"], r["last_name"]) for r in records
                if r["office"] == "public_education_commission"} == {
            ("public_education_commission", "3", "D", "JACOB A TRUJILLO"),
        }
        assert not any("sheriff" in r["office"] for r in records)


# ── Tennessee ────────────────────────────────────────────────────────

TN_ROWS = [
    {"Office": "United States House of Representatives District 6", "Candidate": "Johnny Garrett",
     "Party Name": "Republican", "City": ""},
    {"Office": "Governor", "Candidate": "Marla Blackwood", "Party Name": "Republican", "City": ""},
    {"Office": "Governor", "Candidate": "Jerri Green", "Party Name": "Democratic", "City": ""},
    {"Office": "Governor", "Candidate": "Misam Abidi", "Party Name": "Independent", "City": ""},
    # The legislative files head the column "Party", not "Party Name".
    {"Office": "Tennessee House of Representatives District 3", "Candidate": "Lori Love",
     "Party": "Democratic", "City": ""},
    {"Office": "Tennessee House of Representatives District 93", "Candidate": "No Candidate Qualified",
     "Party": "", "City": ""},
    {"Office": "Tennessee Senate District 5", "Candidate": "Jimmy Matlock", "Party": "Republican", "City": ""},
]


class TestTennessee:
    def test_governor_and_legislature_with_either_party_heading(self):
        fmt = _general("TN")["format"]
        federal, statewide, leg = _split(parse_certified_rows(TN_ROWS, fmt, True))
        assert federal == {("H", 6, "Johnny Garrett", "R")}   # 43.6% plurality: no runoff
        assert statewide == {
            ("governor", "R", "Marla Blackwood"),
            ("governor", "D", "Jerri Green"),
            ("governor", "I", "Misam Abidi"),
        }
        assert leg == {("lower", "3", "D", "Lori Love"), ("upper", "5", "R", "Jimmy Matlock")}

    def test_all_five_files_are_required(self):
        # A partial set would record a governor-less (or legislature-less)
        # Tennessee as checked.
        regexes = _general("TN")["discovery"]["link_regexes"]
        for name in ("USSenate", "USHouse", "Governor", "TNSenate", "TNHouse"):
            assert any(name in rx for rx in regexes), name


# ── the pipeline: which source speaks for state offices ──────────────

def _rec(office, district, party, last, display=None):
    rec = {"office": office, "district": district, "party": party, "last_name": last}
    if display:
        rec["display_name"] = display
    return rec


def _state_fixture(db_session, monkeypatch, state, race_id, district):
    async def no_calendar(client, cycle):
        return {}, False
    monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
    monkeypatch.setattr(sc, "configured_states", lambda: {state})
    db_session.add(Race(id=race_id, cycle_year=2026, office="H", state=state, district=district,
                        is_special=False))
    db_session.commit()


@pytest.fixture()
def tennessee(db_session, monkeypatch):
    _state_fixture(db_session, monkeypatch, "TN", "2026-HOU-TN-06", 6)
    return db_session


@pytest.mark.asyncio
async def test_a_certified_list_that_opts_in_supplies_the_state_offices(tennessee, monkeypatch):
    listed = [
        _rec("H", 6, "R", "Garrett", "Johnny Garrett"),
        _rec("governor", None, "R", "Marla Blackwood"),
        _rec("governor", None, "I", "Misam Abidi"),
        _rec("upper", "5", "R", "Jimmy Matlock"),
    ]
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=listed))
    # Primary results carry no state office for Tennessee.
    monkeypatch.setitem(sc.STRATEGIES, "tn_precinct", AsyncMock(return_value=[]))

    await sc.sync_confirmed_candidates(tennessee, None, 2026)

    assert {(r.office, r.party, r.display_name) for r in tennessee.query(StatewideNominee)} == {
        ("governor", "R", "Marla Blackwood"), ("governor", "I", "Misam Abidi"),
    }
    assert {(r.chamber, r.district) for r in tennessee.query(StateLegNominee)} == {("upper", "5")}
    _, coverage = _statewide_section(tennessee, "TN", 2026)
    assert coverage["status"] == StatewideCoverageStatus.COVERED
    assert coverage["sourceName"] == _general("TN")["source_name"]


@pytest.mark.asyncio
async def test_a_list_that_did_not_answer_claims_nothing(tennessee, monkeypatch):
    # The primary results answered, but they are not what opted in: an
    # empty sync from them would read as "Tennessee elects no governor".
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=None))
    monkeypatch.setitem(sc.STRATEGIES, "tn_precinct", AsyncMock(return_value=[
        _rec("H", 6, "R", "Garrett", "Johnny Garrett"),
    ]))

    await sc.sync_confirmed_candidates(tennessee, None, 2026)

    assert _statewide_marker(tennessee, "TN", 2026) is None
    assert tennessee.query(StatewideNominee).count() == 0


# ── the two parser additions ─────────────────────────────────────────

def test_new_mexicos_land_office_is_not_south_dakotas():
    from app.pipeline.fetch.state_candidates_common import parse_statewide_office
    assert parse_statewide_office("Commissioner of Public Lands") == ("public_lands_commissioner", None)
    assert parse_statewide_office("Commissioner of School and Public Lands") == (
        "school_public_lands_commissioner", None)
    assert parse_statewide_office("County Commissioner of Public Lands") is None


def test_a_bare_seat_number_is_read_only_as_the_whole_label():
    from app.pipeline.fetch.state_candidates_common import parse_state_leg_office
    assert parse_state_leg_office("STATE SENATOR 11") == ("upper", "11", None)
    assert parse_state_leg_office("STATE REPRESENTATIVE 01") == ("lower", "1", None)
    # A number anywhere else in a longer label is not a district.
    assert parse_state_leg_office("State Representative 5 Seat A") is None
    assert parse_state_leg_office("State Senator 2026 Special") is None


# ── until the certified list publishes, the primary results stand in ──

@pytest.fixture()
def colorado(db_session, monkeypatch):
    # Colorado's main source (Clarity primary results) opts in to state
    # offices itself, and so does its certified general_list.
    assert _SOURCES["CO"].get("statewide_offices") and _general("CO").get("statewide_offices")
    _state_fixture(db_session, monkeypatch, "CO", "2026-HOU-CO-08", 8)
    return db_session


_CO_PRIMARY = [
    _rec("H", 8, "R", "Evans", "Gabe Evans"),
    _rec("governor", None, "D", "Michael Bennet"),
    _rec("governor", None, "R", "Barbara Kirkmeyer"),
    _rec("upper", "5", "D", "Primary Winner"),
]


@pytest.mark.parametrize("listed", [None, [_rec("H", 8, "R", "Evans", "Gabe Evans")]])
@pytest.mark.asyncio
async def test_primary_results_stand_in_until_the_list_names_state_offices(colorado, monkeypatch, listed):
    # The list is not published yet (None), or carries its federal rows
    # only: the main source's own opted-in primary results supply the
    # state offices, as they did before the list was configured.
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=listed))
    monkeypatch.setitem(sc.STRATEGIES, "clarity", AsyncMock(return_value=_CO_PRIMARY))

    await sc.sync_confirmed_candidates(colorado, None, 2026)

    assert {(r.office, r.party, r.display_name) for r in colorado.query(StatewideNominee)} == {
        ("governor", "D", "Michael Bennet"), ("governor", "R", "Barbara Kirkmeyer"),
    }
    assert {(r.chamber, r.district) for r in colorado.query(StateLegNominee)} == {("upper", "5")}
    _, coverage = _statewide_section(colorado, "CO", 2026)
    assert coverage["status"] == StatewideCoverageStatus.COVERED
    assert coverage["sourceName"] == _SOURCES["CO"]["source_name"]
    assert coverage["ballotList"] is False


@pytest.mark.asyncio
async def test_once_the_list_named_state_offices_it_stays_the_source(colorado, monkeypatch):
    listed = [
        _rec("H", 8, "R", "Evans", "Gabe Evans"),
        _rec("governor", None, "D", "Michael Bennet"),
        _rec("governor", None, "R", "Barbara Kirkmeyer"),
        _rec("governor", None, "O", "An Independent"),
    ]
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=listed))
    monkeypatch.setitem(sc.STRATEGIES, "clarity", AsyncMock(return_value=_CO_PRIMARY))
    await sc.sync_confirmed_candidates(colorado, None, 2026)
    _, coverage = _statewide_section(colorado, "CO", 2026)
    assert coverage["sourceName"] == _general("CO")["source_name"]
    assert coverage["ballotList"] is True
    assert colorado.query(StateLegNominee).count() == 0

    # The list is down tonight. Its certified ballot (with the
    # independent) is not swapped back to the primary winners.
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=None))
    await sc.sync_confirmed_candidates(colorado, None, 2026)
    assert ("governor", "O", "An Independent") in {
        (r.office, r.party, r.display_name) for r in colorado.query(StatewideNominee)
    }
    _, coverage = _statewide_section(colorado, "CO", 2026)
    assert coverage["sourceName"] == _general("CO")["source_name"]


@pytest.mark.asyncio
async def test_a_joint_ticket_is_stored_as_one_governor_contest(colorado, monkeypatch):
    # Colorado elects the two on one vote: no Lieutenant Governor contest.
    listed = [
        _rec("H", 8, "R", "Evans", "Gabe Evans"),
        _rec("governor", None, "D", "Phil Weiser"),
        _rec("lt_governor", None, "D", "Lesley Dahlkemper"),
    ]
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=listed))
    monkeypatch.setitem(sc.STRATEGIES, "clarity", AsyncMock(return_value=_CO_PRIMARY))
    await sc.sync_confirmed_candidates(colorado, None, 2026)
    assert {(r.office, r.display_name) for r in colorado.query(StatewideNominee)} == {
        ("governor", "Phil Weiser and Lesley Dahlkemper"),
    }


# ── ballotList: where the STATE-office rows came from ────────────────

@pytest.mark.asyncio
async def test_north_carolinas_primary_results_are_not_called_the_ballot(db_session, monkeypatch):
    """North Carolina's general_ballot_complete describes its federal FILING
    list (sync_ballot_filings). Its state offices and judgeships come from
    primary results, so the page must not call them every candidate on the
    November ballot."""
    from app.api.elections import _judicial_marker

    assert _SOURCES["NC"].get("general_ballot_complete") and _SOURCES["NC"]["strategy"] == "tabular"
    _state_fixture(db_session, monkeypatch, "NC", "2026-HOUSE-NC-1", 1)
    monkeypatch.setitem(sc.STRATEGIES, "tabular", AsyncMock(return_value=[
        _rec("H", 1, "D", "Davis", "Don Davis"),
        _rec("lower", "5", "R", "A Primary Winner"),
        {"office": "appeals", "district": None, "seat": "Seat 1", "party": "D", "last_name": "A Judge"},
    ]))
    await sc.sync_confirmed_candidates(db_session, None, 2026)
    assert _statewide_marker(db_session, "NC", 2026)["ballotList"] is False
    assert _judicial_marker(db_session, "NC", 2026)["ballotList"] is False


@pytest.mark.asyncio
async def test_a_certified_list_strategy_is_the_ballot(db_session, monkeypatch):
    # South Carolina's main source is its candidate-tracking list.
    assert _SOURCES["SC"]["strategy"] == "vrems"
    _state_fixture(db_session, monkeypatch, "SC", "2026-HOUSE-SC-1", 1)
    monkeypatch.setitem(sc.STRATEGIES, "vrems", AsyncMock(return_value=[
        _rec("H", 1, "R", "Dykes", "Mark Dykes"),
        _rec("governor", None, "R", "Alan Wilson and Mike Reichenbach"),
    ]))
    await sc.sync_confirmed_candidates(db_session, None, 2026)
    assert _statewide_marker(db_session, "SC", 2026)["ballotList"] is True


@pytest.mark.parametrize("from_general", [True, False])
@pytest.mark.asyncio
async def test_vermont_says_per_run_which_document_it_read(db_session, monkeypatch, from_general):
    from app.pipeline.fetch.state_candidates_common import SourceRecords

    _state_fixture(db_session, monkeypatch, "VT", "2026-HOUSE-VT-0", 0)
    monkeypatch.setitem(sc.STRATEGIES, "vt_enr", AsyncMock(return_value=SourceRecords([
        _rec("H", None, "D", "Balint", "Becca Balint"),
        _rec("governor", None, "R", "Phil Scott"),
    ], ballot_list=from_general)))
    await sc.sync_confirmed_candidates(db_session, None, 2026)
    assert _statewide_marker(db_session, "VT", 2026)["ballotList"] is from_general


# ── a partial state-office read keeps its settled federal rows ───────

@pytest.mark.asyncio
async def test_an_incomplete_state_office_read_confirms_federal_and_keeps_state_rows(db_session, monkeypatch):
    """Georgia-style: the primary settled, its runoff has not. The federal
    nominees are confirmed and the state is 'ok'; the stored state offices
    and judgeships are left exactly as they were, not deleted or marked
    checked from a partial list."""
    from app.api.elections import _judicial_marker
    from app.models import Candidate
    from app.pipeline.fetch.state_candidates_common import SourceRecords

    _state_fixture(db_session, monkeypatch, "GA", "2026-HOUSE-GA-1", 1)
    db_session.add(Candidate(id="H6GA01000", name="CARTER, BUDDY", party="REP", race_id="2026-HOUSE-GA-1"))
    db_session.add(StatewideNominee(state="GA", cycle_year=2026, office="lt_governor", district=None,
                                    party="R", display_name="Stored Earlier"))
    db_session.commit()
    monkeypatch.setitem(sc.STRATEGIES, "tabular", AsyncMock(return_value=SourceRecords(
        [_rec("H", 1, "R", "Carter", "Buddy Carter")], state_offices_incomplete=True,
    )))
    results = await sc.sync_confirmed_candidates(db_session, None, 2026)
    assert results["GA"]["status"] == "ok", results
    assert results["GA"]["confirmed"] == 1, results
    assert [r.display_name for r in db_session.query(StatewideNominee)] == ["Stored Earlier"]
    assert _statewide_marker(db_session, "GA", 2026) is None
    assert _judicial_marker(db_session, "GA", 2026) is None
