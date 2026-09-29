"""Tests for Indiana's ballot-measure strategy (ballot_measures_in.py).

fixtures_in_legislation_summary.json is REAL — two links from in.gov's
Indiana Election Legislation Summaries index, and pdf_text() of the
"Summary of 2026 Election Legislation" PDF from just before its INDIANA
CONSTITUTIONAL AMENDMENTS section to the end, fetched live 2026-09-28.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_in as ind
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

FIXTURE = json.loads((Path(__file__).parent / "fixtures_in_legislation_summary.json").read_text())


def test_summary_link_is_found_by_its_text():
    url = ind.find_summary_url(FIXTURE["index"], 2026)
    assert url == "https://www.in.gov/dA/d13ecb1f55/2026-Indiana-Election-Legislation-Summary.FINAL.pdf?language_id=1"
    assert ind.find_summary_url(FIXTURE["index"], 2030) is None


def test_both_real_public_questions_verbatim():
    one, two = ind.parse_summary(FIXTURE["summary_text"], 2026)
    assert (one["number"], one["title"]) == ("1", "Public Question #1")
    assert one["official_summary"].startswith("Currently, under the Constitution of the State of Indiana")
    assert one["official_summary"].endswith("(This question concerns Article 1, Section 17 of the Constitution of the State of Indiana.)")
    assert (two["number"], two["title"]) == ("2", "Public Question #2")
    assert two["official_summary"].startswith("Shall the Constitution of the State of Indiana be amended to permit")
    # The page-break page number ("21") never lands in ballot text.
    assert " 21 " not in two["official_summary"]
    for m in (one, two):
        assert m["origin"] == "Indiana General Assembly"
        assert m["yes_means"] is None and m["fiscal_impact"] is None
        # The Division's own topic heading is not ballot text.
        assert "Limitations on the Right to Bail" not in m["official_summary"]


def test_questions_dated_to_another_election_are_not_read():
    # Dated 2026 by their own introductions: nothing for 2028's ballot.
    assert ind.parse_summary(FIXTURE["summary_text"], 2028) == []


def test_an_introduction_naming_no_recognisable_election_refuses():
    """The regression: the date check used to be one exact phrase, so an
    introduction worded any other way ("... at the 2026 general election")
    was skipped as if it were another year's — a real question silently
    dropped from a list published as complete."""
    reworded = FIXTURE["summary_text"].replace(
        "ballot at the November 3, 2026, general election.", "ballot this November.", 1,
    )
    assert ind.parse_summary(reworded, 2026) is None
    # Worded differently but still naming the year: read, not skipped.
    variant = FIXTURE["summary_text"].replace(
        "ballot at the November 3, 2026, general election.", "ballot at the 2026 general election.", 1,
    )
    assert [m["number"] for m in ind.parse_summary(variant, 2026)] == ["1", "2"]


def test_a_question_this_reader_cant_quote_refuses():
    broken = FIXTURE["summary_text"].replace("“Public Question #2", "“Public Question No. 2", 1)
    assert ind.parse_summary(broken, 2026) is None


def test_nothing_to_quote_is_empty_never_a_question():
    assert ind.parse_summary("SUMMARY OF 2027 ELECTION LEGISLATION\nNothing here.", 2027) == []


def _client_serving(index_html, pdf_text):
    async def get_text(client, limiter, url, label, **kw):
        return index_html

    async def get_bytes(client, limiter, url, label, **kw):
        return b"%PDF-"

    return get_text, get_bytes, (lambda raw: pdf_text)


async def test_no_question_for_the_year_is_not_yet_published_never_none(monkeypatch):
    """A legislation summary is not a certified list, so an empty read
    can't be confirmed_none — and it isn't a failure either: the Division
    publishes the summary whatever it contains. Not yet covered, no alert
    (it used to be None, i.e. an ingest-failure page every night of a
    year with no question)."""
    get_text, get_bytes, text = _client_serving(FIXTURE["index"], FIXTURE["summary_text"])
    monkeypatch.setattr(ind, "fetch_text_with_retry", get_text)
    monkeypatch.setattr(ind, "fetch_bytes_with_retry", get_bytes)
    monkeypatch.setattr(ind, "pdf_text", text)
    monkeypatch.setattr(ind, "find_summary_url", lambda html, year: "https://x/summary.pdf")
    with pytest.raises(NotYetPublished):
        await ind.fetch_measures(None, 2028)
    parsed = await ind.fetch_measures(None, 2026)
    assert [p["number"] for p, _ in parsed] == ["1", "2"]


async def test_summary_not_linked_yet_is_not_yet_published(monkeypatch):
    get_text, get_bytes, text = _client_serving(FIXTURE["index"], FIXTURE["summary_text"])
    monkeypatch.setattr(ind, "fetch_text_with_retry", get_text)
    # The index links 2026's summary but not 2027's.
    with pytest.raises(NotYetPublished):
        await ind.fetch_measures(None, 2027)
    # An index page that isn't the one we know (no link for the prior
    # year either) is a failure, not "not yet".
    assert await ind.fetch_measures(None, 2031) is None
