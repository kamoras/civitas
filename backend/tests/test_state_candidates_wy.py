"""Tests for Wyoming's confirmed-general-candidate strategy
(state_candidates_wy.py).

fixtures_wy_primary_results.zip is a HAND-BUILT fixture, not a raw slice
of the real download -- the real zip is ~1.1MB (three full Excel
workbooks, one of them a 4.2MB-uncompressed county-by-precinct export),
far too large to commit as a trimmed real-data fixture the way this
system's HTML fixtures (MT/NE/SD) are. It faithfully reproduces the REAL,
live-verified 2026 data and — critically — the real workbook's own
structural quirk this module's docstring calls out: "Statewide
Candidates" is NOT the workbook's first physical worksheet file (a decoy
sheet sits at sheet1.xml/sheet2.xml; the real data is sheet3.xml), so a
lookup that assumed a fixed sheet number would read the wrong data. Real
candidates/parties/vote totals (Senate: Edwards/Hageman/Holtz/Mead/
Skovgard R, Benavidez/Byrd D; House: Friess/Gray/Rasner R plus the real
"* Withdrawn\\nCandidate" placeholder, Del Real/Kinney D) all come from
the live workbook fetched 2026-09-08 — only trimmed to 2 of Wyoming's 23
real counties (Albany, Big Horn; irrelevant to correctness since
_federal_totals reads the Total row directly, never sums counties
itself) and a handful of the real House Republican field (trimmed from
9 real candidates to 3, real vote counts kept for each one kept).
"""

from pathlib import Path
from types import SimpleNamespace

from app.pipeline.fetch import state_candidates_wy as wy

FIXTURES = Path(__file__).parent
ZIP_BYTES = (FIXTURES / "fixtures_wy_primary_results.zip").read_bytes()


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
