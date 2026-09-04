"""Tests for Mississippi's confirmed-general-candidate strategy
(state_candidates_ms.py).

fixtures_ms_republican_primary_2026.pdf (9 pages, 64KB) and
fixtures_ms_democratic_primary_2026.pdf (17 pages, 82KB) are REAL — the
Secretary of State's own "Official Recapitulation" PDFs for the real,
certified 2026-03-10 federal primary, fetched live 2026-09-04. Kept whole:
both are what prove a candidate's row, repeated once per page as the wide
82-county table slides across pages, resolves to their real FINAL total
rather than one page's partial county count — the Republican file for the
ordinary case (every candidate's row spans pages 1-9 without a break), the
Democratic file for the real oddity that motivated the whole running-total-
map design: Jeffrey Hulum III (real, AP-confirmed CD4 nominee) is appended
in his own trailing block on pages 10-17, well after the shared table's
own pagination already finished on page 9, with no contest header of his
own — he must inherit "CD4" from the last header seen before his block.
"""

from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_ms as ms

_GOP_PDF = (Path(__file__).parent / "fixtures_ms_republican_primary_2026.pdf").read_bytes()
_DEM_PDF = (Path(__file__).parent / "fixtures_ms_democratic_primary_2026.pdf").read_bytes()


class TestParseRecapPdf:
    def test_real_republican_primary_resolves_to_the_real_winners(self):
        results = ms._parse_recap_pdf(_GOP_PDF)
        assert sorted(
            (r["office"], r["district"], r["party"], r["last_name"]) for r in results
        ) == [
            ("H", 1, "R", "Kelly"),
            ("H", 2, "R", "Eller"),
            ("H", 3, "R", "Guest"),
            ("H", 4, "R", "Ezell"),
            ("S", None, "R", "Hyde-Smith"),
        ]

    def test_real_democratic_primary_resolves_to_the_real_winners(self):
        results = ms._parse_recap_pdf(_DEM_PDF)
        assert sorted(
            (r["office"], r["district"], r["party"], r["last_name"]) for r in results
        ) == [
            ("H", 1, "D", "Johnson"),
            ("H", 2, "D", "Thompson"),
            ("H", 3, "D", "Chiaradio"),
            ("H", 4, "D", "Hulum"),
            ("S", None, "D", "Colom"),
        ]

    def test_a_late_qualified_candidate_inherits_the_last_seen_contest(self):
        # Jeffrey Hulum III's block carries no "US House Of Rep 04-4th
        # Congressional District" header of its own — this pins that he
        # still resolves to CD4, not to whatever contest happened to be
        # open when his SPECIFIC block started, which would be a bug if
        # `current` weren't a single value persisted across the whole
        # document rather than reset per page.
        results = ms._parse_recap_pdf(_DEM_PDF)
        hulum = next(r for r in results if r["last_name"] == "Hulum")
        assert (hulum["office"], hulum["district"]) == ("H", 4)

    def test_a_losing_candidate_in_a_real_crowded_field_is_excluded(self):
        # The real Democratic Senate primary had 3 candidates (Colom,
        # Littell, Till); only Colom's real majority survives.
        results = ms._parse_recap_pdf(_DEM_PDF)
        senate = [r for r in results if r["office"] == "S"]
        assert senate == [{"office": "S", "district": None, "party": "D", "last_name": "Colom"}]


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    async def _patched(self, monkeypatch, *, gop=_GOP_PDF, dem=_DEM_PDF):
        async def fake_discover(client, year, party_label):
            return f"https://example.gov/{party_label}.pdf"

        async def fake_get(client, url, label):
            class _Resp:
                content = gop if "republican" in url else dem
            return _Resp()

        monkeypatch.setattr(ms, "_discover_pdf_url", fake_discover)
        monkeypatch.setattr(ms, "_get", fake_get)

    async def test_both_real_parties_combine_into_one_result_list(self, monkeypatch):
        await self._patched(monkeypatch)
        result = await ms.fetch_confirmed_candidates(None, 2026, "MS", {})
        assert len(result) == 10
        assert {"office": "S", "district": None, "party": "R", "last_name": "Hyde-Smith"} in result
        assert {"office": "S", "district": None, "party": "D", "last_name": "Colom"} in result

    async def test_neither_party_published_yet_is_a_healthy_empty_list(self, monkeypatch):
        async def fake_discover(client, year, party_label):
            return None

        monkeypatch.setattr(ms, "_discover_pdf_url", fake_discover)
        assert await ms.fetch_confirmed_candidates(None, 2026, "MS", {}) == []

    async def test_a_download_failure_returns_none(self, monkeypatch):
        async def fake_discover(client, year, party_label):
            return "https://example.gov/republican.pdf"

        async def fake_get(client, url, label):
            return None

        monkeypatch.setattr(ms, "_discover_pdf_url", fake_discover)
        monkeypatch.setattr(ms, "_get", fake_get)
        assert await ms.fetch_confirmed_candidates(None, 2026, "MS", {}) is None

    async def test_an_unparsable_pdf_returns_none(self, monkeypatch):
        async def fake_discover(client, year, party_label):
            return "https://example.gov/republican.pdf"

        async def fake_get(client, url, label):
            class _Resp:
                content = b"not a real pdf"
            return _Resp()

        monkeypatch.setattr(ms, "_discover_pdf_url", fake_discover)
        monkeypatch.setattr(ms, "_get", fake_get)
        assert await ms.fetch_confirmed_candidates(None, 2026, "MS", {}) is None


class TestDiscoverPdfUrl:
    @pytest.mark.asyncio
    async def test_the_three_hop_chain_resolves_the_real_pdf_link(self, monkeypatch):
        index_html = (
            '<a href="/elections-voting/election-results/2026/'
            'march-10-2026-republican-primary-results">x</a>'
        )
        results_page_html = '<iframe src="https://sos.ms.gov/x.aspx?party=republican">'
        iframe_html = (
            '<a href="https://www.sos.ms.gov/content/documents/elections/2026/'
            'republican%20primary%202026.pdf">\n'
            "2026 Republican Primary Election Results\n</a>"
        )

        async def fake_get(client, url, label):
            class _Resp:
                text = ""
            if url == ms._INDEX_URL:
                _Resp.text = index_html
            elif "election-results/2026" in url:
                _Resp.text = results_page_html
            elif "x.aspx" in url:
                _Resp.text = iframe_html
            return _Resp()

        monkeypatch.setattr(ms, "_get", fake_get)
        url = await ms._discover_pdf_url(None, 2026, "republican")
        assert url == (
            "https://www.sos.ms.gov/content/documents/elections/2026/"
            "republican%20primary%202026.pdf"
        )

    @pytest.mark.asyncio
    async def test_no_results_page_listed_yet_yields_none_rather_than_a_broken_url(self, monkeypatch):
        async def fake_get(client, url, label):
            class _Resp:
                text = "<html>nothing here</html>"
            return _Resp()

        monkeypatch.setattr(ms, "_get", fake_get)
        assert await ms._discover_pdf_url(None, 2026, "republican") is None
