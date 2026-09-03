"""Tests for Kentucky's confirmed-general-candidate strategy
(state_candidates_ky.py).

fixtures_ky_certification_words.json is REAL — page.extract_words()
output (text/x0/x1/top only) from the real 2026 "Primary Certification
of Vote Totals" PDF, fetched live 2026-09-03 from elect.ky.gov. Five
real pages, each the FINAL page (the one carrying "Total Votes") of its
contest: the US Senate Republican primary (11 candidates, rotated
column headers — Andy Barr's real 2026 field), the US Senate Democratic
primary (a real case where the printed header names outnumber the
counted vote columns 8 to 7 — one candidate drew zero tallied votes and
this module must refuse to guess which), the 1st Congressional
District Republican primary (upright headers, few candidates), the 2nd
Congressional District Republican primary (includes a real hyphenated
surname, PERRY-ADELMANN), and the 6th Congressional District
Democratic primary (a real 7-candidate field that exercises the
word-center column-matching this module relies on).
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_ky as ky

FIXTURE = json.loads((Path(__file__).parent / "fixtures_ky_certification_words.json").read_text())


class TestTitleOnPage:
    def test_senate_republican_title(self):
        text = "United States Senator\nRepublican Party"
        assert ky._title_on_page(text) == ("S", None, "R")

    def test_house_district_democratic_title_short_form(self):
        text = "US Representative\n2nd Congressional District\nDemocratic Party"
        assert ky._title_on_page(text) == ("H", 2, "D")

    def test_house_district_title_long_form(self):
        text = "United States Representative in Congress\n1st Congressional District\nRepublican Party"
        assert ky._title_on_page(text) == ("H", 1, "R")

    def test_section_divider_has_no_party_and_is_not_a_title(self):
        # "For the office of United States Senator" — a real section
        # divider page in this document, carrying the office but no
        # party, must not be mistaken for a real contest's title.
        text = "Official 2026 Primary Election Results\nFor the office of\nUnited States Senator"
        assert ky._title_on_page(text) is None

    def test_state_senator_is_not_united_states_senator(self):
        text = "State Senator\nDemocratic Party"
        assert ky._title_on_page(text) is None


class TestParseTotalPage:
    def test_senate_republican_rotated_header_11_candidates(self):
        result = ky._parse_total_page(FIXTURE["senate_gop_total"], "S", None, "R")
        assert result == [{"office": "S", "district": None, "party": "R", "last_name": "BARR"}]

    def test_senate_democratic_refuses_ambiguous_field(self):
        # Real field: 8 printed candidate names, only 7 counted vote
        # columns. Nothing in the document says which name to drop, so
        # this contest must be skipped entirely rather than guess.
        result = ky._parse_total_page(FIXTURE["senate_dem_total"], "S", None, "D")
        assert result == []

    def test_district1_upright_header_few_candidates(self):
        result = ky._parse_total_page(FIXTURE["district1_total"], "H", 1, "R")
        assert result == [{"office": "H", "district": 1, "party": "R", "last_name": "COMER"}]

    def test_district2_hyphenated_surname_matches_correct_column(self):
        # GUTHRIE won this real primary; PERRY-ADELMANN (a long,
        # hyphenated name) is the real 3rd-place finisher whose left
        # edge sits closer to the WRONG column's x0 than to its own —
        # only word-center matching gets this right.
        result = ky._parse_total_page(FIXTURE["district2_gop_total"], "H", 2, "R")
        assert result == [{"office": "H", "district": 2, "party": "R", "last_name": "GUTHRIE"}]

    def test_district6_democratic_seven_candidate_field(self):
        result = ky._parse_total_page(FIXTURE["district6_dem_total"], "H", 6, "D")
        assert result == [{"office": "H", "district": 6, "party": "D", "last_name": "DEMBO"}]

    def test_no_total_row_returns_empty(self):
        words = [w for w in FIXTURE["district1_total"] if w["text"] not in ("Total", "Votes")]
        assert ky._parse_total_page(words, "H", 1, "R") == []


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    async def test_discovery_finds_nothing_returns_none(self, monkeypatch):
        async def fake_discover_urls(*a, **kw):
            return []

        monkeypatch.setattr(ky, "_discover_urls", fake_discover_urls)
        assert await ky.fetch_confirmed_candidates(None, 2026, "KY", {}) is None

    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake_discover_urls(*a, **kw):
            return [{"url": "https://elect.ky.gov/fake.pdf"}]

        async def fake_fetch_with_retry(*a, **kw):
            return None

        monkeypatch.setattr(ky, "_discover_urls", fake_discover_urls)
        monkeypatch.setattr(ky, "fetch_with_retry", fake_fetch_with_retry)
        assert await ky.fetch_confirmed_candidates(None, 2026, "KY", {}) is None

    async def test_unparseable_pdf_bytes_return_none(self, monkeypatch):
        from types import SimpleNamespace

        async def fake_discover_urls(*a, **kw):
            return [{"url": "https://elect.ky.gov/fake.pdf"}]

        async def fake_fetch_with_retry(*a, **kw):
            return SimpleNamespace(content=b"not a real pdf")

        monkeypatch.setattr(ky, "_discover_urls", fake_discover_urls)
        monkeypatch.setattr(ky, "fetch_with_retry", fake_fetch_with_retry)
        assert await ky.fetch_confirmed_candidates(None, 2026, "KY", {}) is None
