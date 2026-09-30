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
    @pytest.mark.parametrize("text,expected", [
        pytest.param("United States Senator\nRepublican Party", ("S", None, "R"),
                     id="senate_republican"),
        pytest.param("US Representative\n2nd Congressional District\nDemocratic Party",
                     ("H", 2, "D"), id="house_short_form"),
        pytest.param("United States Representative in Congress\n1st Congressional District\nRepublican Party",
                     ("H", 1, "R"), id="house_long_form"),
        # "For the office of United States Senator" -- a real section
        # divider page in this document, carrying the office but no
        # party, must not be mistaken for a real contest's title.
        pytest.param("Official 2026 Primary Election Results\nFor the office of\nUnited States Senator",
                     None, id="section_divider_has_no_party"),
        pytest.param("State Senator\nDemocratic Party", None,
                     id="state_senator_is_not_us_senator"),
    ])
    def test_title_on_page(self, text, expected):
        assert ky._title_on_page(text) == expected


class TestParseTotalPage:
    @pytest.mark.parametrize("key,office,district,party,winner", [
        pytest.param("senate_gop_total", "S", None, "R", "BARR",
                     id="senate_republican_rotated_header_11_candidates"),
        # Real 2026 field: Cory Booker won; Amy McGrath ("McGRATH" in
        # this document's own header style) is a real, unambiguous
        # runner-up -- confirms Mc/Mac surnames are matched correctly.
        pytest.param("senate_dem_total", "S", None, "D", "BOOKER",
                     id="senate_democratic_seven_candidates_with_a_mc_surname"),
        pytest.param("district1_total", "H", 1, "R", "COMER",
                     id="district1_upright_header_few_candidates"),
        # GUTHRIE won this real primary; PERRY-ADELMANN (a long,
        # hyphenated name) is the real 3rd-place finisher whose left
        # edge sits closer to the WRONG column's x0 than to its own --
        # only word-center matching gets this right.
        pytest.param("district2_gop_total", "H", 2, "R", "GUTHRIE",
                     id="district2_hyphenated_surname_matches_correct_column"),
        pytest.param("district6_dem_total", "H", 6, "D", "DEMBO",
                     id="district6_democratic_seven_candidate_field"),
    ])
    def test_real_total_page_resolves_to_the_real_winner(self, key, office, district, party, winner):
        result = ky._parse_total_page(FIXTURE[key], office, district, party)
        assert result == [{"office": office, "district": district, "party": party, "last_name": winner}]

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
