"""Tests for Wyoming's confirmed-general-candidate strategy
(state_candidates_wy.py).

ZIP_BYTES is built HERE, in Python, at collection time — not committed as
a binary .zip fixture. The real download is ~1.1MB (three full Excel
workbooks, one of them a 4.2MB-uncompressed county-by-precinct export),
far too large to commit as a trimmed real-data fixture the way this
system's HTML fixtures (MT/NE/SD) are, and a binary blob would be opaque
to review/diff (matching how test_state_candidates_tn.py sidesteps the
same problem for its own xlsx-reading module, by not shipping real xlsx
bytes at all). This module's own zip/XML-writing code (the same stdlib
zipfile it reads with) builds a small but structurally faithful workbook:
"Statewide Candidates" is deliberately NOT the first physical worksheet
file (a decoy sheet sits at sheet1.xml/sheet2.xml; the real data is
sheet3.xml), reproducing the real workbook's own quirk that a
fixed-sheet-number lookup would silently read the wrong data.

GROUPS below carries the REAL, live-verified 2026 primary data (Senate:
Edwards/Hageman/Holtz/Mead/Skovgard R, Benavidez/Byrd D; House:
Friess/Gray/Rasner R plus the real "* Withdrawn\\nCandidate" placeholder,
Del Real/Kinney D — fetched live 2026-09-08), trimmed to 2 of Wyoming's
23 real counties (Albany, Big Horn — irrelevant to correctness since
_federal_totals reads the Total row directly, never sums counties
itself) and a handful of the real House Republican field (trimmed from
9 real candidates to 3, real vote counts kept for each one kept).
"""

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

from app.pipeline.fetch import state_candidates_wy as wy

FIXTURES = Path(__file__).parent

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

# "Statewide Candidates" is deliberately r:id="rId3" (-> sheet3.xml), not
# the first sheet -- see module docstring.
_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
<sheet name="Statewide Total Ballots Cast" sheetId="1" r:id="rId1"/>
<sheet name="Statewide Candidates" sheetId="2" r:id="rId3"/>
<sheet name="Statewide Senate Odd" sheetId="3" r:id="rId2"/>
</sheets>
</workbook>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""

_DECOY_SHEET = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>decoy sheet -- not read by this module</t></is></c></row>
</sheetData>
</worksheet>"""

# (race, party, [(name, albany_votes, bighorn_votes), ...]) -- real,
# live-verified 2026 statewide Total-row values are looked up by name
# below rather than hand-typed alongside these, so the county columns
# and the real totals can never silently drift apart.
_GROUPS = [
    ("United States Senator", "Republican", [
        ("Jill M\nEdwards", "301", "109"), ("Harriet\nHageman", "2807", "2461"),
        ("John\nHoltz", "152", "115"), ("Sam\nMead", "2561", "744"),
        ("Jimmy\nSkovgard", "269", "160"), ("Write-Ins", "27", "8"),
        ("Overvotes", "10", "7"), ("Undervotes", "114", "123"),
    ]),
    ("United States Senator, Continued", "Democratic", [
        ("Billy\nBenavidez", "288", "41"), ("James\nByrd", "1266", "102"),
        ("Write-Ins", "23", "2"), ("Overvotes", "0", "0"), ("Undervotes", "105", "11"),
    ]),
    ("United States Representative", "Republican", [
        ("Steve\nFriess", "525", "154"), ("Chuck\nGray", "2959", "1224"),
        ("Reid\nRasner", "1251", "1175"), ("* Withdrawn\nCandidate", "883", "603"),
        ("Write-Ins", "0", "0"), ("Overvotes", "29", "11"), ("Undervotes", "2", "4"),
    ]),
    ("United States Representative, Continued", "Democratic", [
        ("Elena\nDel Real", "212", "21"), ("Lisa\nKinney", "4758", "3106"),
        ("Write-Ins", "41", "21"), ("Overvotes", "0", "0"), ("Undervotes", "1442", "600"),
    ]),
]
_TOTALS = {
    "Jill M\nEdwards": "3431", "Harriet\nHageman": "83807", "John\nHoltz": "2539",
    "Sam\nMead": "35879", "Jimmy\nSkovgard": "3527",
    "Billy\nBenavidez": "2499", "James\nByrd": "9591",
    "Steve\nFriess": "25059", "Chuck\nGray": "31224", "Reid\nRasner": "10808",
    "* Withdrawn\nCandidate": "2162",
    "Elena\nDel Real": "2660", "Lisa\nKinney": "9344",
}


def _col_letter(col_idx: int) -> str:
    letters = ""
    n = col_idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _build_fixture_zip() -> bytes:
    race_row, party_row, name_row = [""], [""], [""]
    albany_row, bighorn_row, total_row = ["Albany"], ["Big Horn"], ["Total"]
    for race, party, candidates in _GROUPS:
        for i, (name, albany_votes, bighorn_votes) in enumerate(candidates):
            race_row.append(race if i == 0 else "")
            party_row.append(party if i == 0 else "")
            name_row.append(name)
            albany_row.append(albany_votes)
            bighorn_row.append(bighorn_votes)
            total_row.append(_TOTALS.get(name, "0"))

    rows_text = [
        ["", "Statewide Candidates Official Summary\nWyoming Primary Election - August 18, 2026"],
        [""],
        race_row, party_row, name_row, albany_row, bighorn_row, total_row,
    ]

    shared: list[str] = []

    def sidx(text: str) -> int:
        if text not in shared:
            shared.append(text)
        return shared.index(text)

    def cell_xml(col_idx: int, row_num: int, value: str) -> str:
        if value == "":
            return ""
        ref = f"{_col_letter(col_idx)}{row_num}"
        if value.isdigit():
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="s"><v>{sidx(value)}</v></c>'

    row_xml = "".join(
        f'<row r="{i}">{"".join(cell_xml(j, i, v) for j, v in enumerate(row))}</row>'
        for i, row in enumerate(rows_text, start=1)
    )
    sheet3 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{row_xml}</sheetData></worksheet>"
    )
    shared_strings = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared)}" uniqueCount="{len(shared)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>"
    )

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", _WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        z.writestr("xl/sharedStrings.xml", shared_strings)
        z.writestr("xl/worksheets/sheet1.xml", _DECOY_SHEET)
        z.writestr("xl/worksheets/sheet2.xml", _DECOY_SHEET)
        z.writestr("xl/worksheets/sheet3.xml", sheet3)

    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("2026 Primary Results Summaries - OFFICIAL.xlsx", inner.getvalue())
    return outer.getvalue()


ZIP_BYTES = _build_fixture_zip()


def _patched(monkeypatch, content):
    async def fake(client, rl, method, url, **kw):
        return SimpleNamespace(content=content)

    monkeypatch.setattr(wy, "fetch_with_retry", fake)


class TestColIndex:
    def test_reads_single_letter_columns(self):
        assert wy._col_index("A1") == 0
        assert wy._col_index("C5") == 2
        assert wy._col_index("Z10") == 25

    def test_reads_double_letter_columns(self):
        assert wy._col_index("AA1") == 26
        assert wy._col_index("AB1") == 27

    def test_a_malformed_reference_falls_back_to_zero(self):
        assert wy._col_index("") == 0


class TestFindSummarySheetRows:
    def test_finds_the_real_sheet_by_name_not_by_sheet_number(self):
        # The fixture's "Statewide Candidates" sheet lives at
        # worksheets/sheet3.xml, with decoy content at sheet1.xml/
        # sheet2.xml -- this only passes if the lookup actually walks
        # workbook.xml's own <sheet name=...> entries and follows the
        # matching r:id, exactly like the real workbook requires.
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        assert rows is not None
        assert "Wyoming Primary Election" in rows[0][1]

    def test_sparse_blank_cells_dont_shift_later_columns(self):
        # The real bug this guards against: a row's <c> elements are
        # sparse (a genuinely blank cell is often omitted from the XML
        # entirely), so building a row by ITERATION POSITION rather than
        # each cell's own column reference silently shifts every column
        # after a blank one to the left. Race/party rows are almost all
        # blanks by design (the forward-fill scheme depends on it), so
        # this is the single most load-bearing correctness property of
        # the whole parser.
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        name_row = rows[4]
        # "Chuck\nGray" must land in the same column as his real Total-row
        # vote count (31,224) -- a column shift would pair his name with
        # someone else's total instead.
        gray_col = name_row.index("Chuck\nGray")
        total_row = next(r for r in rows if r and r[0] == "Total")
        assert total_row[gray_col] == "31224"

    def test_a_malformed_zip_returns_none(self):
        assert wy._find_summary_sheet_rows(b"not a zip") is None


class TestPageElection:
    def test_reads_the_real_title(self):
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        assert wy._page_election(rows) == (2026, "2026-08-18")

    def test_a_missing_title_returns_none(self):
        assert wy._page_election([["", "nothing relevant here"]]) is None


class TestFederalTotals:
    def test_finds_all_real_federal_candidates(self):
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        totals = wy._federal_totals(rows)
        names = {t[3] for t in totals}
        assert names == {
            "Edwards", "Hageman", "Holtz", "Mead", "Skovgard",  # Senate R
            "Benavidez", "Byrd",  # Senate D
            "Friess", "Gray", "Rasner",  # House R
            "Real", "Kinney",  # House D
        }

    def test_the_withdrawn_candidate_placeholder_is_excluded(self):
        # Real shape: Wyoming's own House R field carries a
        # "* Withdrawn Candidate" column with 2,162 real votes still
        # counted under it -- not a real candidate, must not surface as
        # a surname of "Candidate".
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        names = {t[3] for t in wy._federal_totals(rows)}
        assert "Candidate" not in names

    def test_write_ins_overvotes_undervotes_are_excluded(self):
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        names = {t[3] for t in wy._federal_totals(rows)}
        assert not names & {"Write-Ins", "Overvotes", "Undervotes"}

    def test_office_and_party_are_forward_filled_correctly(self):
        # The real regression this guards against (hit while building
        # the fixture itself): a hand-typed header row with the wrong
        # blank-padding count silently shifted the second "Democratic"
        # marker 4 columns off, misclassifying the real House Democratic
        # candidates as Republican.
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        by_name = {t[3]: (t[0], t[2]) for t in wy._federal_totals(rows)}
        assert by_name["Hageman"] == ("S", "R")
        assert by_name["Byrd"] == ("S", "D")
        assert by_name["Gray"] == ("H", "R")
        assert by_name["Kinney"] == ("H", "D")

    def test_a_row_inserted_above_the_header_block_doesnt_misattribute_votes(self):
        # The header rows are found by CONTENT (the "Write-Ins" column
        # every real group carries), not a fixed row index -- this proves
        # it: an extra row spliced in above the real headers must not
        # shift race_row/party_row/name_row onto the wrong data, which a
        # fixed-index lookup would do silently (real votes attached to
        # the wrong office/party, no error).
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        rows_with_extra = [rows[0], ["", "unexpected extra row"], *rows[1:]]
        by_name = {t[3]: (t[0], t[2]) for t in wy._federal_totals(rows_with_extra)}
        assert by_name["Hageman"] == ("S", "R")
        assert by_name["Kinney"] == ("H", "D")

    def test_no_write_ins_column_anywhere_returns_none(self):
        # A real shape break (the workbook no longer carries the
        # landmark this module anchors on) must read as fetch_failed,
        # never as "zero federal contests this cycle".
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        stripped = [[c for c in row if c != "Write-Ins"] for row in rows]
        assert wy._federal_totals(stripped) is None

    def test_more_than_one_total_row_returns_none_rather_than_guessing(self):
        rows = wy._find_summary_sheet_rows(ZIP_BYTES)
        total_idx = next(i for i, r in enumerate(rows) if r and r[0] == "Total")
        duplicated = [*rows, rows[total_idx]]
        assert wy._federal_totals(duplicated) is None


class TestFetchConfirmedCandidates:
    async def test_real_primary_resolves_to_the_real_certified_winners(self, monkeypatch):
        _patched(monkeypatch, ZIP_BYTES)
        result = await wy.fetch_confirmed_candidates(None, 2026, "WY", {"settle_days": 1})
        assert {"office": "S", "district": None, "party": "R", "last_name": "Hageman"} in result
        assert {"office": "S", "district": None, "party": "D", "last_name": "Byrd"} in result
        assert {"office": "H", "district": None, "party": "R", "last_name": "Gray"} in result
        assert {"office": "H", "district": None, "party": "D", "last_name": "Kinney"} in result
        assert len(result) == 4

    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(wy, "fetch_with_retry", fake)
        assert await wy.fetch_confirmed_candidates(None, 2026, "WY", {}) is None

    async def test_a_malformed_zip_returns_none(self, monkeypatch):
        _patched(monkeypatch, b"not a zip")
        assert await wy.fetch_confirmed_candidates(None, 2026, "WY", {}) is None

    async def test_a_page_for_the_wrong_year_confirms_nothing_yet(self, monkeypatch):
        _patched(monkeypatch, ZIP_BYTES)
        result = await wy.fetch_confirmed_candidates(None, 2028, "WY", {"settle_days": 1})
        assert result == []

    async def test_not_yet_settled_confirms_nothing(self, monkeypatch):
        _patched(monkeypatch, ZIP_BYTES)
        result = await wy.fetch_confirmed_candidates(None, 2026, "WY", {"settle_days": 36500})
        assert result == []

    async def test_a_configured_runoff_threshold_withholds_a_sub_threshold_leader(self, monkeypatch):
        # Real data: House R's real 9-way field (trimmed to 3 in this
        # fixture, real vote counts kept) has Gray's real 31,224 as a
        # minority of the group's total votes -- Wyoming nominates by
        # plurality so this confirms today, but proves runoff_threshold_
        # pct is actually wired through config, not just harmlessly
        # present at null.
        _patched(monkeypatch, ZIP_BYTES)
        result = await wy.fetch_confirmed_candidates(None, 2026, "WY", {"settle_days": 1, "runoff_threshold_pct": 50.0})
        assert {"office": "H", "district": None, "party": "R", "last_name": "Gray"} not in result
        # The Senate races, real clear majorities, are untouched by the same threshold.
        assert {"office": "S", "district": None, "party": "R", "last_name": "Hageman"} in result
