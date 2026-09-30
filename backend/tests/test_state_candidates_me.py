"""Tests for Maine's confirmed-general-candidate strategy
(state_candidates_me.py).

fixtures_me_results_page.html is a REAL, trimmed excerpt of the live
results-listing page (fetched 2026-09-09): both real `<h2>` sections, a
real Ranked Choice `<h3>` (the real CD2 Democratic primary, including its
real "Cast Vote Records" `<h4>` and two of its real CVR links, kept to
prove that boundary is actually respected) plus a real NON-federal RCV
office ("Governor - Democratic") kept specifically to prove parse_office
excludes it even though it has its own real RCV Summary Report link, and
the real Non-Ranked-Choice `<h3>`s for Senate/CD1/CD2, plus a real
non-federal one ("State Senate") to prove that's excluded too.

fixtures_me_cd2_dem_rcv_summary.pdf is the REAL, unmodified "RCV Summary
Report" PDF for the real 2026 CD2 Democratic primary.

The xlsx fixtures below are built (not downloaded) with `_workbook()`,
but every row is REAL data copied from the actual downloaded 2026 U.S.
Senate Democratic and CD1 Democratic FINAL exports (fetched 2026-09-09),
trimmed to a handful of real towns plus each file's own real anomaly
rows: the Senate file's real "STATE UOCAVA" row (a genuinely blank
Municipality-adjacent cell in the source, which is why this fixture
constructs it with `None` for that cell rather than an empty string —
see state_candidates_tabular.py's _xlsx_rows fix) and its own real
all-caps, singular "STATE TOTAL" grand-total row; the CD1 file's real
title-case, plural "State Totals" row. Both totals rows are real
numbers, not fabricated, and were the direct cause of a real bug found
while building this module: matching only one of the two spellings let
the Senate file's own grand-total row get silently double-counted as if
it were a real town (see _municipality_choices' own docstring).
"""

from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_me as me
from tests.test_state_candidates_tabular import _workbook

FIXTURES = Path(__file__).parent
LANDING_HTML = (FIXTURES / "fixtures_me_results_page.html").read_text()
RCV_PDF = (FIXTURES / "fixtures_me_cd2_dem_rcv_summary.pdf").read_bytes()


# Real rows from the actual downloaded 2026 U.S. Senate Democratic FINAL
# export: two real towns (Auburn, Durham), the real "STATE UOCAVA" row
# (its own CTY cell genuinely blank/omitted in the source), and the
# file's own real, all-caps, non-plural grand total.
_SENATE_DEM_HEADER = ["CTY", "Municipality", "COSTELLO, DAVID A", "MILLS, JANET T", "PLATNER, GRAHAM C", "LAFLAMME, ANDREA ", "BLANK", "TBC"]
_SENATE_DEM_ROWS = [
    _SENATE_DEM_HEADER,
    # Header-annotation row: each candidate's home town, blank CTY/Municipality.
    [None, None, "BRUNSWICK", "FARMINGTON", "SULLIVAN", "BANGOR", None, None],
    ["AND", "Auburn", "280", "564", "1894", "9", "72", "2819"],
    ["AND", "Durham", "64", "107", "564", "0", "18", "753"],
    [None, "STATE UOCAVA", "63", "146", "596", "8", "3", "816"],
    [None, "STATE TOTAL", "17560", "41644", "156084", "1114", "6003", "222405"],
]

# Real rows from the actual downloaded 2026 Representative to Congress
# District 1 Democratic FINAL export: two real towns, a real county
# subtotal row (own real numbers), and the file's own real, title-case,
# plural grand total -- deliberately a DIFFERENT spelling than the
# Senate file's "STATE TOTAL" above (see module docstring).
_CD1_DEM_HEADER = ["DIS", "CTY", "Municipality", "PINGREE, CHELLIE", "BLANK", "TBC"]
_CD1_DEM_ROWS = [
    _CD1_DEM_HEADER,
    [None, None, None, "NORTH HAVEN", None, None],
    ["1", "CUM", "Baldwin", "131", "7", "138"],
    ["1", "CUM", "Bridgton", "811", "75", "886"],
    [None, None, "YOR Totals", "32055", "2926", "34981"],
    [None, None, "State Totals", "128257", "10664", "138921"],
]


class TestResultsPageReader:
    def test_finds_every_real_federal_entry(self):
        """Exactly these six, which is also what proves the refusals the
        fixture was built for: the real "Governor - Democratic" RCV
        heading has its own real RCV Summary Report link but is not
        federal; the real "State Senate" plurality heading is not either;
        and the <h4>Cast Vote Records</h4> boundary ends CD2 Democratic's
        own link collection -- otherwise its real CVR export links would
        be misread as more results sources for that office (CD2 D is one
        "rcv" entry, nothing more)."""
        entries = me._discover_entries(LANDING_HTML, 2026)
        assert sorted(entries) == sorted([
            ("rcv", ("H", 2), "D", "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/CG2%20Democratic%20RCV%20Summary%20Report.pdf"),
            ("xlsx", ("S", None), "D", "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/US%20Senate%20DEM%20-%20FINAL.xlsx"),
            ("xlsx", ("S", None), "R", "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/US%20Senate%20REP%20-%20FINAL.xlsx"),
            ("xlsx", ("H", 1), "D", "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/Rep%20to%20Congress%20Dist%201%20FINAL.xlsx"),
            ("xlsx", ("H", 1), "R", "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/Rep%20to%20Congress%20Dis%201%20REP%20-%20FINAL.xlsx"),
            ("xlsx", ("H", 2), "R", "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/Rep%20to%20Congress%20Dis%202%20REP%20-%20FINAL.xlsx"),
        ])

    def test_a_stale_years_page_yields_nothing(self):
        """The page's own h2 text carries the cycle's year -- asking
        about a year this same live content never claims to be must not
        silently confirm 2026's results as if they were some other
        year's."""
        assert me._discover_entries(LANDING_HTML, 2028) == []

class TestMunicipalityChoices:
    def test_sums_real_towns_and_uocava_but_not_the_grand_total(self):
        """Real regression: the Senate file's own grand-total row is
        spelled "STATE TOTAL" (no trailing s, all caps) -- a check that
        only recognized "State Totals" (as CD1's own file spells it)
        would silently sum this row in as a fifth "municipality",
        doubling every candidate's true total. Expected sums here are
        the REAL per-candidate totals from the actual downloaded file
        (Auburn + Durham + STATE UOCAVA), verified to independently
        equal that file's own STATE TOTAL row for a candidate who only
        appears in these three rows would not hold here since these are
        a trimmed subset -- what this test actually proves is that the
        grand-total row's own numbers are excluded, not summed in."""
        rows = me._xlsx_rows(_workbook(_SENATE_DEM_ROWS))
        choices = dict(me._municipality_choices(rows))
        # Auburn + Durham + STATE UOCAVA only -- the STATE TOTAL row's own
        # 156084/41644/17560/1114 must NOT be added on top. Row 2's
        # home-town annotations (blank Municipality cell, "BRUNSWICK"
        # etc.) are skipped too.
        assert choices["PLATNER, GRAHAM C"] == 1894 + 564 + 596
        assert choices["MILLS, JANET T"] == 564 + 107 + 146
        assert choices["COSTELLO, DAVID A"] == 280 + 64 + 63
        assert choices["LAFLAMME, ANDREA "] == 9 + 0 + 8

    def test_a_differently_spelled_total_row_is_also_excluded(self):
        """CD1's own file spells its grand total "State Totals" (plural,
        title case) -- a different string than the Senate file's "STATE
        TOTAL", both of which must be caught by the same rule."""
        rows = me._xlsx_rows(_workbook(_CD1_DEM_ROWS))
        choices = dict(me._municipality_choices(rows))
        assert choices["PINGREE, CHELLIE"] == 131 + 811  # Baldwin + Bridgton only
        # Neither the county subtotal (32055) nor the state grand total
        # (128257) may appear anywhere in this sum.

    def test_a_differently_cased_bookkeeping_column_is_still_excluded(self):
        """Real files in this exact family have already proven twice
        that Maine's own exports don't hold one consistent spelling for
        the same column/row meaning (the _xlsx_rows column-shift bug;
        the "State Totals"/"STATE TOTAL" row-spelling bug above) -- an
        exact "BLANK"/"Municipality" match would repeat that class of
        bug a third time the moment some future export spells either
        header differently."""
        rows = me._xlsx_rows(_workbook([
            ["cty", "MUNICIPALITY", "PLATNER, GRAHAM C", " Blank ", "tbc"],
            ["AND", "Auburn", "1894", "72", "1966"],
        ]))
        choices = dict(me._municipality_choices(rows))
        assert choices == {"PLATNER, GRAHAM C": 1894}


class TestParseRcvSummary:
    def test_reads_the_real_certified_winner(self):
        text = me._pdf_text(RCV_PDF)
        assert me._parse_rcv_summary(text) == "Dunlap, Matthew G."

    def test_cross_check_catches_a_winner_line_that_disagrees_with_the_round_table(self):
        text = me._pdf_text(RCV_PDF).replace("Winner(s) Dunlap, Matthew G.", "Winner(s) Baldacci, Joseph M.")
        assert me._parse_rcv_summary(text) is None

    def test_a_tie_for_the_final_rounds_top_spot_is_refused(self):
        text = (
            "Winner(s) Smith, Pat\n"
            "Rounds Round 1 Round 2\n"
            "Smith, Pat 100 500\n"
            "Jones, Alex 100 500\n"
        )
        assert me._parse_rcv_summary(text) is None

    def test_missing_winner_line_yields_none(self):
        assert me._parse_rcv_summary("Rounds Round 1\nSmith, Pat 100\n") is None


def _patched(monkeypatch, html=LANDING_HTML, files=None):
    """The landing page, and each download by the first `files` needle its
    URL contains; any other PDF is the real CD2 RCV summary, any other
    xlsx a zero-vote placeholder."""
    files = files or {}

    async def fake_text(client, rl, url, label, **kw):
        return html

    async def fake_bytes(client, rl, url, label, **kw):
        for needle, payload in files.items():
            if needle in url:
                return payload
        if url.endswith(".pdf"):
            return RCV_PDF
        return _workbook([["Municipality", "NOBODY"], ["Anytown", "0"]])

    monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
    monkeypatch.setattr(me, "fetch_bytes_with_retry", fake_bytes)


class TestFetchConfirmedCandidates:
    @pytest.mark.asyncio
    async def test_confirms_the_real_rcv_and_plurality_nominees_together(self, monkeypatch):
        _patched(monkeypatch, files={
            "US%20Senate%20DEM": _workbook(_SENATE_DEM_ROWS),
            "US%20Senate%20REP": _workbook([["Municipality", "COLLINS, SUSAN M"], ["Auburn", "900"]]),
            "Rep%20to%20Congress%20Dist%201%20FINAL": _workbook(_CD1_DEM_ROWS),
            "Dis%201%20REP": _workbook([["Municipality", "RUSSELL, RONALD G"], ["Auburn", "500"]]),
            "Dis%202%20REP": _workbook([["Municipality", "LEPAGE, PAUL R"], ["Auburn", "700"]]),
        })
        records = await me.fetch_confirmed_candidates(None, 2026, "ME", {})
        by_seat = {(r["office"], r["district"], r["party"]): r["last_name"] for r in records}
        # RCV path (the PDF) preserves the document's own mixed case;
        # the xlsx path's names come straight from ME's own all-caps
        # column headers -- both are real, unmodified source casing.
        assert by_seat[("H", 2, "D")] == "Dunlap"
        assert by_seat[("S", None, "D")] == "PLATNER"
        assert by_seat[("S", None, "R")] == "COLLINS"
        assert by_seat[("H", 1, "D")] == "PINGREE"
        assert by_seat[("H", 1, "R")] == "RUSSELL"
        assert by_seat[("H", 2, "R")] == "LEPAGE"

    @pytest.mark.asyncio
    async def test_a_bad_rcv_cross_check_fails_the_whole_fetch(self, monkeypatch):
        """A downloaded-but-untrustworthy RCV summary is a genuine
        problem with this cycle's data, not a healthy "nothing yet" --
        must return None (fetch failed), never a partial/empty list."""
        _patched(monkeypatch)
        monkeypatch.setattr(
            me, "_pdf_text",
            lambda content: "Winner(s) Nobody, Real\nRounds Round 1\nSomeone, Else 5\n",
        )
        assert await me.fetch_confirmed_candidates(None, 2026, "ME", {}) is None

    @pytest.mark.asyncio
    async def test_a_failed_landing_page_fetch_returns_none(self, monkeypatch):
        async def fake_text(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        assert await me.fetch_confirmed_candidates(None, 2026, "ME", {}) is None

    @pytest.mark.asyncio
    async def test_no_federal_entries_yet_is_a_healthy_empty_list(self, monkeypatch):
        async def fake_text(client, rl, url, label, **kw):
            return "<h2><strong>June 9, 2027 - Primary Election - Ranked Choice Offices</strong></h2>"

        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        assert await me.fetch_confirmed_candidates(None, 2026, "ME", {}) == []

    @pytest.mark.asyncio
    async def test_an_unparseable_xlsx_download_fails_the_whole_fetch(self, monkeypatch):
        _patched(monkeypatch, files={"US%20Senate%20DEM": b"not a real xlsx"})
        assert await me.fetch_confirmed_candidates(None, 2026, "ME", {}) is None


# ── Statewide executive contests (Governor) ──────────────────────────
#
# fixtures_me_gov_dem_rcv_summary.pdf and fixtures_me_gov_rep_rcv_summary
# .pdf are the REAL, unmodified "RCV Summary Report" PDFs for the 2026
# Democratic and Republican governor primaries (fetched 2026-09-28 from
# the results page's own links). The landing-page fixture carries both
# real "Governor - ..." headings, plus the real "Senate District 4 -
# Republican" RCV heading and the real "County Treasurer" and "District
# Attorney" headings, none of which is a statewide executive office.

GOV_DEM_PDF = (FIXTURES / "fixtures_me_gov_dem_rcv_summary.pdf").read_bytes()
GOV_REP_PDF = (FIXTURES / "fixtures_me_gov_rep_rcv_summary.pdf").read_bytes()
_BASE = "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/"


class TestStatewideDiscovery:
    def test_both_real_governor_tabulations_are_found(self):
        entries = me._discover_entries(LANDING_HTML, 2026, statewide=True)
        statewide = sorted(e for e in entries if e[1][0] not in ("S", "H"))
        assert statewide == [
            ("rcv", ("governor", None), "D", _BASE + "GOV%20Democratic%20RCV%20Summary%20Report.pdf"),
            ("rcv", ("governor", None), "R", _BASE + "GOV%20Republican%20RCV%20Summary%20Reportxlsx.pdf"),
        ]

    def test_legislative_and_county_headings_are_still_refused(self):
        urls = [url for *_, url in me._discover_entries(LANDING_HTML, 2026, statewide=True)]
        assert not any(k in url for url in urls for k in ("SS4", "County%20Treasurer", "District%20Attorney"))


class TestGovernorRcvWinners:
    def test_the_real_democratic_winner_came_from_behind(self):
        # Nirav D. Shah led every round but the last: 58,606 first
        # choices to Hannah M. Pingree's 50,552, then 86,950 to 111,750
        # in round 4. A first-choice plurality would name Shah.
        assert me._parse_rcv_summary(me._pdf_text(GOV_DEM_PDF)) == "Pingree, Hannah M."

    def test_the_real_republican_winner(self):
        assert me._parse_rcv_summary(me._pdf_text(GOV_REP_PDF)) == "Charles, Robert B."

    def test_names_are_put_in_reading_order(self):
        assert me._first_last("Pingree, Hannah M.") == "Hannah M. Pingree"
        assert me._first_last("King, Angus, III") == "Angus King III"
        assert me._first_last("PINGREE, HANNAH M") == "HANNAH M PINGREE"
        assert me._first_last("Plain Name") == "Plain Name"


class TestFetchWithStatewideOffices:
    @pytest.mark.asyncio
    async def test_the_real_governor_nominees(self, monkeypatch):
        _patched(monkeypatch, files={
            "GOV%20Democratic": GOV_DEM_PDF, "GOV%20Republican": GOV_REP_PDF,
        })
        records = await me.fetch_confirmed_candidates(None, 2026, "ME", {"statewide_offices": True})
        statewide = sorted(
            (r["office"], r["district"], r["party"], r["last_name"])
            for r in records if r["office"] not in ("S", "H")
        )
        assert statewide == [
            ("governor", None, "D", "Hannah M. Pingree"),
            ("governor", None, "R", "Robert B. Charles"),
        ]
        # The federal records are untouched by the opt-in.
        assert {"office": "H", "district": 2, "party": "D", "last_name": "Dunlap",
                "display_name": "Dunlap, Matthew G."} in records

    @pytest.mark.asyncio
    async def test_without_the_opt_in_no_governor_is_read(self, monkeypatch):
        _patched(monkeypatch, files={
            "GOV%20Democratic": GOV_DEM_PDF, "GOV%20Republican": GOV_REP_PDF,
        })
        records = await me.fetch_confirmed_candidates(None, 2026, "ME", {})
        assert all(r["office"] in ("S", "H") for r in records)

    @pytest.mark.asyncio
    async def test_a_plain_count_governor_primary_is_resolved_by_votes(self, monkeypatch):
        # A party whose field is too small for RCV is posted as a plain
        # FINAL count under "Non-Ranked Choice Offices", as the federal
        # ones are. Hypothetical heading and figures in the real shape.
        html = (
            "<h2><strong>June 9, 2026 - Primary Election - Non-Ranked Choice Offices</strong></h2>"
            '<h3><strong>Governor</strong></h3><ul><li><a href="/x/Governor%20GRN%20-%20FINAL.xlsx">'
            "Green Independent</a></li></ul>"
        )
        _patched(monkeypatch, html=html, files={"Governor%20GRN": _workbook([
            ["DIS", "CTY", "Municipality", "SMITH, PAT A", "JONES, LEE", "BLANK", "TBC"],
            [None, None, None, "PORTLAND", "BANGOR", None, None],
            ["1", "CUM", "Portland", "300", "120", "5", "425"],
            ["2", "PEN", "Bangor", "40", "90", "1", "131"],
            [None, None, "State Totals", "340", "210", "6", "556"],
        ])})
        records = await me.fetch_confirmed_candidates(None, 2026, "ME", {"statewide_offices": True})
        assert records == [{"office": "governor", "district": None, "party": "G", "last_name": "PAT A SMITH"}]

    @pytest.mark.asyncio
    async def test_an_unreadable_governor_tabulation_fails_the_whole_fetch(self, monkeypatch):
        # Returning the federal records alone would sync Maine as
        # "checked, no governor's race".
        _patched(monkeypatch, files={
            "GOV%20Democratic": b"not a pdf", "GOV%20Republican": GOV_REP_PDF,
        })
        assert await me.fetch_confirmed_candidates(None, 2026, "ME", {"statewide_offices": True}) is None
