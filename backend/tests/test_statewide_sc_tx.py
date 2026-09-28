"""Statewide executive and legislative contests for South Carolina (vrems)
and Texas (tx_civix), both read from the state's own certified November
list rather than primary results.

Both fixtures are REAL, trimmed, captured 2026-09-28:

- fixtures_sc_vrems_all_offices.html: 41 of the 1,175 rows South
  Carolina's VREMS candidate tracking returned for ONE search of every
  office ("All", status Active) in the 11/3/2026 Statewide General
  Election — every executive and U.S. Senate row, three State House
  districts, and the local rows most likely to fool a gate (a county
  Auditor and Treasurer, a Probate Judge, a Solicitor, a public-works
  commissioner, and Fripp Island's "Public Service Commission").
- fixtures_tx_civix_state_offices.json: 71 of the 3,967 rows Civix
  returned for Texas's 2026 GE election — every row Civix types SW
  (statewide, judicial included), six SR rows (state by district: a
  House and a Senate district, a State Board of Education seat, a
  district judge, a court of appeals seat, a criminal district
  attorney), two county rows of each county type and the Senate rows.
  Fields not read by the adapter (addresses, e-mail) are dropped.

The Texas list already settles the May 26 runoffs: the runoff election's
own list (Civix id 58315/58314) paired Chip Roy with Mayes Middleton for
Attorney General, Jim Wright with Bo French for Railroad Commissioner,
Joe Jaworski with Nathan Johnson and Marcos Isaias Velez with Vikki
Goodwin; only the second of each pair is on the GE list.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.pipeline.fetch import state_candidates_tx as tx
from app.pipeline.fetch.state_candidates_common import (
    PARTY_CODE_MAP,
    STATEWIDE_OFFICE_LABELS,
    normalize_party,
    parse_statewide_office,
)
from app.pipeline.fetch.state_candidates_vrems import (
    _state_office_record,
    fetch_confirmed_candidates as fetch_vrems,
)

HERE = Path(__file__).parent
SC_TABLE = (HERE / "fixtures_sc_vrems_all_offices.html").read_text()
TX_ROWS = json.loads((HERE / "fixtures_tx_civix_state_offices.json").read_text())

SC_ELECTIONS = [
    {"electionId": "22596", "electionName": "Statewide General Election",
     "electionDate": "2026-11-03T00:00:00"},
    {"electionId": "22760", "electionName": "US Senate Special Republican Primary",
     "electionDate": "2026-08-11T00:00:00"},
]

# The real dropdown's shape, trimmed: its "All" choice, the executive
# offices, both federal ones and the House, whose label has no district.
SC_SEARCH_PAGE = """<html><body><form id="searchForm">
<input type="hidden" id="ElectionId" name="ElectionId" value="22596" />
<select id="SelectedOffice" name="SelectedOffice">
<option value="-1">All</option><option value="463">Governor and Lieutenant Governor</option>
<option value="373">State Superintendent of Education</option>
<option value="376">U.S. Senate</option><option value="378">U.S. House of Representatives</option>
<option value="380">State House of Representatives</option><option value="402">Auditor</option>
</select></form></body></html>"""


def _sc_client(page=SC_SEARCH_PAGE, asked=None):
    def handler(request):
        if request.url.path == "/Candidate/GetElections":
            return httpx.Response(200, json=SC_ELECTIONS)
        if request.method == "GET":
            return httpx.Response(200, text=page)
        body = request.content.decode()
        office = body.split('name="SelectedOffice"\r\n\r\n', 1)[1].split("\r\n", 1)[0]
        if asked is not None:
            asked.append(office)
        return httpx.Response(200, text=SC_TABLE)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _by_office(records):
    out = {}
    for r in records:
        out.setdefault(r["office"], set()).add((r["party"], r["last_name"]))
    return out


# ── South Carolina ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sc_reads_every_executive_contest_from_one_search():
    asked = []
    async with _sc_client(asked=asked) as client:
        records = await fetch_vrems(
            client, 2026, "SC", {"base_url": "https://vrems.test", "statewide_offices": True},
        )

    # One search of every office, not one per dropdown entry: the
    # dropdown's House entry carries no district.
    assert asked == ["-1"]
    offices = _by_office(r for r in records if r["office"] in STATEWIDE_OFFICE_LABELS)
    assert offices == {
        # The joint ticket is the governor's contest; the running mate is
        # not a separate one.
        "governor": {("U", "Michael Addison"), ("G", "Walid Hakim"),
                     ("D", "Jermaine Johnson"), ("R", "Alan Wilson")},
        "secretary_of_state": {("D", "Jason Belton"), ("R", "Mark Hammond")},
        "treasurer": {("D", "Vincent Coe"), ("R", "Curtis Loftis")},
        "attorney_general": {("D", "Richard Hricik"), ("R", "David Stumbo")},
        "comptroller": {("D", "Tiffany Boozer"), ("R", "Mike Burkhold")},
        "school_superintendent": {("U", "Baba Amin Ojuok"), ("R", "Ellen Weaver"),
                                  ("D", "Sylvia Wright")},
        "agriculture_commissioner": {("D", "DeShawn Blanding"), ("U", "Chris Nelums"),
                                     ("R", "Cody Simpson"), ("L", "Michael Sullens")},
    }
    # Nothing local got in: no Fripp Island PSC, no county Auditor or
    # Treasurer, no Probate Judge, no Solicitor.
    names = {r["last_name"] for r in records}
    assert not names & {"Gregory S Counts", "Sheridan Laughlin", "Raphael Coleman",
                        "Mark Sumner", "Lester M Gill Bell Jr", "Jerry Cromer"}

    house = {(r["district"], r["party"], r["last_name"]) for r in records if r["office"] == "lower"}
    assert house == {
        ("1", "R", "Bill Whitmire"), ("1", "D", "Jasmine Williams"),
        ("11", "D", "Jessica Beasley"), ("11", "R", "Craig Gagnon"), ("11", "U", "E T Stone"),
        ("26", "D", "Jenny Desch"), ("26", "R", "David W Martin"),
        # Kiral Mace (Workers): a party with no code of ours or FEC's, so
        # the neutral "O", with the party kept exactly as printed.
        ("26", "O", "Kiral Mace"),
    }
    mace = next(r for r in records if r["last_name"] == "Kiral Mace")
    assert mace["party_label"] == "Workers"
    # A recognised party is its code alone.
    assert all("party_label" not in r for r in records if r["office"] == "lower" and r["party"] != "O")
    # The federal rows still come through, from the same search.
    federal = {(r["office"], r["display_name"]) for r in records if r["office"] == "S"}
    assert federal == {("S", "Annie Andrews"), ("S", "Darline Graham"),
                       ("S", "Mark Hackett"), ("S", "Kasie Whitener")}


@pytest.mark.asyncio
async def test_sc_without_the_opt_in_asks_only_for_federal_offices():
    asked = []
    async with _sc_client(asked=asked) as client:
        records = await fetch_vrems(client, 2026, "SC", {"base_url": "https://vrems.test"})
    assert asked == ["376", "378"]
    assert {r["office"] for r in records} == {"S"}


@pytest.mark.asyncio
async def test_sc_opt_in_says_nothing_when_there_is_no_all_search():
    page = SC_SEARCH_PAGE.replace('<option value="-1">All</option>', "")
    async with _sc_client(page=page) as client:
        records = await fetch_vrems(
            client, 2026, "SC", {"base_url": "https://vrems.test", "statewide_offices": True},
        )
    # Per-office searches would miss the legislature, and a partial list
    # under the opt-in would read as a confirmed absence.
    assert records is None


def test_sc_statewide_label_on_a_county_row_is_refused():
    row = {"Office": "Secretary of State", "Associated Counties": "BEAUFORT",
           "Name on Ballot": "Someone Local", "Party": "Republican"}
    assert _state_office_record(row) is None
    row["Associated Counties"] = ""
    assert _state_office_record(row) == {
        "office": "secretary_of_state", "district": None, "party": "R",
        "last_name": "Someone Local",
    }


def test_shared_vocabulary_for_both_states():
    assert parse_statewide_office("State Superintendent of Education") == ("school_superintendent", None)
    assert parse_statewide_office("Comptroller General") == ("comptroller", None)
    assert parse_statewide_office("RAILROAD COMMISSIONER ") == ("railroad_commissioner", None)
    assert parse_statewide_office("COMMISSIONER OF THE GENERAL LAND OFFICE") == (
        "land_office_commissioner", None)
    # A special-purpose LOCAL district that names a "Public Service
    # Commission" is not Georgia's statewide PSC.
    assert parse_statewide_office(
        "Public Service District, Fripp Island Public Service Commission") is None
    assert parse_statewide_office("Public Service Commission, District 3") == (
        "public_service_commission", "3")
    assert normalize_party("United Citizens", ballot_list=True) == "U"
    assert PARTY_CODE_MAP["U"] == "UC"


# ── Texas ──────────────────────────────────────────────────────────────


def _tx_client():
    def handler(request):
        if "getElectionsByYear" in str(request.url):
            return httpx.Response(200, json=[{"idElection": 53815, "cdElectionType": "GE"}])
        return httpx.Response(200, json=TX_ROWS)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_tx_reads_its_executive_offices_and_legislature():
    async with _tx_client() as client:
        records = await tx.fetch_confirmed_candidates(client, 2026, "TX", {"statewide_offices": True})

    offices = _by_office(r for r in records if r["office"] in STATEWIDE_OFFICE_LABELS)
    assert offices == {
        # The rejected independents (Cunningham, Wysinger) and the four
        # declared write-ins are not on the ballot.
        "governor": {("R", "GREG ABBOTT"), ("D", "GINA HINOJOSA"), ("L", "PAT DIXON")},
        "lt_governor": {("R", "DAN PATRICK"), ("D", "VIKKI GOODWIN"),
                        ("G", "KEVIN MCCORMICK"), ("L", "ANTHONY CRISTO")},
        # Both parties' runoff winners (see module docstring).
        "attorney_general": {("R", "MAYES MIDDLETON"), ("D", "NATHAN JOHNSON"),
                             ("L", "TOM OXFORD")},
        "comptroller": {("R", "DON HUFFINES"), ("D", "SARAH ECKHARDT"),
                        ("G", "SHEHLA FAIZI"), ("L", "V. ALONZO ECHAVARRIA-GARZA")},
        "land_office_commissioner": {("R", "DAWN BUCKINGHAM"), ("D", "BENJAMIN FLORES"),
                                     ("L", "NEILL SNIDER")},
        # Alfred Molison Jr. (Green) is not CG — never certified.
        "agriculture_commissioner": {("R", "NATE SHEETS"), ("D", "CLAYTON TUCKER"),
                                     ("L", "AUSTIN R. KELLY")},
        "railroad_commissioner": {("R", "BO FRENCH"), ("D", "JON ROSENTHAL"),
                                  ("L", "ARTHUR DIBIANCA")},
        "state_board_of_education": {("R", "TAMMIE NIELSEN"), ("D", "CORETTA MALLET-FONTENOT")},
    }
    sboe = {r["district"] for r in records if r["office"] == "state_board_of_education"}
    assert sboe == {"4"}

    legislature = {(r["office"], r["district"]) for r in records if r["office"] in ("upper", "lower")}
    assert legislature == {("lower", "1"), ("upper", "4")}

    # No court, no district attorney and no county office became a
    # statewide or legislative record: judicial is its own opt-in.
    known = set(STATEWIDE_OFFICE_LABELS) | {"upper", "lower", "S", "H"}
    assert {r["office"] for r in records} <= known
    names = {r["last_name"] for r in records}
    assert not names & {"JIMMY BLACKLOCK", "MAGGIE ELLIS", "THOMAS SMITH", "KYLE HAWKINS"}


@pytest.mark.asyncio
async def test_tx_without_the_opt_in_reads_only_federal_rows():
    async with _tx_client() as client:
        records = await tx.fetch_confirmed_candidates(client, 2026, "TX", {})
    assert records and {r["office"] for r in records} == {"S"}
