"""Tests for Missouri's ballot-measure strategy (ballot_measures_mo.py).

fixtures_mo_ballot_measures.html is REAL — a trimmed copy of the live
sos.mo.gov/petitions/2026ballotmeasures page, fetched 2026-09-03. It
keeps the real "general election" and "primary election" <h2> headings
and everything between/after them, including the real site
inconsistency this module is built to survive: Amendment 8's own
heading paragraph sits as the LAST child of Amendment 7's <div>, not
the first child of its own.
"""

from pathlib import Path

import pytest
from lxml import html as lxml_html

from app.pipeline.fetch import ballot_measures_mo as mo

FIXTURE_HTML = (Path(__file__).parent / "fixtures_mo_ballot_measures.html").read_text()


class TestFetchMeasuresParsing:
    """Exercises the real fixture through the full parse path (general-
    section extraction -> per-measure split -> per-measure parse),
    without the network layer — matches the shape TestFetchMeasures
    exercises end to end with a mocked fetch."""

    def _measures(self):
        tree = lxml_html.fromstring(FIXTURE_HTML)
        by_number = mo._split_by_measure(mo._general_section_elements(tree))
        return {n: mo._parse_measure(n, els) for n, els in by_number.items()}

    def test_finds_exactly_the_three_real_november_measures(self):
        measures = self._measures()
        assert sorted(measures.keys(), key=int) == ["3", "7", "8"]

    def test_amendment_8_parses_despite_sitting_in_amendment_7_s_div(self):
        # The real site inconsistency this module exists to survive.
        measures = self._measures()
        eight = measures["8"]
        assert eight is not None
        assert "sheriff" in eight["official_summary"].lower()

    def test_real_yes_no_framing_and_fiscal_impact(self):
        measures = self._measures()
        three = measures["3"]
        assert three["origin"] == "Missouri General Assembly"
        assert three["yes_means"].startswith("repeal Article I, Section 36")
        assert three["no_means"].startswith("leave Article I, Section 36")
        assert three["fiscal_impact"].startswith("State governmental entities estimate")
        # The fiscal sentence must not leak into the summary, and vice versa.
        assert "estimate no costs" not in three["official_summary"]
        assert "Repeal the 2024" not in three["fiscal_impact"]

    def test_single_paragraph_measure_still_splits_summary_from_fiscal(self):
        # Amendment 7's "Official Ballot Title" blockquote is ONE
        # paragraph followed by ONE fiscal paragraph (no bulleted list
        # ahead of it, unlike Amendment 3) — a different shape the
        # last-child split must still handle correctly.
        seven = self._measures()["7"]
        assert seven["fiscal_impact"] == "State and local governmental entities estimate no costs or savings."
        assert "permanent public endowment fund" in seven["official_summary"]
        assert "estimate no costs" not in seven["official_summary"]
        # The name "Show-Me Prosperity Fund" only appears in the Fair
        # Ballot Language section, not the Official Ballot Title itself.
        assert "Show-Me Prosperity Fund" in seven["yes_means"]


class TestGeneralSectionElements:
    def test_stops_before_the_primary_election_heading(self):
        tree = lxml_html.fromstring(FIXTURE_HTML)
        elements = mo._general_section_elements(tree)
        joined = " ".join(e.text_content() for e in elements)
        assert "Amendment 1" not in joined  # primary-only measure

    def test_no_general_election_heading_returns_empty(self):
        tree = lxml_html.fromstring("<div><h2>Nothing relevant here</h2><p>x</p></div>")
        assert mo._general_section_elements(tree) == []


class TestOriginFor:
    def test_general_assembly_referral(self):
        assert mo._origin_for("Proposed by 103rd General Assembly (First Regular Session) HCS HJR 73") == (
            "Missouri General Assembly"
        )

    def test_citizen_initiative(self):
        assert mo._origin_for("Proposed by initiative petition") == "Missouri voters (initiative petition)"

    def test_unrecognized_text_returns_none(self):
        assert mo._origin_for("Some future phrasing nobody has seen yet") is None


@pytest.mark.asyncio
class TestFetchMeasures:
    async def test_real_shaped_flow_returns_all_three(self, monkeypatch):
        async def fake_get_text(client, url, label):
            assert url == mo.URL_PATTERN.format(year=2026)
            return FIXTURE_HTML

        monkeypatch.setattr(mo, "_get_text", fake_get_text)

        results = await mo.fetch_measures(None, 2026)

        assert results is not None
        assert [parsed["number"] for parsed, _url in results] == ["3", "7", "8"]
        assert all(url == mo.URL_PATTERN.format(year=2026) for _parsed, url in results)

    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake_get_text(client, url, label):
            return None

        monkeypatch.setattr(mo, "_get_text", fake_get_text)

        assert await mo.fetch_measures(None, 2026) is None

    async def test_empty_response_body_returns_none(self, monkeypatch):
        # lxml.html.fromstring raises ParserError on a genuinely empty
        # document (it's otherwise extremely forgiving of "weird" HTML,
        # so this is the realistic way the parse step itself fails).
        async def fake_get_text(client, url, label):
            return ""

        monkeypatch.setattr(mo, "_get_text", fake_get_text)

        assert await mo.fetch_measures(None, 2026) is None

    async def test_no_general_election_heading_returns_empty(self, monkeypatch):
        async def fake_get_text(client, url, label):
            return "<html><body><h2>Nothing relevant this cycle</h2></body></html>"

        monkeypatch.setattr(mo, "_get_text", fake_get_text)

        assert await mo.fetch_measures(None, 2026) == []
