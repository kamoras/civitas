"""Tests for Oregon's confirmed-general-candidate strategy
(state_candidates_or.py).

fixtures_or_primary_results.pdf is a REAL 3-page slice of the actual 2026
primary "Abstract of Votes" PDF (fetched live 2026-09-08 via
records.sos.state.or.us's own DocumentStream.ashx?uri=16180585, 63 pages,
1.37MB), trimmed with pypdfium2's page-import (not hand-authored -- PDF's
binary structure isn't something to safely hand-write the way this
session's OOXML/HTML fixtures are) to just US Senator (both parties, 2
pages) and CD1 (both parties on one page, 1 page). Chosen specifically
because these 3 pages already prove out every real quirk this module's
own docstring documents: office+district detection from plain text (not
the table), multiple party blocks on one page (CD1), and the real
middle-name-wrap bug this module was built to survive (Senate R's
"David Brock Smith" -- the real regression case; a naive parser
misreads this as "Brock", not "Smith").

Every name and vote total below is read directly off this real fixture,
never recalled -- a real mistake happened once already while researching
this module (several candidates' first names were wrong on the first
draft, guessed from general political recollection instead of the
actual document) and is why this docstring says "read directly" rather
than "verified against real 2026 winners" the way earlier modules this
session could.
"""

import io
from pathlib import Path

import pdfplumber

from app.pipeline.fetch import state_candidates_or as orm

FIXTURES = Path(__file__).parent
PDF_BYTES = (FIXTURES / "fixtures_or_primary_results.pdf").read_bytes()

_DISCOVERY_JSON = {
    "value": [{
        "Title": "May 19, 2026",
        "Election_x0020_Date": "2026-05-19T05:00:00Z",
        "Results": (
            '<div><a href="https&#58;//records.sos.state.or.us/ORSOSCMSearch/'
            'Search/RecordViewer.aspx?uri=16180585" target="_blank">'
            "Official Results of May Primary​</a><br></div>"
        ),
    }],
}


def _patched(monkeypatch, json_body=_DISCOVERY_JSON, pdf=PDF_BYTES):
    async def fake_json(client, rl, url, label, **kw):
        return json_body

    async def fake_bytes(client, rl, url, label, **kw):
        return pdf

    monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
    monkeypatch.setattr(orm, "fetch_bytes_with_retry", fake_bytes)


class TestPageOffice:
    def test_reads_the_real_senate_page(self):
        with pdfplumber.open(io.BytesIO(PDF_BYTES)) as pdf:
            assert orm._page_office(pdf.pages[0].extract_text()) == ("S", None)

    def test_reads_the_real_house_district_page(self):
        with pdfplumber.open(io.BytesIO(PDF_BYTES)) as pdf:
            assert orm._page_office(pdf.pages[2].extract_text()) == ("H", 1)

    def test_a_non_federal_office_is_refused(self):
        assert orm._page_office("title\nGovernor\nDemocrat\nmore text") is None

    def test_too_few_lines_returns_none(self):
        assert orm._page_office("one line only") is None


class TestPageCandidates:
    def test_the_middle_name_wrap_does_not_overwrite_the_real_surname(self):
        # The real regression: "David Brock Smith"'s overflowing middle
        # name ("Brock") wraps onto its own row below the real surname
        # row ("*Smith") in the actual PDF. Without the awaiting_surnames
        # guard, that continuation row is mistaken for a second surname
        # row and silently overwrites the real one just before the Total
        # row is read -- attaching Smith's real 107,953 votes to the
        # surname "Brock" instead.
        with pdfplumber.open(io.BytesIO(PDF_BYTES)) as pdf:
            candidates = orm._page_candidates(pdf.pages[1])
        by_name = {name: votes for name, _party, votes in candidates}
        assert by_name["Smith"] == 107953
        assert "Brock" not in by_name

    def test_finds_all_real_senate_republican_candidates(self):
        with pdfplumber.open(io.BytesIO(PDF_BYTES)) as pdf:
            candidates = orm._page_candidates(pdf.pages[1])
        names = {name for name, _party, _votes in candidates}
        assert names == {"Barker", "Brown", "Burch", "McAlmond", "Perkins", "Skelton", "Smith"}

    def test_misc_column_is_excluded(self):
        with pdfplumber.open(io.BytesIO(PDF_BYTES)) as pdf:
            candidates = orm._page_candidates(pdf.pages[0])
        assert "Misc" not in {name for name, _party, _votes in candidates}

    def test_both_party_blocks_on_one_page_are_found(self):
        # CD1's real page carries a complete Democratic block AND a
        # complete Republican block, one below the other -- proves
        # current_party actually updates mid-page rather than being
        # fixed once per page.
        with pdfplumber.open(io.BytesIO(PDF_BYTES)) as pdf:
            candidates = orm._page_candidates(pdf.pages[2])
        by_name = {name: party for name, party, _votes in candidates}
        assert by_name == {"Ahmad": "D", "Bonamici": "D", "Kahl": "R", "Verbeek": "R"}


class TestFederalContests:
    def test_finds_every_real_federal_candidate_across_all_three_pages(self):
        contests = orm._federal_contests(PDF_BYTES)
        assert sorted((o, d, p, n) for o, d, p, n, _v in contests) == [
            ("H", 1, "D", "Ahmad"), ("H", 1, "D", "Bonamici"),
            ("H", 1, "R", "Kahl"), ("H", 1, "R", "Verbeek"),
            ("S", None, "D", "Merkley"), ("S", None, "D", "Wells"),
            ("S", None, "R", "Barker"), ("S", None, "R", "Brown"),
            ("S", None, "R", "Burch"), ("S", None, "R", "McAlmond"),
            ("S", None, "R", "Perkins"), ("S", None, "R", "Skelton"),
            ("S", None, "R", "Smith"),
        ]


class TestDiscoverPdfUrl:
    async def test_reads_the_real_uri_and_date(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return _DISCOVERY_JSON

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        result = await orm._discover_pdf_url(None, "OR", 2026)
        assert result == (
            "https://records.sos.state.or.us/ORSOSCMSearch/Search/DocumentStream.ashx?uri=16180585",
            "2026-05-19",
        )

    async def test_a_row_for_the_wrong_year_returns_none(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return _DISCOVERY_JSON

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        assert await orm._discover_pdf_url(None, "OR", 2028) is None

    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        assert await orm._discover_pdf_url(None, "OR", 2026) is None

    async def test_no_matching_uri_in_results_field_returns_none(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return {"value": [{"Election_x0020_Date": "2026-05-19T05:00:00Z", "Results": "no link here"}]}

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        assert await orm._discover_pdf_url(None, "OR", 2026) is None


class TestFetchConfirmedCandidates:
    async def test_real_primary_resolves_to_the_real_winners(self, monkeypatch):
        _patched(monkeypatch)
        result = await orm.fetch_confirmed_candidates(None, 2026, "OR", {"settle_days": 1})
        assert {"office": "S", "district": None, "party": "D", "last_name": "Merkley"} in result
        assert {"office": "S", "district": None, "party": "R", "last_name": "Smith"} in result
        assert {"office": "H", "district": 1, "party": "D", "last_name": "Bonamici"} in result
        assert {"office": "H", "district": 1, "party": "R", "last_name": "Kahl"} in result
        assert len(result) == 4

    async def test_no_row_for_the_requested_year_confirms_nothing_yet(self, monkeypatch):
        _patched(monkeypatch)
        result = await orm.fetch_confirmed_candidates(None, 2028, "OR", {"settle_days": 1})
        assert result == []

    async def test_not_yet_settled_confirms_nothing(self, monkeypatch):
        _patched(monkeypatch)
        result = await orm.fetch_confirmed_candidates(None, 2026, "OR", {"settle_days": 36500})
        assert result == []

    async def test_discovery_failure_returns_empty_not_none(self, monkeypatch):
        # A missing/malformed SharePoint row reads as "not published yet"
        # (an empty, healthy list is a real state early in a cycle),
        # distinct from a PDF fetch failure below, which is a genuine
        # fetch_failed.
        async def fake_json(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        assert await orm.fetch_confirmed_candidates(None, 2026, "OR", {}) == []

    async def test_pdf_fetch_failure_returns_none(self, monkeypatch):
        _patched(monkeypatch)

        async def fake_bytes(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(orm, "fetch_bytes_with_retry", fake_bytes)
        assert await orm.fetch_confirmed_candidates(None, 2026, "OR", {"settle_days": 1}) is None

    async def test_a_configured_runoff_threshold_withholds_a_sub_threshold_leader(self, monkeypatch):
        # Real data: Senate R's real 7-way field has Smith's real 107,953
        # as a minority of the group's total votes -- Oregon nominates by
        # plurality so this confirms today, but proves runoff_threshold_
        # pct is actually wired through config, not just harmlessly
        # present at null.
        _patched(monkeypatch)
        result = await orm.fetch_confirmed_candidates(
            None, 2026, "OR", {"settle_days": 1, "runoff_threshold_pct": 50.0},
        )
        assert {"office": "S", "district": None, "party": "R", "last_name": "Smith"} not in result
        # Merkley's real 2-way majority is untouched by the same threshold.
        assert {"office": "S", "district": None, "party": "D", "last_name": "Merkley"} in result
