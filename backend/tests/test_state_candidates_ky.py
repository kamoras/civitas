"""Tests for Kentucky's confirmed-general-candidate strategy
(state_candidates_ky.py).

fixtures_ky_certification_words.json is REAL — page.extract_words()
output (text/x0/x1/top only) from the real 2026 "Primary Certification
of Vote Totals" PDF, fetched live 2026-09-03 from elect.ky.gov. Five
real pages, each the FINAL page (the one carrying "Total Votes") of its
contest: the US Senate Republican primary (11 candidates, rotated
column headers — Andy Barr's real 2026 field), the US Senate Democratic
primary (7 candidates, including a real Mc-surname — Amy McGrath,
printed "McGRATH" even in this document's all-caps header style, not
"MCGRATH" — this module must match it as a surname rather than treat
the lowercase "c" as disqualifying), the 1st Congressional District
Republican primary (upright headers, few candidates), the 2nd
Congressional District Republican primary (includes a real hyphenated
surname, PERRY-ADELMANN), and the 6th Congressional District
Democratic primary (a real 7-candidate field that exercises the
word-center column-matching this module relies on).

The "refuse to guess" safety branch (a column whose header doesn't
resolve to exactly one surname) is NOT exercised by any page in the
real 2026 document — every real contest resolves cleanly — so it is
tested below with a small CONSTRUCTED word list instead of a fixture.
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

    def test_senate_democratic_seven_candidate_field_with_a_mc_surname(self):
        # Real 2026 field: Cory Booker won; Amy McGrath ("McGRATH" in
        # this document's own header style) is a real, unambiguous
        # runner-up — confirms Mc/Mac surnames are matched correctly.
        result = ky._parse_total_page(FIXTURE["senate_dem_total"], "S", None, "D")
        assert result == [{"office": "S", "district": None, "party": "D", "last_name": "BOOKER"}]

    def test_ambiguous_column_refuses_the_whole_contest(self):
        # Constructed, not a fixture: column 1 resolves cleanly to
        # "SMITH"; column 2's header carries TWO all-caps words
        # ("JONES" and "DOE") with nothing in the data to say which is
        # the real surname, so the whole contest must be refused.
        words = [
            {"text": "Total", "x0": 10.0, "x1": 30.0, "top": 300.0},
            {"text": "Votes", "x0": 35.0, "x1": 55.0, "top": 300.0},
            {"text": "100", "x0": 100.0, "x1": 120.0, "top": 300.0},
            {"text": "50", "x0": 200.0, "x1": 215.0, "top": 300.0},
            {"text": "SMITH", "x0": 95.0, "x1": 125.0, "top": 150.0},
            {"text": "JONES", "x0": 195.0, "x1": 220.0, "top": 150.0},
            {"text": "DOE", "x0": 195.0, "x1": 220.0, "top": 160.0},
        ]
        assert ky._parse_total_page(words, "H", 1, "R") == []

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
