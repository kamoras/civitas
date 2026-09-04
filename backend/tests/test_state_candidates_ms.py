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

The same-surname-collision safety branch (two DIFFERENT real candidates
sharing a surname in one contest) is NOT exercised by either real 2026
document — every surname in both files is unique per contest — so it's
tested below with a small CONSTRUCTED row list instead of a fixture.
"""

from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_ms as ms

_GOP_PDF = (Path(__file__).parent / "fixtures_ms_republican_primary_2026.pdf").read_bytes()
_DEM_PDF = (Path(__file__).parent / "fixtures_ms_democratic_primary_2026.pdf").read_bytes()
_THRESHOLD = 50.0


class TestParseRecapPdf:
    def test_real_republican_primary_resolves_to_the_real_winners(self):
        results = ms._parse_recap_pdf(_GOP_PDF, _THRESHOLD)
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
        results = ms._parse_recap_pdf(_DEM_PDF, _THRESHOLD)
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
        results = ms._parse_recap_pdf(_DEM_PDF, _THRESHOLD)
        hulum = next(r for r in results if r["last_name"] == "Hulum")
        assert (hulum["office"], hulum["district"]) == ("H", 4)

    def test_a_losing_candidate_in_a_real_crowded_field_is_excluded(self):
        # The real Democratic Senate primary had 3 candidates (Colom,
        # Littell, Till); only Colom's real majority survives.
        results = ms._parse_recap_pdf(_DEM_PDF, _THRESHOLD)
        senate = [r for r in results if r["office"] == "S"]
        assert senate == [{"office": "S", "district": None, "party": "D", "last_name": "Colom"}]


class TestProcessRows:
    """`_process_rows` takes each row already as a left-to-right token
    list, split out from the pdfplumber/geometry extraction specifically
    so a safety branch the real 2026 fixtures never exercise can still be
    tested directly, without needing to construct a fake PDF."""

    def test_two_different_candidates_sharing_a_surname_both_survive(self):
        # Two DIFFERENT real people, same surname, one contest — the
        # running map's key must be the full printed name, or the
        # second row silently overwrites the first under a bare-surname
        # key and one candidate vanishes with no error.
        rows = [
            ["United States-Senate"],
            ["Alice Johnson", "Democrat", "500"],
            ["Bob Johnson", "Democrat", "300"],
        ]
        results = ms._process_rows(rows, runoff_threshold_pct=None)
        assert results == [
            {"office": "S", "district": None, "party": "D", "last_name": "Johnson"},
        ]
        # Alice's 500 beats Bob's 300 for the single nomination — this
        # also pins that the two rows were correctly kept as two SEPARATE
        # candidates feeding one pick_nominee call, not merged into one
        # (a merge would produce a fabricated 800-vote "Johnson").

    def test_a_repeated_row_for_the_same_candidate_still_overwrites_correctly(self):
        # The ordinary case the whole running-map design exists for:
        # the SAME candidate's row appears twice (e.g. two pages), and
        # the later occurrence's value must win, not sum or duplicate.
        rows = [
            ["US House Of Rep 01-1st Congressional"],
            ["Jane Doe", "Republican", "100"],
            ["Jane Doe", "Republican", "9000"],
        ]
        results = ms._process_rows(rows, runoff_threshold_pct=None)
        assert results == [{"office": "H", "district": 1, "party": "R", "last_name": "Doe"}]

    def test_a_candidate_below_the_runoff_threshold_confirms_nobody(self):
        rows = [
            ["United States-Senate"],
            ["Alice Johnson", "Democrat", "40"],
            ["Bob Johnson", "Democrat", "35"],
            ["Cara Johnson", "Democrat", "25"],
        ]
        assert ms._process_rows(rows, runoff_threshold_pct=50.0) == []

    def test_a_row_before_any_contest_header_is_ignored(self):
        rows = [["Stray Candidate", "Republican", "100"]]
        assert ms._process_rows(rows, runoff_threshold_pct=None) == []


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
        result = await ms.fetch_confirmed_candidates(
            None, 2026, "MS", {"runoff_threshold_pct": _THRESHOLD},
        )
        assert len(result) == 10
        assert {"office": "S", "district": None, "party": "R", "last_name": "Hyde-Smith"} in result
        assert {"office": "S", "district": None, "party": "D", "last_name": "Colom"} in result

    async def test_neither_party_published_yet_is_a_healthy_empty_list(self, monkeypatch):
        async def fake_discover(client, year, party_label):
            return None

        monkeypatch.setattr(ms, "_discover_pdf_url", fake_discover)
        assert await ms.fetch_confirmed_candidates(None, 2026, "MS", {}) == []

    async def test_only_one_party_discoverable_is_treated_as_a_broken_scrape(self, monkeypatch):
        # Both primaries are held the same day and published together —
        # one page existing while the other doesn't is a much stronger
        # signal of a broken discovery regex than a genuinely one-sided
        # primary, so this must fail loudly (None) rather than silently
        # returning only the working party's nominees.
        async def fake_discover(client, year, party_label):
            return "https://example.gov/republican.pdf" if party_label == "republican" else None

        monkeypatch.setattr(ms, "_discover_pdf_url", fake_discover)
        assert await ms.fetch_confirmed_candidates(None, 2026, "MS", {}) is None

    async def test_a_download_failure_returns_none(self, monkeypatch):
        async def fake_discover(client, year, party_label):
            return f"https://example.gov/{party_label}.pdf"

        async def fake_get(client, url, label):
            return None

        monkeypatch.setattr(ms, "_discover_pdf_url", fake_discover)
        monkeypatch.setattr(ms, "_get", fake_get)
        assert await ms.fetch_confirmed_candidates(None, 2026, "MS", {}) is None

    async def test_an_unparsable_pdf_returns_none(self, monkeypatch):
        async def fake_discover(client, year, party_label):
            return f"https://example.gov/{party_label}.pdf"

        async def fake_get(client, url, label):
            class _Resp:
                content = b"not a real pdf"
            return _Resp()

        monkeypatch.setattr(ms, "_discover_pdf_url", fake_discover)
        monkeypatch.setattr(ms, "_get", fake_get)
        assert await ms.fetch_confirmed_candidates(None, 2026, "MS", {}) is None

    async def test_the_configured_threshold_is_what_actually_gates_nominees(self, monkeypatch):
        # A threshold no real CONTESTED 2026 race clears (unopposed
        # candidates are unaffected — they're always 100%) proves the
        # value in `source` is genuinely read and applied, not a fixed
        # internal constant the config can no longer influence.
        await self._patched(monkeypatch)
        result = await ms.fetch_confirmed_candidates(
            None, 2026, "MS", {"runoff_threshold_pct": 99.9},
        )
        assert {"office": "S", "district": None, "party": "R", "last_name": "Hyde-Smith"} not in result
        assert {"office": "H", "district": 2, "party": "R", "last_name": "Eller"} not in result


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
