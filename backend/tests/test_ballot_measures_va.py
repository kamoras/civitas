"""Tests for Virginia's ballot-measure strategy (ballot_measures_va.py).

fixtures_va_referenda.json is REAL — extract_text() output from the
real per-question "Explanation for Proposed Constitutional Amendment"
PDFs for all 3 questions on Virginia's real November 3, 2026 ballot
(reproductive freedom, marriage, felony voting-rights restoration),
fetched live 2026-09-03 from elections.virginia.gov.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_va as va

FIXTURE = json.loads((Path(__file__).parent / "fixtures_va_referenda.json").read_text())

_INDEX_HTML = """
<a href="/election-law/proposed-constitutional-amendment-question-1/">Question 1</a>
<a href="/election-law/proposed-constitutional-amendment-question-2/">Question 2</a>
<a href="/election-law/proposed-constitutional-amendment-question-3/">Question 3</a>
<a href="/election-law/proposed-amendment-for-april-2026-special-election/">April special</a>
"""

_QUESTION_PAGE_HTML = """
<a href="/media/electionadministration/electionlaw/11-3-26-Special-Explanation-for-Q{n}-Topic.pdf">English</a>
<a href="/media/electionadministration/electionlaw/11-3-26-Special-Explanation-for-Q{n}-Topic-ES.pdf">Spanish</a>
<a href="/media/electionadministration/electionlaw/11-3-26-Special-Explanation-for-Q{n}-Topic-KO.pdf">Korean</a>
<a href="/media/electionadministration/electionlaw/11-3-26-Special-Explanation-for-Q{n}-Topic-VI.pdf">Vietnamese</a>
"""


class TestParseDocument:
    def test_real_fixture_question_1(self):
        parsed = va.parse_document(FIXTURE["1"], "1")
        assert parsed["number"] == "1"
        assert parsed["title"] == "Fundamental right to reproductive freedom"
        assert parsed["origin"] == "Virginia General Assembly"
        assert parsed["title_authority"] == "Virginia General Assembly"
        assert parsed["official_summary"].startswith(
            "Currently, the Virginia Constitution does not explicitly provide"
        )
        # Present Law flows straight into Proposed Amendment with the
        # heading label itself dropped — not fused mid-sentence, and the
        # separate BALLOT QUESTION section is never appended (that would
        # be inserting unsourced connective text — see module docstring).
        assert "Proposed Amendment" not in parsed["official_summary"]
        assert "The proposed amendment would add to the Virginia Constitution" in parsed["official_summary"]
        assert "Ballot question:" not in parsed["official_summary"]
        # No framing or fiscal data is published in this document — never
        # inferred from the question's own phrasing.
        assert parsed["yes_means"] is None
        assert parsed["no_means"] is None
        assert parsed["fiscal_impact"] is None

    def test_real_fixture_question_2_marriage(self):
        parsed = va.parse_document(FIXTURE["2"], "2")
        assert parsed["title"] == "Marriage"
        assert "same-sex marriage" in parsed["official_summary"] or "same sex" in parsed["official_summary"]

    def test_real_fixture_question_3_voting_rights(self):
        parsed = va.parse_document(FIXTURE["3"], "3")
        assert parsed["title"] == "Qualifications of voters"
        assert "felony" in parsed["official_summary"].lower()

    def test_unrecognized_shape_returns_none(self):
        assert va.parse_document("Some unrelated PDF text with no matching sections.", "9") is None

    def test_url_slug_disagreeing_with_the_pdf_s_own_heading_returns_none(self):
        # Real fixture "1"'s document says "QUESTION 1" throughout — feed
        # it in as if the index page's URL slug called it question 2.
        assert va.parse_document(FIXTURE["1"], "2") is None


class TestEnglishPdfUrl:
    def test_picks_the_link_with_no_language_suffix(self):
        html = _QUESTION_PAGE_HTML.format(n=1)
        url = va._english_pdf_url(html, "https://www.elections.virginia.gov/x")
        assert url == (
            "https://www.elections.virginia.gov/media/electionadministration/"
            "electionlaw/11-3-26-Special-Explanation-for-Q1-Topic.pdf"
        )

    def test_no_english_link_returns_none(self):
        html = """<a href="/x-ES.pdf">ES</a><a href="/x-KO.pdf">KO</a>"""
        assert va._english_pdf_url(html, "https://example.com") is None

    def test_ambiguous_multiple_unsuffixed_links_returns_none(self):
        html = """<a href="/a.pdf">A</a><a href="/b.pdf">B</a>"""
        assert va._english_pdf_url(html, "https://example.com") is None


@pytest.mark.asyncio
class TestFetchMeasures:
    async def test_real_shaped_flow_returns_all_three_questions(self, monkeypatch):
        async def fake_get_text(client, url, label):
            if url == va._INDEX_URL:
                return _INDEX_HTML
            for n in ("1", "2", "3"):
                if f"question-{n}/" in url:
                    return _QUESTION_PAGE_HTML.format(n=n)
            raise AssertionError(f"unexpected URL {url}")

        async def fake_get_bytes(client, url, label):
            for n in ("1", "2", "3"):
                if f"Q{n}-Topic.pdf" in url:
                    return f"FIXTURE:{n}".encode()
            raise AssertionError(f"unexpected URL {url}")

        monkeypatch.setattr(va, "_get_text", fake_get_text)
        monkeypatch.setattr(va, "_get_bytes", fake_get_bytes)
        # _extract_text normally opens a real PDF; here it just decodes the
        # marker fetch_bytes returned, so this test exercises discovery
        # (index -> per-question page -> English PDF) without needing a
        # real PDF round-trip — that part is covered by TestParseDocument.
        monkeypatch.setattr(va, "_extract_text", lambda raw: FIXTURE[raw.decode().split(":")[1]])

        results = await va.fetch_measures(None, 2026)

        assert results is not None
        assert [parsed["number"] for parsed, _url in results] == ["1", "2", "3"]
        assert all(url.endswith(".pdf") and "-ES" not in url for _parsed, url in results)

    async def test_index_fetch_failure_returns_none(self, monkeypatch):
        async def fake_get_text(client, url, label):
            return None

        monkeypatch.setattr(va, "_get_text", fake_get_text)

        assert await va.fetch_measures(None, 2026) is None

    async def test_no_questions_linked_returns_empty(self, monkeypatch):
        async def fake_get_text(client, url, label):
            return "<html>nothing relevant here</html>"

        monkeypatch.setattr(va, "_get_text", fake_get_text)

        assert await va.fetch_measures(None, 2026) == []

    async def test_one_question_failing_fails_the_whole_fetch(self, monkeypatch):
        # A question the index page names but that fails at a later step
        # must NOT silently produce a shorter, still-"successful" list —
        # that would get cached as if it were the complete ballot for 72h
        # with no signal anything was missing (see module docstring).
        async def fake_get_text(client, url, label):
            if url == va._INDEX_URL:
                return _INDEX_HTML
            if "question-2/" in url:
                return None  # question 2's own page fails to fetch
            for n in ("1", "3"):
                if f"question-{n}/" in url:
                    return _QUESTION_PAGE_HTML.format(n=n)
            raise AssertionError(f"unexpected URL {url}")

        async def fake_get_bytes(client, url, label):
            for n in ("1", "3"):
                if f"Q{n}-Topic.pdf" in url:
                    return f"FIXTURE:{n}".encode()
            raise AssertionError(f"unexpected URL {url}")

        monkeypatch.setattr(va, "_get_text", fake_get_text)
        monkeypatch.setattr(va, "_get_bytes", fake_get_bytes)
        monkeypatch.setattr(va, "_extract_text", lambda raw: FIXTURE[raw.decode().split(":")[1]])

        assert await va.fetch_measures(None, 2026) is None

    async def test_duplicate_index_link_keeps_the_first_and_warns(self, monkeypatch):
        duplicate_index_html = _INDEX_HTML + (
            '\n<a href="/election-law/proposed-constitutional-amendment-question-1/">'
            "Question 1 (again, different URL)</a>"
        )

        async def fake_get_text(client, url, label):
            if url == va._INDEX_URL:
                return duplicate_index_html
            for n in ("1", "2", "3"):
                if f"question-{n}/" in url:
                    return _QUESTION_PAGE_HTML.format(n=n)
            raise AssertionError(f"unexpected URL {url}")

        async def fake_get_bytes(client, url, label):
            for n in ("1", "2", "3"):
                if f"Q{n}-Topic.pdf" in url:
                    return f"FIXTURE:{n}".encode()
            raise AssertionError(f"unexpected URL {url}")

        monkeypatch.setattr(va, "_get_text", fake_get_text)
        monkeypatch.setattr(va, "_get_bytes", fake_get_bytes)
        monkeypatch.setattr(va, "_extract_text", lambda raw: FIXTURE[raw.decode().split(":")[1]])

        results = await va.fetch_measures(None, 2026)

        # Same duplicate URL both times -> not treated as a real conflict,
        # question 1 still resolves once, not twice.
        assert [parsed["number"] for parsed, _url in results] == ["1", "2", "3"]
