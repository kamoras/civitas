"""Tests for New Jersey's ballot-measure strategy (ballot_measures_nj.py).

fixtures_nj_public_questions.json is REAL, fetched live 2026-09-28 from
nj.gov/state/elections:

- page_2026: a trimmed copy of election-information-2026.shtml — the
  "Official General Election Candidates" card, including the page
  template's commented-out public-question block whose hrefs are
  re-dated to 2026 and 404.
- page_2021: a trimmed copy of the 2021 page — the two visible public
  question blocks and the candidate certification that follows them.
- question_*: the cropped text (ballot_measures_nj._extract_text) of the
  real English public-question PDFs for 2021 Q1/Q2, 2020 Q1 and 2019 Q1.
"""

import json
from pathlib import Path

from app.pipeline.fetch import ballot_measures_nj as nj

FIXTURE = json.loads((Path(__file__).parent / "fixtures_nj_public_questions.json").read_text())


class TestQuestionLinks:
    def test_2026_commented_out_template_block_is_not_a_question(self):
        # The raw page does contain the phantom link...
        assert "2026-public-question-1-english.pdf" in FIXTURE["page_2026"]
        # ...but it is inside an HTML comment, so there is no question.
        assert nj.question_links(FIXTURE["page_2026"], nj.URL_PATTERN.format(year=2026)) == {}

    def test_2021_visible_questions_are_found_in_order(self):
        links = nj.question_links(FIXTURE["page_2021"], nj.URL_PATTERN.format(year=2021))
        assert links == {
            "1": "https://www.nj.gov/state/elections/assets/pdf/election-results/2021/2021-public-question-1-english.pdf",
            "2": "https://www.nj.gov/state/elections/assets/pdf/election-results/2021/2021-public-question-2-english.pdf",
        }

    def test_page_without_the_general_certification_is_not_read_as_none(self):
        page = '<a href="x.pdf">Official Primary Election Candidates</a>'
        assert nj.question_links(page, "https://www.nj.gov/") is None

    def test_a_visible_question_reference_with_no_document_is_not_none(self):
        page = (
            '<a href="c.pdf">Official General Election Candidates: U.S. Senate</a>'
            '<a href="r.pdf">Official General Election Results: <strong>Public Question</strong></a>'
        )
        assert nj.question_links(page, "https://www.nj.gov/") is None


class TestParseDocument:
    def test_2021_question_1(self):
        parsed = nj.parse_document(FIXTURE["question_2021_1"], "1")
        assert parsed["title"] == (
            "CONSTITUTIONAL AMENDMENT TO PERMIT WAGERING ON ALL COLLEGE SPORT OR ATHLETIC EVENTS"
        )
        assert parsed["official_summary"].startswith(
            "Currently, the State Constitution prohibits wagering on college sport"
        )
        assert parsed["official_summary"].endswith("through casinos and current or former horse racetracks.")
        assert parsed["origin"] == parsed["title_authority"] == "New Jersey Legislature"

    def test_ballot_box_labels_and_question_text_stay_out_of_the_summary(self):
        for key, number in (("question_2021_1", "1"), ("question_2021_2", "2"),
                            ("question_2020_1", "1"), ("question_2019_1", "1")):
            parsed = nj.parse_document(FIXTURE[key], number)
            assert parsed is not None, key
            words = parsed["official_summary"].split()
            assert "YES" not in words and "NO" not in words
            assert "Do you approve" not in parsed["official_summary"]
            assert parsed["yes_means"] is None and parsed["no_means"] is None
            assert parsed["fiscal_impact"] is None

    def test_zero_padded_2020_heading_matches(self):
        # The real 2020 file is named ...-question-01-...; its heading says "NO. 1".
        assert nj.parse_document(FIXTURE["question_2020_1"], "1")["title"] == (
            "CONSTITUTIONAL AMENDMENT TO LEGALIZE MARIJUANA"
        )

    def test_heading_disagreeing_with_the_link_is_refused(self):
        assert nj.parse_document(FIXTURE["question_2021_2"], "1") is None

    def test_unrecognised_shape_is_refused(self):
        assert nj.parse_document("PUBLIC QUESTION NO. 1\nTITLE\nno statement here", "1") is None


class TestFetchMeasures:
    async def test_2026_page_reads_as_none(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            return FIXTURE["page_2026"]
        monkeypatch.setattr(nj, "fetch_text_with_retry", fake_text)
        assert await nj.fetch_measures(None, 2026) == []

    async def test_2021_flow_returns_both_questions(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            return FIXTURE["page_2021"]

        async def fake_bytes(client, limiter, url, label, **kw):
            return url.encode()
        monkeypatch.setattr(nj, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(nj, "fetch_bytes_with_retry", fake_bytes)
        monkeypatch.setattr(
            nj, "_extract_text",
            lambda raw: FIXTURE["question_2021_1" if raw.decode().endswith("1-english.pdf") else "question_2021_2"],
        )
        result = await nj.fetch_measures(None, 2021)
        assert [p["number"] for p, _ in result] == ["1", "2"]
        assert result[1][1].endswith("2021-public-question-2-english.pdf")

    async def test_one_question_failing_fails_the_whole_fetch(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            return FIXTURE["page_2021"]

        async def fake_bytes(client, limiter, url, label, **kw):
            return None if url.endswith("2-english.pdf") else url.encode()
        monkeypatch.setattr(nj, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(nj, "fetch_bytes_with_retry", fake_bytes)
        monkeypatch.setattr(nj, "_extract_text", lambda raw: FIXTURE["question_2021_1"])
        assert await nj.fetch_measures(None, 2021) is None

    async def test_page_fetch_failure_is_none(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            return None
        monkeypatch.setattr(nj, "fetch_text_with_retry", fake_text)
        assert await nj.fetch_measures(None, 2026) is None
