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
PROPOSITION_A = (Path(__file__).parent / "fixtures_mo_proposition_a_2026.html").read_text()


class TestFetchMeasuresParsing:
    """Exercises the real fixture through the full parse path (general-
    section extraction -> per-measure split -> per-measure parse),
    without the network layer — matches the shape TestFetchMeasures
    exercises end to end with a mocked fetch."""

    def _measures(self):
        tree = lxml_html.fromstring(FIXTURE_HTML)
        by_number = mo.split_measures(mo._general_section_elements(tree))
        return {n: mo._parse_measure(n, els, kind) for n, (kind, els) in by_number.items()}

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
        assert three["yes_means"].startswith("A “yes” vote will repeal Article I, Section 36")
        assert three["no_means"].startswith("A “no” vote will leave Article I, Section 36")
        assert three["fiscal_impact"].startswith("State governmental entities estimate")
        # The fiscal sentence must not leak into the summary, and vice versa.
        assert "estimate no costs" not in three["official_summary"]
        assert "Repeal the 2024" not in three["fiscal_impact"]
        # The fair ballot language's closing tax statement (RSMo 116.025)
        # is about the measure, not a NO vote: it was being glued onto
        # no_means. It is kept, verbatim, with the fiscal statement.
        assert "If passed" not in three["no_means"]
        assert three["no_means"].endswith("will not prohibit gender transition procedures for minors.")
        assert three["fiscal_impact"].endswith(
            "estimate no costs or savings.\n\nIf passed, this measure will not increase or decrease taxes."
        )
        # Drafters per RSMo chapter 116: a General Assembly referral's
        # summary and fiscal note summary are the Assembly's or, failing
        # that, the Secretary's and the Auditor's; the page doesn't say
        # which, so neither is credited to the Secretary alone.
        assert three["title_authority"] == mo._REFERRAL_TITLE_AUTHORITY
        assert three["fiscal_authority"].startswith(mo._REFERRAL_FISCAL_AUTHORITY)
        assert three["fiscal_authority"].endswith("Missouri Secretary of State (tax statement, RSMo 116.025)")
        assert three["framing_authority"] == mo.FRAMING_AUTHORITY

    def test_single_paragraph_measure_still_splits_summary_from_fiscal(self):
        # Amendment 7's "Official Ballot Title" blockquote is ONE
        # paragraph followed by ONE fiscal paragraph (no bulleted list
        # ahead of it, unlike Amendment 3) — a different shape the
        # last-child split must still handle correctly.
        seven = self._measures()["7"]
        assert seven["fiscal_impact"].startswith("State and local governmental entities estimate no costs or savings.\n\n")
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


def _serve(monkeypatch, body):
    async def fake_get_text(client, rl, url, label, **kw):
        assert url == mo.URL_PATTERN.format(year=2026)
        return body

    monkeypatch.setattr(mo, "fetch_text_with_retry", fake_get_text)


def _general_then_primary():
    general = FIXTURE_HTML.index("<h2")
    return general, FIXTURE_HTML.index("<h2", general + 1)


@pytest.mark.asyncio
class TestFetchMeasures:
    async def test_real_shaped_flow_returns_all_three(self, monkeypatch):
        _serve(monkeypatch, FIXTURE_HTML)

        results = await mo.fetch_measures(None, 2026)

        assert results is not None
        assert [parsed["number"] for parsed, _url in results] == ["3", "7", "8"]
        assert all(url == mo.URL_PATTERN.format(year=2026) for _parsed, url in results)

    @pytest.mark.parametrize("body", [
        pytest.param(None, id="fetch_failure"),
        # lxml.html.fromstring raises ParserError on a genuinely empty
        # document (it's otherwise extremely forgiving of "weird" HTML,
        # so this is the realistic way the parse step itself fails).
        pytest.param("", id="empty_response_body"),
        # The regression: a page with no general-election heading — a
        # restyled page, an error page with an <h2> — returned [], i.e.
        # confirmed none.
        pytest.param("<html><body><h2>Nothing relevant this cycle</h2></body></html>", id="unknown_page"),
        # A measure heading of a kind this reader doesn't know refuses the
        # page; an unparseable measure used to be skipped.
        pytest.param(
            FIXTURE_HTML.replace("</strong>Amendment 7</p>", "</strong>Question Seven</p>", 1),
            id="measure_heading_it_cannot_number",
        ),
        pytest.param(
            FIXTURE_HTML.replace("Official Ballot Title:", "Ballot Title Text:", 1),
            id="unparseable_measure_refuses_instead_of_being_skipped",
        ),
    ])
    async def test_a_page_it_cannot_read_whole_is_a_failure(self, monkeypatch, body):
        assert body != FIXTURE_HTML
        _serve(monkeypatch, body)
        assert await mo.fetch_measures(None, 2026) is None

    async def test_only_the_primary_section_so_far_is_not_yet_published(self, monkeypatch):
        from app.pipeline.fetch.ballot_measure_text import NotYetPublished

        # The same page before anything was certified to November: only
        # the primary's section.
        general, primary = _general_then_primary()
        _serve(monkeypatch, FIXTURE_HTML[:general] + FIXTURE_HTML[primary:])
        with pytest.raises(NotYetPublished):
            await mo.fetch_measures(None, 2026)

    async def test_general_section_with_no_measure_is_confirmed_none(self, monkeypatch):
        _serve(monkeypatch, (
            "<html><body><h2>The following ballot measures will appear on the "
            "November 3, 2026 General Election ballot</h2><p>None.</p></body></html>"
        ))
        assert await mo.fetch_measures(None, 2026) == []

    async def test_a_proposition_is_read_not_dropped(self, monkeypatch):
        """The regression, live: the 2026 general section carries
        Proposition A — the referendum petition on the General Assembly's
        congressional map — and the Amendment-only heading pattern skipped
        it without a trace, publishing Missouri's ballot one measure short."""
        _, primary = _general_then_primary()
        _serve(monkeypatch, FIXTURE_HTML[:primary] + PROPOSITION_A + FIXTURE_HTML[primary:])
        results = await mo.fetch_measures(None, 2026)
        assert [p["number"] for p, _ in results] == ["3", "7", "8", "A"]
        prop = results[-1][0]
        assert prop["title"] == "Proposition A"
        assert prop["origin"] == "Missouri voters (referendum petition)"
        assert prop["official_summary"].startswith("Do the people of the state of Missouri approve the act")
        # A veto referendum: the state's own framing says what YES does.
        assert prop["yes_means"].startswith("A “yes” vote will approve the act of the General Assembly")
        assert prop["fiscal_impact"].startswith("State and local governmental entities estimate no costs or savings.")
        # A petition's summary is the Secretary's, its fiscal note summary
        # the Auditor's.
        assert prop["title_authority"] == mo._PETITION_TITLE_AUTHORITY
        assert prop["fiscal_authority"].startswith(mo._PETITION_FISCAL_AUTHORITY)
