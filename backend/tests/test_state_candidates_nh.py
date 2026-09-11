"""Tests for New Hampshire's confirmed-general-candidate strategy
(state_candidates_nh.py).

fixtures_nh_elections_root.html, fixtures_nh_results_index.html, and
fixtures_nh_democratic_page.html are REAL, trimmed excerpts of the
actual live pages (fetched 2026-09-10/11) — the real three-hop anchor
shape this module's own discovery depends on, including a real decoy
("US Senator Belknap", a per-county breakdown link that also parses as
a Senate office) proving the "summary" text filter actually does its
job rather than only being tested against a page with nothing to
filter.

The xlsx fixture rows below are built with `_workbook()` (not
downloaded), but every value is REAL: two real counties (Belknap,
Carroll) from the actual downloaded 2026 U.S. Senate Democratic and
Republican exports, including the real cross-party write-in columns
(a Democratic candidate's own small vote count inside the Republican
file, and vice versa) and the real data quirk that motivated the
no-suffix fallback — Richard A. McMenamon II's own party suffix is
genuinely dropped ("Richard A. McMenamon " with no ", r") in the real
Republican file specifically, confirmed by comparing it against the
same candidate's correctly-suffixed column in the real Democratic
file's own cross-tabulation.
"""

import io
import zipfile
from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_nh as nh

FIXTURES = Path(__file__).parent
ROOT_HTML = (FIXTURES / "fixtures_nh_elections_root.html").read_text()
INDEX_HTML = (FIXTURES / "fixtures_nh_results_index.html").read_text()
DEM_PAGE_HTML = (FIXTURES / "fixtures_nh_democratic_page.html").read_text()
REP_PAGE_HTML = DEM_PAGE_HTML.replace("democratic", "republican").replace("Democratic", "Republican")


def _col_letter(i: int) -> str:
    letters = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _workbook(rows: list[list[str | None]]) -> bytes:
    """Minimal real .xlsx: real column references (r="A1"), a `None`
    entry omitting that cell from the row's XML entirely — same shape
    as state_candidates_me.py's own test helper."""
    table = []
    for row in rows:
        for cell in row:
            if cell is not None and cell not in table:
                table.append(cell)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    shared = f"<sst {ns}>" + "".join(f"<si><t>{v}</t></si>" for v in table) + "</sst>"
    body = "".join(
        "<row>" + "".join(
            f'<c r="{_col_letter(i)}{rownum}" t="s"><v>{table.index(cell)}</v></c>'
            for i, cell in enumerate(row) if cell is not None
        ) + "</row>"
        for rownum, row in enumerate(rows, start=1)
    )
    sheet = f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


# Real rows, real 2026 U.S. Senate Democratic export, trimmed to two
# real counties. Sununu (the real Republican nominee) appears with his
# own real, small cross-party write-in count on the Democratic ballot;
# McMenamon appears WITH his real ", r" suffix here (only the
# Republican file drops it — see REP_ROWS below).
DEM_ROWS = [
    [None, "State of New Hampshire - Primary Election"],
    ["September 08, 2026", "United States Senator - Democratic"],
    ["Summary By Counties", "David Jarvis, d", "Chris Pappas, d", "John E. Sununu, r", "Richard A. McMenamon II, r", "Write-Ins "],
    ["Belknap", "50", "4954", "13", "0", "3"],
    ["Carroll", "42", "5098", "6", "0", "2"],
    ["TOTALS", "92", "10052", "19", "0", "5"],
]

# Real rows, real 2026 U.S. Senate Republican export, same two
# counties. Pappas (the real Democratic nominee) appears with his own
# real, small cross-party write-in count here. McMenamon's own real
# column genuinely carries NO ", r" suffix in this actual file — the
# live data quirk _office_choices' no-suffix fallback exists for.
REP_ROWS = [
    [None, "State of New Hampshire - Primary Election"],
    ["September 08, 2026", "United States Senator - Republican"],
    ["Summary By Counties", "Tom Alciere, r", "John E. Sununu, r", "Richard A. McMenamon ", "Chris Pappas, d", "Write-Ins "],
    ["Belknap", "0", "13", "0", "66", "16"],
    ["Carroll", "0", "6", "1", "67", "5"],
    ["TOTALS", "0", "19", "1", "133", "21"],
]


class TestLinks:
    def test_extracts_href_and_text_from_real_anchor_markup(self):
        links = nh._links(DEM_PAGE_HTML)
        assert ("/sites/g/files/ehbemt561/files/inline-documents/sonh/2026-sp-us-senator-summary-democratic.xlsx", "US Senator Summary") in links


class TestOfficeChoices:
    def test_democratic_file_keeps_only_its_own_party_and_excludes_the_cross_listed_republican(self):
        rows = nh._xlsx_rows(_workbook(DEM_ROWS), skip=2)
        choices = dict(nh._office_choices(rows, "d"))
        assert choices == {"Chris Pappas": 4954 + 5098, "David Jarvis": 50 + 42}
        assert "John E. Sununu" not in choices  # the other party's real cross-tab, not a genuine total
        assert "Richard A. McMenamon II" not in choices

    def test_republican_file_keeps_the_suffix_less_candidate_via_the_fallback(self):
        """Real regression: McMenamon's own column carries no ", r"
        suffix in the real Republican file -- must still be counted as
        this file's own party, not silently dropped."""
        rows = nh._xlsx_rows(_workbook(REP_ROWS), skip=2)
        choices = dict(nh._office_choices(rows, "r"))
        assert choices["John E. Sununu"] == 13 + 6
        assert choices["Richard A. McMenamon"] == 0 + 1
        assert "Chris Pappas" not in choices  # the other party's real cross-tab

    def test_totals_row_and_write_ins_column_are_excluded(self):
        rows = nh._xlsx_rows(_workbook(DEM_ROWS), skip=2)
        choices = dict(nh._office_choices(rows, "d"))
        # If TOTALS (92/10052/...) were summed in as a third "county",
        # Pappas would be 2x his real total instead of matching it.
        assert choices["Chris Pappas"] == 10052
        assert "Write-Ins" not in choices
        assert "" not in choices


def _patch(monkeypatch, texts: dict[str, str], files: dict[str, bytes], held=None):
    async def fake_text(client, url, label):
        for needle, html in texts.items():
            if needle in url:
                return html
        return None

    async def fake_bytes(client, url, label):
        for needle, content in files.items():
            if needle in url:
                return content
        return None

    monkeypatch.setattr(nh, "_get_text", fake_text)
    monkeypatch.setattr(nh, "_get_bytes", fake_bytes)
    monkeypatch.setattr(nh, "primary_date", lambda state, year: held)


class TestDiscoverOfficeLinks:
    @pytest.mark.asyncio
    async def test_finds_the_real_senate_summary_and_house_district_for_both_parties(self, monkeypatch):
        _patch(monkeypatch, {
            "/elections": ROOT_HTML,
            "state-primary-election-results": INDEX_HTML,
            "democratic-state-primary": DEM_PAGE_HTML,
            "republican-state-primary": REP_PAGE_HTML,
        }, {})
        offices = await nh._discover_office_links(None, 2026)
        assert set(offices.keys()) == {("S", None), ("H", 1)}
        assert "summary-democratic" in offices[("S", None)]["d"]
        assert "summary-republican" in offices[("S", None)]["r"]
        # The real per-county decoy link ("US Senator Belknap") must
        # never win the ("S", None) slot over the real summary link.
        assert "belknap" not in offices[("S", None)]["d"].lower()

    @pytest.mark.asyncio
    async def test_two_different_links_for_the_same_office_are_refused_not_guessed(self, monkeypatch):
        """Never observed live (only Senate publishes per-county
        breakdowns today, already excluded by the "summary" filter), but
        if a second real link ever parsed to the same office/district/
        party as one already found, silently keeping whichever came last
        would risk confirming the wrong candidate with no error."""
        dem_with_duplicate_house_link = DEM_PAGE_HTML.replace(
            "</a><br>\n",
            '</a><br>\n<a href="/sites/g/files/ehbemt561/files/inline-documents/sonh/decoy-cd1-democratic.xlsx">'
            "Representative in Congress District No. 1</a><br>\n",
            1,
        )
        _patch(monkeypatch, {
            "/elections": ROOT_HTML,
            "state-primary-election-results": INDEX_HTML,
            "democratic-state-primary": dem_with_duplicate_house_link,
            "republican-state-primary": REP_PAGE_HTML,
        }, {})
        offices = await nh._discover_office_links(None, 2026)
        assert "d" not in offices.get(("H", 1), {})
        assert "r" in offices.get(("H", 1), {})  # the OTHER party's own real link is unaffected

    @pytest.mark.asyncio
    async def test_no_results_page_for_this_year_yet_is_healthy_empty(self, monkeypatch):
        _patch(monkeypatch, {"/elections": "<a href=\"/2024-election-results\">2024 Election Results</a>"}, {})
        assert await nh._discover_office_links(None, 2026) == {}

    @pytest.mark.asyncio
    async def test_root_fetch_failure_is_not_silently_healthy(self, monkeypatch):
        _patch(monkeypatch, {}, {})
        assert await nh._discover_office_links(None, 2026) is None


class TestFetchConfirmedCandidates:
    def _full_patch(self, monkeypatch, held=None):
        _patch(monkeypatch, {
            "/elections": ROOT_HTML,
            "state-primary-election-results": INDEX_HTML,
            "democratic-state-primary": DEM_PAGE_HTML,
            "republican-state-primary": REP_PAGE_HTML,
        }, {
            "us-senator-summary-democratic": _workbook(DEM_ROWS),
            "us-senator-summary-republican": _workbook(REP_ROWS),
            "congressional-district-1-democratic": _workbook(DEM_ROWS),
            "congressional-district-1-republican": _workbook(REP_ROWS),
        }, held=held)

    @pytest.mark.asyncio
    async def test_confirms_the_real_nominees_once_settled(self, monkeypatch):
        self._full_patch(monkeypatch, held="2026-01-01")  # long past any settle_days floor
        records = await nh.fetch_confirmed_candidates(None, 2026, "NH", {})
        by_seat = {(r["office"], r["district"], r["party"]): r["last_name"] for r in records}
        assert by_seat[("S", None, "D")] == "Pappas"
        assert by_seat[("S", None, "R")] == "Sununu"
        assert by_seat[("H", 1, "D")] == "Pappas"
        assert by_seat[("H", 1, "R")] == "Sununu"

    @pytest.mark.asyncio
    async def test_withheld_before_settle_days_even_though_files_already_exist(self, monkeypatch):
        """Real files appeared within 2 days of the real primary -- a
        live count, not a certified one. Must not confirm early just
        because the download already succeeds. `held` is set in the
        future rather than a near-real date, so `_settled`'s own
        `(now - held).days >= settle_days` is guaranteed negative (never
        settled) regardless of what day this test actually runs on —
        an ordinary recent date would quietly stop proving anything once
        real time caught up past the real settle_days floor."""
        self._full_patch(monkeypatch, held="2099-01-01")
        assert await nh.fetch_confirmed_candidates(None, 2026, "NH", {}) == []

    @pytest.mark.asyncio
    async def test_no_cached_primary_date_does_not_block(self, monkeypatch):
        """held is None (calendar not cached yet) skips the gate rather
        than blocking forever — matches ma_pd43's own established
        fallback behavior for the identical shape."""
        self._full_patch(monkeypatch, held=None)
        records = await nh.fetch_confirmed_candidates(None, 2026, "NH", {})
        assert records  # not withheld

    @pytest.mark.asyncio
    async def test_no_year_page_yet_is_healthy_empty(self, monkeypatch):
        _patch(monkeypatch, {"/elections": "<a href=\"/2024-election-results\">2024 Election Results</a>"}, {}, held="2026-01-01")
        assert await nh.fetch_confirmed_candidates(None, 2026, "NH", {}) == []

    @pytest.mark.asyncio
    async def test_a_file_download_failure_fails_the_whole_fetch(self, monkeypatch):
        _patch(monkeypatch, {
            "/elections": ROOT_HTML,
            "state-primary-election-results": INDEX_HTML,
            "democratic-state-primary": DEM_PAGE_HTML,
            "republican-state-primary": REP_PAGE_HTML,
        }, {}, held="2026-01-01")  # discovery succeeds, but every file download 404s
        assert await nh.fetch_confirmed_candidates(None, 2026, "NH", {}) is None
