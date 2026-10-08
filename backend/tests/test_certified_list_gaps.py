"""Certified November lists for three states whose pages were confirmed
from primary results, and missed what primary results cannot see:
withdrawals, petition independents and nominees no primary contested.

Each test reads the state's real entry in state_candidate_sources.json
against a trimmed copy of what the state served on 2026-10-08 (contact
fields blanked: only the ballot columns are evidence), so a change to the
configuration is tested along with the code.

  Montana -- the cycle's GENERAL list, chosen from the list page's own
             election picker; the PRIMARY list it used to read leaves
             petition independents at PENDING PETITION for good.
  Idaho    -- the candidate portal's FINAL CANDIDATES LIST (JSON API),
             keyed by an election id read from the portal's own list.
  Arkansas -- the Secretary of State's candidate search (JSON API), pruned
             after the primary to the November field.
"""

import json
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest

from app.models import Candidate, Race
from app.pipeline.fetch import state_candidates as sc
from app.pipeline.fetch import state_candidates_certified_table as ct
from app.pipeline.rate_limiter import RateLimiter

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "state_candidates"
SOURCES = json.loads(
    (pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "state_candidate_sources.json").read_text()
)["states"]

# Before any ballot must be final, so "not yet" answers [] rather than None.
BEFORE_THE_BALLOT_WINDOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(ct, "_rate_limiter", RateLimiter(rps=1000))


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _parties(records, office, district=None):
    """The parties on the ballot for one race, one entry per candidate."""
    return sorted(r["party"] for r in records if r["office"] == office and r["district"] == district)


# --- Montana -------------------------------------------------------------

MT_BUTTON = "ctl00$ContentPlaceHolder1$grdCandidates$ctl00$ctl02$ctl00$ExportToCsvButton"
MT_PICKER = "ctl00$ContentPlaceHolder1$ddlElection"


def _mt_page(selected: str) -> str:
    """The candidate list page, trimmed to its form: the election picker
    (its real option labels), view state and the export button."""
    options = [("450002928", "FEDERAL PRIMARY 2026 (06/02/2026) (Primary)"),
               ("450002987", "FEDERAL GENERAL 2026 (11/03/2026) (General)")]
    opts = "".join(
        f'<option {"selected=\"selected\" " if value == selected else ""}value="{value}">{label}</option>'
        for value, label in options
    )
    name = "GENERAL" if selected == "450002987" else "PRIMARY"
    return (f'<html><h2>{name} 2026 Candidate List</h2><form method="post" action="./CandidateList.aspx?e={selected}">'
            f'<input type="hidden" name="__VIEWSTATE" value="vs-{selected}">'
            f'<input type="hidden" name="__EVENTTARGET" value="">'
            f'<select name="{MT_PICKER}">{opts}</select>'
            f'<input type="submit" name="{MT_BUTTON}" value=" ">'
            "</form></html>")


def _mt_handler(posts, switch=True):
    csv_bytes = (FIXTURES / "mt_general_candidate_list.csv").read_bytes()

    def handler(request):
        if request.url.host == "sosmt.gov":
            return httpx.Response(200, text='<a href="https://candidatefiling.mt.gov/candidatefiling/'
                                             'CandidateList.aspx?e=450002928" target="_blank">2026 Candidate Filings</a>')
        if request.method == "GET":
            return httpx.Response(200, text=_mt_page("450002928"))
        form = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        posts.append(form)
        if MT_BUTTON in form:
            assert form["__VIEWSTATE"] == "vs-450002987", "the export must be pressed on the GENERAL page"
            return httpx.Response(200, content=csv_bytes, headers={"content-type": "text/csv"})
        assert form["__EVENTTARGET"] == MT_PICKER
        return httpx.Response(200, text=_mt_page(form[MT_PICKER] if switch else "450002928"))
    return handler


@pytest.mark.asyncio
async def test_montana_reads_the_general_list_with_its_petition_independents():
    posts = []
    async with _client(_mt_handler(posts)) as client:
        records = await ct.fetch_confirmed_candidates(client, 2026, "MT", SOURCES["MT"]["general_list"])

    # The Senate and MT-2 independents are on the ballot by petition; the
    # primary list still said PENDING PETITION for both. The declared
    # Senate write-in (party preference NON, the list's fifth Senate row)
    # is not printed on the ballot, so it is not read.
    assert _parties(records, "S") == ["D", "I", "L", "R"]
    assert _parties(records, "H", 1) == ["D", "L", "R"]
    assert _parties(records, "H", 2) == ["D", "I", "L", "R"]
    # Chosen first, as the picker posts the page back; then exported.
    assert [MT_BUTTON in p for p in posts] == [False, True]
    assert posts[0][MT_PICKER] == "450002987"


@pytest.mark.asyncio
async def test_montana_refuses_an_export_the_picker_did_not_switch():
    # Pressing the button on the primary page exports the PRIMARY list:
    # reading it as the ballot is the bug this fixes.
    posts = []
    async with _client(_mt_handler(posts, switch=False)) as client:
        assert await ct.fetch_confirmed_candidates(client, 2026, "MT", SOURCES["MT"]["general_list"]) is None
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_montana_before_the_general_exists_is_not_yet(freeze_utcnow):
    freeze_utcnow(BEFORE_THE_BALLOT_WINDOW)
    page = _mt_page("450002928").replace(
        '<option value="450002987">FEDERAL GENERAL 2026 (11/03/2026) (General)</option>', "")

    def handler(request):
        if request.url.host == "sosmt.gov":
            return httpx.Response(200, text='<a href="https://candidatefiling.mt.gov/candidatefiling/'
                                             'CandidateList.aspx?e=450002928">x</a>')
        return httpx.Response(200, text=page)

    async with _client(handler) as client:
        assert await ct.fetch_confirmed_candidates(client, 2026, "MT", SOURCES["MT"]["general_list"]) == []


# --- Idaho ---------------------------------------------------------------

def _id_handler(final=True, elections=None, calls=None):
    listing = json.loads((FIXTURES / "id_final_candidates_list.json").read_text())
    listing["data"]["isFinalList"] = final
    elections = elections if elections is not None else (FIXTURES / "id_portal_elections.json").read_text()

    def handler(request):
        if calls is not None:
            calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("GetAllElections"):
            return httpx.Response(200, text=elections)
        body = json.loads(request.content)
        assert body["electionGuid"] == "1e38d17c-a176-47b8-91d1-368ba38ccdec"  # the 2026 GENERAL
        return httpx.Response(200, json=listing)
    return handler


@pytest.mark.asyncio
async def test_idaho_reads_the_final_list_without_the_withdrawn_nominee():
    calls = []
    async with _client(_id_handler(calls=calls)) as client:
        records = await ct.fetch_confirmed_candidates(client, 2026, "ID", SOURCES["ID"]["general_list"])

    # The Democratic primary winner withdrew (Withdrawn on the list), and
    # two independents are on the ballot by petition, where no primary
    # showed them.
    assert _parties(records, "S") == ["I", "I", "L", "R"]
    assert _parties(records, "H", 1) == ["C", "D", "I", "R"]
    # MT-2's Constitution candidate withdrew too.
    assert _parties(records, "H", 2) == ["D", "I", "I", "L", "R"]
    # State offices come from the list too, independents and the
    # Constitution Party included; of the two independents filed for
    # Governor, the declared write-in is not printed on the ballot.
    assert _parties(records, "governor") == ["C", "D", "I", "L", "R"]
    # Seat A and Seat B of one district are two contests (office_parts).
    seats = {(r["office"], r["district"], r.get("seat")) for r in records if r["office"] in ("upper", "lower")}
    assert seats == {("upper", "1", None), ("lower", "1", "A"), ("lower", "1", "B")}
    # County offices and judgeships on the same list are not read.
    assert {r["office"] for r in records} <= {"S", "H", "governor", "lt_governor", "secretary_of_state",
                                              "controller", "treasurer", "attorney_general",
                                              "school_superintendent", "upper", "lower"}
    assert [c[1] for c in calls] == ["/api/PublicLookup/GetAllElections", "/api/FiledCandidates/SearchCandidates"]


@pytest.mark.asyncio
async def test_idaho_waits_for_the_portal_to_call_its_list_final(freeze_utcnow):
    freeze_utcnow(BEFORE_THE_BALLOT_WINDOW)
    async with _client(_id_handler(final=False)) as client:
        assert await ct.fetch_confirmed_candidates(client, 2026, "ID", SOURCES["ID"]["general_list"]) == []


@pytest.mark.asyncio
async def test_idaho_without_a_general_election_is_not_yet(freeze_utcnow):
    freeze_utcnow(BEFORE_THE_BALLOT_WINDOW)
    primary_only = json.dumps({"data": [{"value": "x", "name": "May 19, 2026 - 2026 PRIMARY "}]})
    async with _client(_id_handler(elections=primary_only)) as client:
        assert await ct.fetch_confirmed_candidates(client, 2026, "ID", SOURCES["ID"]["general_list"]) == []


@pytest.mark.asyncio
async def test_idaho_outage_is_not_an_empty_ballot():
    async with _client(lambda r: httpx.Response(503)) as client:
        assert await ct.fetch_confirmed_candidates(client, 2026, "ID", SOURCES["ID"]["general_list"]) is None


@pytest.mark.asyncio
async def test_idaho_a_truncated_page_is_refused():
    listing = json.loads((FIXTURES / "id_final_candidates_list.json").read_text())
    listing["data"]["candidatesFound"] += 1

    def handler(request):
        if request.url.path.endswith("GetAllElections"):
            return httpx.Response(200, text=(FIXTURES / "id_portal_elections.json").read_text())
        return httpx.Response(200, json=listing)

    async with _client(handler) as client:
        assert await ct.fetch_confirmed_candidates(client, 2026, "ID", SOURCES["ID"]["general_list"]) is None


# --- Arkansas ------------------------------------------------------------

AR_HOME = "<h2>2026 Preferential Primary and Nonpartisan Judicial General Election</h2>"


def _ar_handler(body: bytes, home: str = AR_HOME, seen=None):
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text=home)
        if seen is not None:
            seen.append(request.headers.get("X-Requested-With"))
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})
    return handler


@pytest.mark.asyncio
async def test_arkansas_reads_every_statewide_office_including_the_unopposed():
    seen = []
    body = (FIXTURES / "ar_candidate_search.json").read_bytes()
    async with _client(_ar_handler(body, seen=seen)) as client:
        records = await ct.fetch_confirmed_candidates(client, 2026, "AR", SOURCES["AR"]["general_list"])

    # Every statewide office, where the primary results showed the
    # Governor's race alone and that with only the Democrat: the sitting
    # Governor and the nominees for the other five offices were unopposed
    # in their primaries, and the Libertarians were nominated by convention.
    statewide = {}
    for r in records:
        if r["office"] not in ("S", "H", "upper", "lower"):
            statewide.setdefault(r["office"], []).append(r["party"])
    assert {office: sorted(parties) for office, parties in statewide.items()} == {
        "governor": ["D", "L", "R"],
        "lt_governor": ["L", "R"],
        "attorney_general": ["R"],
        "secretary_of_state": ["D", "L", "R"],
        "treasurer": ["R"],
        "auditor": ["R"],
        "state_lands_commissioner": ["L", "R"],
    }
    # Every House seat, with the nominees AR-1's and AR-3's uncontested
    # primaries never listed, and the Libertarians.
    assert _parties(records, "H", 1) == ["D", "L", "R"]
    assert _parties(records, "H", 3) == ["D", "L", "R"]
    # Names exactly as filed for the ballot, the title of an elective office
    # the candidate holds included (Arkansas lets one be printed).
    listed = {row["CanBallotName"] for row in json.loads(body)["data"]}
    assert {r["display_name"] for r in records if r["office"] in ("S", "H")} <= listed
    assert any(r["display_name"].startswith("Senator ") for r in records if r["office"] == "S")
    # An independent for the state House, on the ballot by petition.
    assert [r["district"] for r in records if r["office"] == "lower" and r["party"] == "I"] == ["45"]
    assert seen == ["XMLHttpRequest"]


@pytest.mark.asyncio
async def test_arkansas_empty_answer_is_a_failed_read_not_an_empty_ballot():
    # The table API answers 200 with an empty body when it refuses a
    # request (too soon after another, or missing its own parameters).
    async with _client(_ar_handler(b"")) as client:
        assert await ct.fetch_confirmed_candidates(client, 2026, "AR", SOURCES["AR"]["general_list"]) is None


@pytest.mark.asyncio
async def test_arkansas_list_of_another_cycle_is_not_read(freeze_utcnow):
    freeze_utcnow(BEFORE_THE_BALLOT_WINDOW)
    body = (FIXTURES / "ar_candidate_search.json").read_bytes()
    home = AR_HOME.replace("2026", "2028")
    async with _client(_ar_handler(body, home=home)) as client:
        assert await ct.fetch_confirmed_candidates(client, 2026, "AR", SOURCES["AR"]["general_list"]) == []


# --- a general list's own fallback ---------------------------------------

def _race(db, race_id, state, office="S", district=None):
    db.add(Race(id=race_id, cycle_year=2026, office=office, state=state, district=district, is_special=False))


def _rec(office, district, party, last, display):
    return {"office": office, "district": district, "party": party, "last_name": last, "display_name": display}


@pytest.mark.asyncio
async def test_a_general_list_that_cannot_be_read_falls_back_to_its_spare(db_session, monkeypatch):
    # Arkansas's candidate search down: Google Civic, its named fallback,
    # still decides the races it answers for, and a primary-results name
    # it does not list (a nominee since replaced) is not confirmed.
    async def no_calendar(client, cycle):
        return {}, False
    monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
    monkeypatch.setattr(sc, "configured_states", lambda: {"AR"})
    monkeypatch.setitem(sc.STRATEGIES, "tally_enr", AsyncMock(return_value=[_rec("S", None, "D", "Colfax", "Gordon Colfax")]))
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=None))
    civic = AsyncMock(return_value=[_rec("S", None, "D", "Penrose", "Reid D. Penrose")])
    monkeypatch.setitem(sc.STRATEGIES, "google_civic", civic)
    _race(db_session, "2026-SEN-AR", "AR")
    db_session.add(Candidate(id="S6AR1", race_id="2026-SEN-AR", name="COLFAX, GORDON", party="DEM"))
    db_session.add(Candidate(id="S6AR2", race_id="2026-SEN-AR", name="PENROSE, REID", party="DEM"))
    db_session.commit()

    await sc.sync_confirmed_candidates(db_session, None, 2026)

    assert civic.await_count == 1
    assert {c.id: c.confirmed_general for c in db_session.query(Candidate)} == {"S6AR1": False, "S6AR2": True}
