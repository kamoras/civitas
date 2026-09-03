"""Tests for New Jersey's confirmed-general-candidate strategy
(state_candidates_nj.py).

fixtures_nj_certification_words.json is REAL — page.extract_words()
output (text/x0/top only) from the real "Certification of General
Election Nominees" PDF for the November 3, 2026 general election,
fetched live 2026-09-03 from nj.gov. Five real pages: the U.S. Senate
section (one real candidate per page — Booker on page 1, Murphy on
page 2, confirming office/district must carry across pages even within
Senate), District 1 (2 candidates), District 3 (includes a real Green
Party candidate), and District 8 (includes 4 real candidates whose
party labels — "INDEPENDENT", "SOCIALIST WORKERS PARTY", "WE THE
PEOPLE" — this module must correctly refuse to guess at, alongside the
one real Democratic nominee it must still catch).
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_nj as nj

FIXTURE = json.loads((Path(__file__).parent / "fixtures_nj_certification_words.json").read_text())


class TestParsePage:
    def test_senate_spans_two_pages_one_candidate_each(self):
        results1, office, district = nj._parse_page(FIXTURE["senate_page1"], None, None)
        assert office == "S"
        assert district is None
        assert results1 == [{"office": "S", "district": None, "party": "D", "last_name": "BOOKER"}]

        # Office/district must carry into the next page even though it's
        # still the Senate section (its own "Candidates for US Senate"
        # header repeats, but there's no district heading to re-set).
        results2, office2, district2 = nj._parse_page(FIXTURE["senate_page2"], office, district)
        assert office2 == "S"
        assert results2 == [{"office": "S", "district": None, "party": "R", "last_name": "MURPHY"}]

    def test_district_1_two_candidates(self):
        results, office, district = nj._parse_page(FIXTURE["district1"], None, None)
        assert office == "H"
        assert district == 1
        assert {"office": "H", "district": 1, "party": "D", "last_name": "NORCROSS"} in results
        assert {"office": "H", "district": 1, "party": "R", "last_name": "GALDO"} in results
        assert len(results) == 2

    def test_district_3_includes_a_real_minor_party_candidate(self):
        results, office, district = nj._parse_page(FIXTURE["district3"], None, None)
        assert district == 3
        parties = {r["last_name"]: r["party"] for r in results}
        assert parties["CONAWAY"] == "D"
        assert parties["MCGUIRE"] == "R"
        assert parties["WELZER"] == "G"

    def test_district_8_skips_unrecognized_party_labels_without_guessing(self):
        # Real field: Menendez (Democratic), plus three real candidates
        # running as "INDEPENDENT", "SOCIALIST WORKERS PARTY", and "WE
        # THE PEOPLE" — none of those normalize to a known party, and
        # this module must refuse them rather than misclassify or
        # fabricate a party for a real person.
        results, office, district = nj._parse_page(FIXTURE["district8"], None, None)
        assert district == 8
        assert results == [{"office": "H", "district": 8, "party": "D", "last_name": "MENENDEZ"}]

    def test_office_and_district_carry_forward_across_pages(self):
        # A district's candidate list can spill onto the next page (the
        # real document does this for the 7th district) — office/
        # district must persist even when a later page has no new
        # section header or district-heading row of its own.
        _, office, district = nj._parse_page(FIXTURE["district3"], None, None)
        results2, office2, district2 = nj._parse_page(
            [w for w in FIXTURE["district3"] if w["text"] not in ("Third", "Congressional", "District:")],
            office, district,
        )
        assert office2 == "H"
        assert district2 == 3
        assert len(results2) == 3  # same 3 real candidates, no heading row to re-consume


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake_fetch_with_retry(*a, **kw):
            return None

        monkeypatch.setattr(nj, "fetch_with_retry", fake_fetch_with_retry)

        assert await nj.fetch_confirmed_candidates(None, 2026, "NJ", {}) is None

    async def test_unparseable_pdf_bytes_return_none(self, monkeypatch):
        from types import SimpleNamespace

        async def fake_fetch_with_retry(*a, **kw):
            return SimpleNamespace(content=b"not a real pdf")

        monkeypatch.setattr(nj, "fetch_with_retry", fake_fetch_with_retry)

        assert await nj.fetch_confirmed_candidates(None, 2026, "NJ", {}) is None
