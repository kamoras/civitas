"""Tests for Maine's ballot-measure strategy (ballot_measures_me.py).

fixtures_me_citizens_guide.json is REAL — page.extract_text() output of
the Secretary of State's "Maine Citizen's Guide to the Referendum
Election", fetched live 2026-09-28 from maine.gov:

- "2024": every page of the November 5, 2024 guide (a citizen
  initiative, three bonds with Treasurer debt-service sections, and a
  legislative referendum; bare-number page footers).
- "2025": the November 4, 2025 guide trimmed to its cover/letter/listing
  pages, each question's heading page, and each question's Intent and
  Content / Fiscal Impact / Public Comments pages ("Page N" footers; the
  bill-text pages in between are dropped).
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_me as me
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

FIXTURE = json.loads((Path(__file__).parent / "fixtures_me_citizens_guide.json").read_text())
GUIDE_2024 = "\n".join(FIXTURE["2024"])
GUIDE_2025 = "\n".join(FIXTURE["2025"])


class TestParseGuide:
    def test_2024_all_five_questions_match_the_guides_own_listing(self):
        parsed = me.parse_guide(GUIDE_2024)
        assert [p["number"] for p in parsed] == me.listed_numbers(GUIDE_2024) == ["1", "2", "3", "4", "5"]

    def test_2025_both_questions_match_the_listing(self):
        parsed = me.parse_guide(GUIDE_2025)
        assert [p["number"] for p in parsed] == me.listed_numbers(GUIDE_2025) == ["1", "2"]

    def test_yes_no_sentences_are_the_attorney_generals_own_words(self):
        by_number = {p["number"]: p for p in me.parse_guide(GUIDE_2024)}
        assert by_number["1"]["yes_means"] == "A “YES” vote is to enact the initiated legislation."
        assert by_number["1"]["no_means"] == "A “NO” vote opposes the initiated legislation."
        assert by_number["2"]["yes_means"] == (
            "A “YES” vote approves the issuance of up to $25 million in general "
            "obligation bonds to support technological innovation."
        )
        assert by_number["5"]["yes_means"] == "A “YES” vote is to change the state flag to the Pine Tree Flag."
        assert by_number["5"]["no_means"] == "A “NO” vote opposes changing the state flag."

    def test_bond_fiscal_text_keeps_the_debt_service_it_refers_to(self):
        two = {p["number"]: p for p in me.parse_guide(GUIDE_2024)}["2"]
        assert two["fiscal_impact"].startswith("Total estimated life time cost is $31,875,000")
        assert two["fiscal_impact"].endswith(
            "no significant fiscal impact other than the debt service costs identified above."
        )
        assert two["fiscal_authority"] == (
            "Maine Office of the Treasurer and Maine Office of Fiscal and Program Review"
        )
        assert two["origin"] == "Maine Legislature"

    def test_initiative_origin_and_fiscal_authority(self):
        one = me.parse_guide(GUIDE_2025)[0]
        assert one["origin"] == "Maine voters (citizen initiative)"
        assert one["fiscal_authority"] == "Maine Office of Fiscal and Program Review"
        assert one["fiscal_impact"].startswith("This citizen initiative requires the presentation")

    def test_sections_do_not_bleed_into_each_other(self):
        for guide in (GUIDE_2024, GUIDE_2025):
            for p in me.parse_guide(guide):
                s = p["official_summary"]
                assert "A “YES” vote" not in s and "Fiscal Impact Statement" not in s
                assert "Prepared by" not in s and not s.startswith("Question")
                assert "Public Comment" not in (p["fiscal_impact"] or "")
                assert "Page " not in p["fiscal_impact"]

    def test_2025_summary_starts_after_the_repeated_heading(self):
        two = me.parse_guide(GUIDE_2025)[1]
        assert two["official_summary"].startswith(
            "This citizen-initiated bill is intended to establish a process"
        )


class TestDiscovery:
    def test_guide_link_needs_year_and_guide(self):
        html = (
            '<a href="/sos/sites/maine.gov.sos/files/inline-files/MaineCitizensGuide2025.pdf">'
            "2025 Maine Citizen's Guide to the Referendum Election</a>"
            '<a href="/x/MaineCitizensGuide2024.pdf">old</a>'
        )
        assert me.guide_links(html, "https://www.maine.gov/sos/news/x", 2025) == [
            "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/MaineCitizensGuide2025.pdf"
        ]
        assert me.guide_links(html, "https://www.maine.gov/sos/news/x", 2026) == []

    def test_release_link_by_slug(self):
        html = '<a href="/sos/news/citizens-guide-2025-maine-referendum-election-available">x</a>'
        assert me.release_links(html, "https://www.maine.gov/sos/about-us/news", 2025) == [
            "https://www.maine.gov/sos/news/citizens-guide-2025-maine-referendum-election-available"
        ]


class TestFetchMeasures:
    async def test_guide_not_published_is_not_yet_published_never_empty(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            return "<a href='/sos/news/other'>other</a><time datetime=\"2026-06-25T12:00:00Z\">Jun 25</time>"
        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        with pytest.raises(NotYetPublished) as raised:
            await me.fetch_measures(None, 2026)
        # A guide printed only when there are questions: its absence can be
        # the real answer, so no expected-by cutoff applies.
        assert raised.value.deadline_applies is False

    async def test_news_pages_that_dont_reach_back_far_enough_are_not_a_complete_search(self, monkeypatch):
        """Three news pages is a page budget, not a date range: if they all
        fall after the earliest the guide is ever announced, its release
        could be on page 4 — that is a blind spot, not "not published"."""
        async def fake_text(client, limiter, url, label, **kw):
            return "<a href='/sos/news/other'>other</a><time datetime=\"2026-09-25T12:00:00Z\">Sep 25</time>"
        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        assert await me.fetch_measures(None, 2026) is None

    async def test_full_flow_with_the_real_2024_guide(self, monkeypatch):
        guide = "https://www.maine.gov/sos/x/Citizens-20Guide-2011.5.2024-20FINAL.pdf"

        async def fake_text(client, limiter, url, label, **kw):
            return f'<a href="{guide}">2024 Citizens Guide</a>'

        async def fake_bytes(client, limiter, url, label, **kw):
            return b"pdf"
        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(me, "fetch_bytes_with_retry", fake_bytes)
        monkeypatch.setattr(me, "_extract_text", lambda raw: GUIDE_2024)
        result = await me.fetch_measures(None, 2024)
        assert [p["number"] for p, _ in result] == ["1", "2", "3", "4", "5"]
        assert all(url == guide for _, url in result)

    async def test_a_guide_for_another_year_is_refused(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            return '<a href="/x/MaineCitizensGuide2026.pdf">2026 Citizen Guide</a>'

        async def fake_bytes(client, limiter, url, label, **kw):
            return b"pdf"
        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(me, "fetch_bytes_with_retry", fake_bytes)
        monkeypatch.setattr(me, "_extract_text", lambda raw: GUIDE_2024)
        assert await me.fetch_measures(None, 2026) is None

    async def test_a_parse_short_of_the_listing_is_none(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            return '<a href="/x/MaineCitizensGuide2024.pdf">2024 Citizen Guide</a>'

        async def fake_bytes(client, limiter, url, label, **kw):
            return b"pdf"
        # Drop question 3's Intent section: the listing still names 5.
        broken = GUIDE_2024.replace("A “YES” vote approves the issuance of up to $10 million", "X")
        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(me, "fetch_bytes_with_retry", fake_bytes)
        monkeypatch.setattr(me, "_extract_text", lambda raw: broken)
        assert await me.fetch_measures(None, 2024) is None


class TestBallotQuestionText:
    def test_official_title_is_the_listed_ballot_question_not_a_label(self):
        """The regression: "Question 1: Citizen's Initiative" — a label
        this reader composed — was rendered as the OFFICIAL BALLOT TITLE
        "Drafted by Maine Secretary of State"."""
        by_number = {p["number"]: p for p in me.parse_guide(GUIDE_2024)}
        assert by_number["1"]["title"] == "Question 1: Citizen’s Initiative"
        assert by_number["1"]["official_title"] == (
            "Do you want to set a $5,000 limit for giving to political action committees that spend money "
            "independently to support or defeat candidates for office?"
        )
        assert by_number["5"]["official_title"].startswith("Do you favor making the former state flag")
        assert by_number["5"]["official_title"].endswith("the official flag of the State?")
        for p in by_number.values():
            assert p["official_title"].endswith("?")
            assert "Treasurer" not in p["official_title"]

    def test_the_question_names_its_real_drafter(self):
        by_number = {p["number"]: p for p in me.parse_guide(GUIDE_2024)}
        assert by_number["1"]["title_authority"] == "Maine Secretary of State"
        # A bond question is written into the Legislature's own act.
        assert by_number["2"]["title_authority"] == "Maine Legislature"
        assert me.parse_guide(GUIDE_2025)[1]["official_title"].startswith("Do you want to allow courts")


class TestNotYetPublishedVsOutage:
    async def test_a_page_that_could_not_be_fetched_is_a_failure_not_unpublished(self, monkeypatch):
        """The regression: _find_guide returned None both when the guide
        wasn't linked AND when page fetches failed, so an outage read as
        "not yet published" — silenced, no alert."""
        async def fake_text(client, limiter, url, label, **kw):
            return None if "news" in url else "<a href='/sos/other'>other</a>"
        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        assert await me.fetch_measures(None, 2026) is None

    async def test_a_release_page_that_could_not_be_fetched_is_a_failure(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            if "citizens-guide-2026" in url:
                return None
            return '<a href="/sos/news/citizens-guide-2026-maine-referendum-election">x</a>'
        monkeypatch.setattr(me, "fetch_text_with_retry", fake_text)
        assert await me.fetch_measures(None, 2026) is None
