"""Ohio's official primary canvass (state_candidates_oh, strategy
oh_canvass_xlsx).

Every cell below is real, copied from the Secretary of State's May 5,
2026 "Summary Level Official Results" workbooks (Democratic, Republican,
Libertarian; publicfiles.ohiosos.gov, fetched 2026-09-28) and trimmed to
a few contests and one county row. The workbooks are rebuilt here with
inline strings, which is one of the two cell encodings the reader
accepts; the real files use a shared-string table.

Checked against the real results: Jon Husted and Sherrod Brown are the
Senate special's nominees; Eric Conroy won the Republican primary in the
1st (25,603 to Holly Adams's 7,020), Greg Landsman the Democratic one;
Vivek Ramaswamy and Amy Acton head the tickets for Governor.
"""

import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest

from app.pipeline.fetch import state_candidates_oh
from app.pipeline.fetch.state_candidates_oh import contest_totals, fetch_confirmed_candidates, sheet_rows

SOURCE = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "data" / "state_candidate_sources.json").read_text()
)["states"]["OH"]

@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    async def go():
        return None
    monkeypatch.setattr(state_candidates_oh._rate_limiter, "acquire", go)


TITLE = "May 5, 2026 Primary/Special Election Official Canvass\n*Write-in candidates will be displayed with a (WI) designation."
LEAD = ["County Name", "Region Name", "Media Market", "Registered Voters", "Ballots Counted", "Official Voter Turnout"]
TOTAL = ["Total", "", "", "7896681", "1791152", "0.22682339580388267"]
ADAMS = ["Adams", "Southwest", "Cincinnati", "16942", "4070", "0.24023137764136465"]


def _sheet(contests, names, total, county):
    return [[TITLE, "", "", "", "", "", *contests], [*LEAD, *names], [*TOTAL, *total], [*ADAMS, *county]]


REPUBLICAN = {
    "U.S. Congress": _sheet(
        ["U.S. Senator\nUnexpired Term Ending 01/03/2029", "Representative to Congress - District 01\n", "", "",
         "Representative to Congress - District 04\n"],
        ["Jon Husted (R)", "Holly Adams (R)", "Eric Conroy (R)", "Rosemary Oglesby - Henry (R)", "Jim Jordan (R)"],
        ["736413", "7020", "25603", "2994", "75210"],
        ["2523", "0", "0", "0", "0"],
    ),
    "Statewide Offices": _sheet(
        ["Governor and Lieutenant Governor\n", "", "Attorney General\n"],
        ["Casey  Putsch and Kimberly  C. Georgeton (R)", "Vivek Ramaswamy and Robert A. McColley (R)",
         "Keith Faber (R)"],
        ["144184", "676562", "698856"],
        ["120", "2100", "2300"],
    ),
    "General Assembly": _sheet(
        ["State Senator - District 07\n", "", "State Senator - District 09\n"],
        ["Zac Haines (R)", "Kim Lukens (R)", "Linda Matthews (WI)* (R)"],
        ["22360", "6549", "214"],
        ["0", "0", "0"],
    ),
    # Never opened: judges and party committees are not configured.
    "Justice of the Supreme Court": _sheet(
        ["Justice of the Supreme Court\nTerm Commencing 01/01/2027"], ["Daniel R. Hawkins (R)"], ["674078"], ["1"],
    ),
}
DEMOCRATIC = {
    "U.S. Congress": _sheet(
        ["U.S. Senator\nUnexpired Term Ending 01/03/2029", "", "Representative to Congress - District 01\n", ""],
        ["Sherrod Brown (D)", "Ron Kincaid (D)", "Greg Landsman (D)", "Damon Lynch IV (D)"],
        ["712106", "84405", "36984", "17483"],
        ["1200", "90", "0", "0"],
    ),
    "Statewide Offices": _sheet(
        ["Governor and Lieutenant Governor\n", "Attorney General\n", ""],
        ["Amy Acton and David Pepper (D)", "Elliot Forhan (D)", "John  J. Kulewicz (D)"],
        ["767360", "260407", "445336"],
        ["900", "300", "500"],
    ),
    "General Assembly": _sheet(
        ["State Senator - District 07\n"], ["Cara Jacob (D)"], ["19095"], ["0"],
    ),
}
LIBERTARIAN = {
    "U.S. Congress": _sheet(
        ["U.S. Senator\n", "Representative to Congress - District 01\n", "",
         "Representative to Congress - District 05\n"],
        ["William B. Redpath (L)", "John D. Hancock Jr (L)", "Jason Stoops (WI)* (L)", "Michael J. Veloff (WI)* (L)"],
        ["9102", "535", "51", "25"],
        ["10", "0", "0", "0"],
    ),
}


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def workbook(sheets: dict[str, list[list[str]]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        entries = "".join(
            f'<sheet name="{escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, name in enumerate(sheets)
        )
        z.writestr("xl/workbook.xml", (
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{entries}</sheets></workbook>"))
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            f'worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for i, rows in enumerate(sheets.values()):
            body = "".join(
                f'<row r="{r + 1}">' + "".join(
                    f'<c r="{_col(c)}{r + 1}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
                    for c, v in enumerate(row) if v != "") + "</row>"
                for r, row in enumerate(rows))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       f"<sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


def _index(year=2026):
    def f(name, path):
        return {"displayName": name, "blobPath": path, "order": 10}
    base = f"past-elections/{year}/Primary+Special Election - May 5, {year}/"
    return {"historicalComparisons": [], "listOfElectedOfficials": [], "pastElectionResults": [{
        "year": year,
        "elections": [{"type": f"Primary/Special Election - May 5, {year}", "fileGroups": [
            {"description": None, "files": [f("Voter Turnout by County", base + "turnoutbycountybyparty.xlsx")]},
            {"description": "Results by County by Party", "files": [
                f(f"Summary Level Official Results for {year} Primary Election - Democratic",
                  base + f"group1/summary-level-official-results-{year}-primary---democratic.xlsx"),
                f(f"Summary Level Official Results for {year} Primary Election - Libertarian",
                  base + f"group1/summary-level-official-results-{year}-primary---libertarian.xlsx"),
                f(f"Summary Level Official Results for {year} Primary Election - Republican",
                  base + f"group1/summary-level-official-results-{year}-primary---republican.xlsx"),
            ]},
            {"description": "County officials", "files": [
                f(f"Summary Level Official Results for {year} Primary Election - County Officials Only - Republican",
                  base + "group3/county-only.xlsx"),
            ]},
        ]}],
    }]}


def _handler(books, seen, index=None):
    def handle(request):
        path = request.url.path
        seen.append(str(request.url))
        if path.endswith("files-index.json"):
            return httpx.Response(200, json=index or _index())
        for party, book in books.items():
            if path.endswith(f"---{party}.xlsx"):
                return httpx.Response(200, content=workbook(book))
        # Not a 404: the shared fetch retries those with backoff.
        return httpx.Response(200, content=b"<html>not a workbook</html>")
    return handle


async def _fetch(books, source=SOURCE, index=None):
    seen = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(books, seen, index))) as client:
        return await fetch_confirmed_candidates(client, 2026, "OH", source), seen


BOOKS = {"democratic": DEMOCRATIC, "republican": REPUBLICAN, "libertarian": LIBERTARIAN}


@pytest.mark.asyncio
async def test_federal_nominees_by_plurality_from_every_party_workbook():
    records, seen = await _fetch(BOOKS)
    federal = {(r["office"], r["district"], r["party"], r["display_name"]) for r in records if r["office"] in ("S", "H")}
    assert federal == {
        ("S", None, "R", "Jon Husted"), ("S", None, "D", "Sherrod Brown"), ("S", None, "L", "William B. Redpath"),
        ("H", 1, "R", "Eric Conroy"), ("H", 1, "D", "Greg Landsman"), ("H", 1, "L", "John D. Hancock Jr"),
        ("H", 4, "R", "Jim Jordan"),
        # District 5's Libertarian "winner" is a write-in with nobody
        # printed: 25 votes, below any petition requirement (R.C. 3513.23).
    }
    # The index's own blobPath, spaces encoded and its "+" kept; the
    # county-officials-only file is never fetched.
    assert ("https://publicfiles.ohiosos.gov/election-results/past-elections/2026/"
            "Primary+Special%20Election%20-%20May%205,%202026/group1/"
            "summary-level-official-results-2026-primary---republican.xlsx") in seen
    assert not any("county-only" in url for url in seen)


@pytest.mark.asyncio
async def test_state_offices_and_seats_ride_the_opt_in():
    records, _ = await _fetch(BOOKS)
    state = {(r["office"], r["district"], r["party"], r["last_name"]) for r in records if r["office"] not in ("S", "H")}
    assert state == {
        ("governor", None, "R", "Vivek Ramaswamy and Robert A. McColley"),
        ("governor", None, "D", "Amy Acton and David Pepper"),
        ("attorney_general", None, "R", "Keith Faber"),
        ("attorney_general", None, "D", "John J. Kulewicz"),
        ("upper", "7", "R", "Zac Haines"),
        ("upper", "7", "D", "Cara Jacob"),
    }
    records, _ = await _fetch(BOOKS, {**SOURCE, "statewide_offices": False})
    assert {r["office"] for r in records} == {"S", "H"}


@pytest.mark.asyncio
async def test_every_party_workbook_is_required():
    # The Libertarian file listed in the index does not read as a workbook.
    records, _ = await _fetch({"democratic": DEMOCRATIC, "republican": REPUBLICAN})
    assert records is None


@pytest.mark.asyncio
async def test_a_workbook_not_titled_this_years_official_canvass_is_refused():
    unofficial = {name: [[rows[0][0].replace("Official Canvass", "Unofficial Results"), *rows[0][1:]], *rows[1:]]
                  for name, rows in REPUBLICAN.items()}
    records, _ = await _fetch({**BOOKS, "republican": unofficial})
    assert records is None


@pytest.mark.asyncio
async def test_no_primary_in_the_index_yet_is_an_empty_answer():
    records, _ = await _fetch(BOOKS, index={"pastElectionResults": [{"year": 2025, "elections": []}]})
    assert records == []


def test_a_candidate_whose_printed_party_is_not_the_workbooks_is_skipped():
    rows = _sheet(["Representative to Congress - District 01\n"], ["Greg Landsman (D)"], ["36984"], ["0"])
    assert contest_totals(rows, "R") == []
    assert contest_totals(rows, "D") == [("Representative to Congress - District 01", "Greg Landsman", 36984, False)]


def test_only_configured_sheets_are_read():
    assert set(sheet_rows(workbook(REPUBLICAN), SOURCE["sheets"])) == {
        "U.S. Congress", "Statewide Offices", "General Assembly",
    }


def test_the_entry_is_the_states_own_canvass_and_google_only_supplements_it():
    assert SOURCE["strategy"] == "oh_canvass_xlsx"
    assert SOURCE["general_list"]["strategy"] == "google_civic"
