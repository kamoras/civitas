"""Tests for Hawaii's strategy (ballot_measures_hi.py).

fixtures_hi_proposed_amendments_2026.html is REAL — the content region
of elections.hawaii.gov/voting/2026-proposed-amendments-to-the-hawaii-
state-constitution/, fetched 2026-09-28, cut from its <h2> to just
before "How a Proposed Amendment is Approved". It keeps the real markup
difference this module is built for: Question 2's blockquote sits inside
four nested <div>s, Question 1's does not.
"""

from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_hi as hi

FIXTURE = (Path(__file__).parent / "fixtures_hi_proposed_amendments_2026.html").read_text()


def test_both_real_questions_parse_verbatim():
    measures = {m["number"]: m for m in hi.parse_page(FIXTURE)}
    assert sorted(measures) == ["1", "2"]
    one = measures["1"]
    assert one["title"] == (
        "Increasing the Timeframe for the Hawaii Senate to Consider and Act on Judicial Appointments."
    )
    assert one["official_summary"].startswith(
        "Shall the Constitution of the State of Hawaii be amended to allow the Senate more time"
    )
    assert one["official_summary"].endswith("about to adjourn the regular session?")
    # Question 2's blockquote is wrapped in nested divs — still paired.
    assert "resilient infrastructure for shelter and equity bonds" in measures["2"]["official_summary"]


def test_no_framing_or_fiscal_is_invented():
    for m in hi.parse_page(FIXTURE):
        assert m["yes_means"] is None and m["no_means"] is None and m["fiscal_impact"] is None
        assert m["origin"] == "Hawaii State Legislature"


def test_heading_without_a_blockquote_is_not_paired_with_the_next_questions_text():
    html = (
        "<div><p><strong>QUESTION #1: Lonely heading.</strong></p>"
        "<p><strong>QUESTION #2: Real one.</strong></p>"
        "<blockquote><p>Shall it?</p></blockquote></div>"
    )
    measures = hi.parse_page(html)
    assert [(m["number"], m["official_summary"]) for m in measures] == [("2", "Shall it?")]


@pytest.mark.asyncio
async def test_fetch_failure_is_none_not_empty(monkeypatch):
    async def fake_fetch(*a, **k):
        return None
    monkeypatch.setattr(hi, "fetch_text_with_retry", fake_fetch)
    assert await hi.fetch_measures(None, 2026) is None


@pytest.mark.asyncio
async def test_fetch_pairs_each_measure_with_the_page_url(monkeypatch):
    async def fake_fetch(*a, **k):
        return FIXTURE
    monkeypatch.setattr(hi, "fetch_text_with_retry", fake_fetch)
    pairs = await hi.fetch_measures(None, 2026)
    assert [url for _, url in pairs] == [hi.URL_PATTERN.format(year=2026)] * 2


@pytest.mark.asyncio
async def test_unpaired_question_heading_fails_the_state(monkeypatch):
    async def fake_fetch(*a, **k):
        return FIXTURE.replace("<blockquote", "<div").replace("</blockquote>", "</div>", 1)
    monkeypatch.setattr(hi, "fetch_text_with_retry", fake_fetch)
    assert await hi.fetch_measures(None, 2026) is None


@pytest.mark.asyncio
async def test_a_page_with_no_question_heading_is_a_failure_never_none(monkeypatch):
    """The regression: a 200 page whose headings this reader couldn't find
    (0 headings, 0 parsed) passed the count check and returned [] —
    confirmed none for a state with amendments."""
    async def fake_fetch(*a, **k):
        return FIXTURE.replace("QUESTION", "PROPOSAL")
    monkeypatch.setattr(hi, "fetch_text_with_retry", fake_fetch)
    assert await hi.fetch_measures(None, 2026) is None
