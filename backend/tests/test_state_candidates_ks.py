"""Tests for Kansas's confirmed-general-candidate strategy
(state_candidates_ks.py).

fixtures_ks_primary_2026.pdf (15 pages, 41KB) is REAL — the Secretary of
State's own "Official Vote Totals" PDF for the real, certified 2026-08-04
primary, fetched live 2026-09-04. Kept whole: the ~140 non-federal race
sections after the 5 federal ones on page 1 are what prove the race-reset
logic actually works — without it, every one of those state house/senate
candidates falsely inherited whichever federal race printed last (found
and fixed during initial build, not a hypothetical).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import state_candidates_ks as ks

_PRIMARY_PDF = (Path(__file__).parent / "fixtures_ks_primary_2026.pdf").read_bytes()


def _resp(body=None, *, text=None, content=None):
    return SimpleNamespace(text=text, content=content, json=lambda: body)


class TestParseTotalsPdf:
    def test_real_primary_resolves_to_the_real_certified_winners(self):
        results = ks._parse_totals_pdf(_PRIMARY_PDF)
        assert sorted(
            (r["office"], r["district"], r["party"], r["last_name"]) for r in results
        ) == [
            ("H", 1, "D", "Reinhold"),
            ("H", 1, "R", "Mann"),
            ("H", 2, "D", "Coover"),
            ("H", 2, "R", "Schmidt"),
            ("H", 3, "D", "Davids"),
            ("H", 3, "R", "Jenkins"),
            ("H", 4, "D", "Tyndell"),
            ("H", 4, "R", "Estes"),
            ("S", None, "D", "Hamilton"),
            ("S", None, "R", "Marshall"),
        ]

    def test_a_crowded_senate_primary_correctly_excludes_the_losers(self):
        # The real Democratic Senate primary had 11 candidates; only
        # Hamilton's real plurality (34.63%) survives.
        results = ks._parse_totals_pdf(_PRIMARY_PDF)
        senate_d = [r for r in results if r["office"] == "S" and r["party"] == "D"]
        assert senate_d == [{"office": "S", "district": None, "party": "D", "last_name": "Hamilton"}]

    def test_non_federal_races_after_the_federal_section_are_excluded(self):
        # The real document prints ~140 state house/senate races AFTER
        # the 5 federal ones on page 1 -- this is the regression the
        # race-reset logic exists for: without it, every one of those
        # non-federal candidates falsely inherited "US House 4" (the
        # last federal section printed) rather than being ignored.
        results = ks._parse_totals_pdf(_PRIMARY_PDF)
        assert len(results) == 10
        assert all(r["office"] in ("H", "S") for r in results)
        house_districts = {r["district"] for r in results if r["office"] == "H"}
        assert house_districts == {1, 2, 3, 4}



@pytest.mark.asyncio
class TestDiscoverPdfUrl:
    async def test_matches_the_link_by_its_title_text_not_a_url_template(self, monkeypatch):
        # The real listing page also carries prior years' links whose
        # FILE SLUG varies ("2024-Primary-Official-Vote-Totals.pdf" vs
        # 2026's "2026-Primary-Election-Official-Vote-Totals.pdf") --
        # only the anchor's own title text reliably carries the year.
        html = (
            '<a href="24elec/2024-Primary-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2024 Primary Election Official results in a new window">x</a>'
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            assert url == ks.LISTING_URL
            return _resp(text=html)

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        result = await ks._discover_pdf_url(None, 2026)
        assert result == "https://sos.ks.gov/elections/26elec/2026-Primary-Election-Official-Vote-Totals.pdf"

    async def test_a_year_not_yet_listed_yields_none(self, monkeypatch):
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            return _resp(text=html)

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks._discover_pdf_url(None, 2028) is None

    async def test_listing_page_fetch_failure_yields_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks._discover_pdf_url(None, 2026) is None


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    def _patched(self, monkeypatch):
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            if url == ks.LISTING_URL:
                return _resp(text=html)
            if url.endswith(".pdf"):
                return _resp(content=_PRIMARY_PDF)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(ks, "fetch_with_retry", fake)

    async def test_real_primary_resolves_end_to_end(self, monkeypatch):
        self._patched(monkeypatch)
        result = await ks.fetch_confirmed_candidates(None, 2026, "KS", {})
        assert len(result) == 10
        assert {"office": "S", "district": None, "party": "R", "last_name": "Marshall"} in result

    async def test_not_yet_published_this_cycle_is_a_healthy_empty_list(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return _resp(text="<html>nothing here</html>")

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks.fetch_confirmed_candidates(None, 2026, "KS", {}) == []

    async def test_pdf_download_failure_returns_none(self, monkeypatch):
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            if url == ks.LISTING_URL:
                return _resp(text=html)
            return None

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks.fetch_confirmed_candidates(None, 2026, "KS", {}) is None

    async def test_an_unparsable_pdf_returns_none(self, monkeypatch):
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            if url == ks.LISTING_URL:
                return _resp(text=html)
            if url.endswith(".pdf"):
                return _resp(content=b"not a real pdf")
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks.fetch_confirmed_candidates(None, 2026, "KS", {}) is None
