"""North Carolina's state offices and judgeships from its candidate list
for the general election (the general_list entry), rather than from its
primary results export.

Measured 2026-10-09 against the State Board of Elections' own file
(dl.ncsbe.gov/Elections/2026/Candidate Filing/Candidate_Listing_2026.csv,
general rows): the page, reading the results export, showed 59
legislative nominees in 55 of 170 districts -- three of them since
replaced by their party -- one nominee for two of the four appellate
seats and nothing for the Supreme Court seat. North Carolina holds no
primary for an unopposed nomination, so the results never list one.

The CSV below has the real file's header and row shape (one row per
county per candidate, both elections in one file); the names are
placeholders and the columns the adapter never reads are blanked.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.api.elections import _judicial_marker, _statewide_marker
from app.models import JudicialNominee, Race, StateLegNominee
from app.pipeline.fetch import state_candidates as sc
from app.pipeline.fetch.state_candidates_certified_table import _records, _rows

_SOURCES = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "data" / "state_candidate_sources.json").read_text()
)["states"]
_LIST = _SOURCES["NC"]["general_list"]

_HEADER = (
    '"election_dt","county_name","contest_name","name_on_ballot","first_name","middle_name",'
    '"last_name","name_suffix_lbl","nick_name","street_address","city","state","zip_code","phone",'
    '"office_phone","business_phone","email","candidacy_dt","party_contest","party_candidate",'
    '"is_unexpired","has_primary","is_partisan","vote_for","term"'
)


def _row(date, county, contest, name, party_contest, party):
    blanks = ",".join('""' for _ in range(14))
    return (f'"{date}","{county}","{contest}","{name}",{blanks},'
            f'"{party_contest}","{party}","FALSE","FALSE","TRUE","1",""')


NC_CSV = "\n".join([
    _HEADER,
    # A primary row: never a November record.
    _row("03/03/2026", "WAKE", "NC HOUSE OF REPRESENTATIVES DISTRICT 117", "Primary Winner", "REP", "REP"),
    _row("11/03/2026", "ALAMANCE", "US SENATE", "Senate Nominee", "", "DEM"),
    _row("11/03/2026", "WAKE", "US SENATE", "Senate Nominee", "", "DEM"),
    _row("11/03/2026", "WAKE", "US HOUSE OF REPRESENTATIVES DISTRICT 02", "House Nominee", "", "LIB"),
    # The party's replacement for the primary winner above.
    _row("11/03/2026", "HENDERSON", "NC HOUSE OF REPRESENTATIVES DISTRICT 117", "Replacement Nominee", "", "REP"),
    _row("11/03/2026", "HENDERSON", "NC HOUSE OF REPRESENTATIVES DISTRICT 117", "Unopposed Nominee", "", "DEM"),
    _row("11/03/2026", "WAKE", "NC HOUSE OF REPRESENTATIVES DISTRICT 051", "Unaffiliated Petitioner", "", "UNA"),
    _row("11/03/2026", "WAKE", "NC STATE SENATE DISTRICT 08", "Richard (Rick) Placeholder", "", "DEM"),
    _row("11/03/2026", "WAKE", "NC SUPREME COURT ASSOCIATE JUSTICE SEAT 01", "Justice Nominee", "", "DEM"),
    _row("11/03/2026", "ALAMANCE", "NC SUPREME COURT ASSOCIATE JUSTICE SEAT 01", "Justice Nominee", "", "DEM"),
    _row("11/03/2026", "WAKE", "NC COURT OF APPEALS JUDGE SEAT 02", "Appeals Nominee", "", "REP"),
    _row("11/03/2026", "WAKE", "NC DISTRICT COURT JUDGE DISTRICT 10A SEAT 01", "District Judge", "", "REP"),
    # Not a contest any gate reads.
    _row("11/03/2026", "WAKE", "WAKE COUNTY CLERK OF SUPERIOR COURT", "A Clerk", "", "DEM"),
]) + "\n"


def _parsed():
    rows = _rows(NC_CSV.encode(), "list.csv", _LIST["format"])
    return _records("NC", rows, _LIST["format"], True, None, judicial=True)


def test_the_list_waits_for_a_november_row_of_this_year():
    import re
    rx = _LIST["discovery"]["year_regex"].replace("{year}", "2026")
    assert re.search(rx, NC_CSV)
    assert not re.search(rx, "\n".join(line for line in NC_CSV.splitlines() if "11/03/2026" not in line))


def test_every_november_row_and_no_primary_row():
    got = {(r["office"], r["district"], r.get("seat"), r["party"], r["last_name"]) for r in _parsed()}
    assert got == {
        ("S", None, None, "D", "Nominee"),
        ("H", 2, None, "L", "Nominee"),
        ("lower", "117", None, "R", "Replacement Nominee"),
        ("lower", "117", None, "D", "Unopposed Nominee"),
        ("lower", "51", None, "I", "Unaffiliated Petitioner"),
        # The nickname the ballot prints is kept.
        ("upper", "8", None, "D", "Richard (Rick) Placeholder"),
        ("supreme", None, "1", "D", "Justice Nominee"),
        ("appeals", None, "2", "R", "Appeals Nominee"),
        ("district", "10A", "1", "R", "District Judge"),
    }


def test_no_judgeship_without_the_opt_in():
    rows = _rows(NC_CSV.encode(), "list.csv", _LIST["format"])
    records = _records("NC", rows, _LIST["format"], True, None)
    assert not any(r["office"] in sc.JUDICIAL_COURT_LABELS for r in records)


@pytest.fixture()
def north_carolina(db_session, monkeypatch):
    async def no_calendar(client, cycle):
        return {}, False
    monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
    monkeypatch.setattr(sc, "configured_states", lambda: {"NC"})
    db_session.add(Race(id="2026-HOUSE-NC-2", cycle_year=2026, office="H", state="NC", district=2,
                        is_special=False))
    db_session.commit()
    return db_session


_PRIMARY = [
    {"office": "lower", "district": "117", "party": "R", "last_name": "Primary Winner"},
    {"office": "appeals", "district": None, "seat": "1", "party": "R", "last_name": "Primary Judge"},
]


@pytest.mark.asyncio
async def test_the_list_supplies_legislature_and_judgeships(north_carolina, monkeypatch):
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=_parsed()))
    monkeypatch.setitem(sc.STRATEGIES, "tabular", AsyncMock(return_value=_PRIMARY))

    await sc.sync_confirmed_candidates(north_carolina, None, 2026)

    leg = {(r.chamber, r.district, r.display_name) for r in north_carolina.query(StateLegNominee)}
    assert ("lower", "117", "Primary Winner") not in leg
    assert ("lower", "117", "Unopposed Nominee") in leg
    judges = {(r.court, r.seat, r.display_name) for r in north_carolina.query(JudicialNominee)}
    assert judges == {
        ("supreme", "1", "Justice Nominee"), ("appeals", "2", "Appeals Nominee"),
        ("district", "1", "District Judge"),
    }
    assert _statewide_marker(north_carolina, "NC", 2026)["ballotList"] is True
    assert _judicial_marker(north_carolina, "NC", 2026)["ballotList"] is True
    assert _judicial_marker(north_carolina, "NC", 2026)["sourceName"] == _LIST["source_name"]


@pytest.mark.asyncio
async def test_once_the_list_named_judgeships_a_quiet_night_keeps_them(north_carolina, monkeypatch):
    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=_parsed()))
    monkeypatch.setitem(sc.STRATEGIES, "tabular", AsyncMock(return_value=_PRIMARY))
    await sc.sync_confirmed_candidates(north_carolina, None, 2026)

    monkeypatch.setitem(sc.STRATEGIES, "certified_table", AsyncMock(return_value=None))
    await sc.sync_confirmed_candidates(north_carolina, None, 2026)

    judges = {r.display_name for r in north_carolina.query(JudicialNominee)}
    assert "Primary Judge" not in judges and "Justice Nominee" in judges
