"""Tests for Oregon's absence-only strategy (ballot_measures_or.py).

fixtures_or_voters_pamphlet_2026.json is REAL (see its _source).
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_or as orr
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

FX = json.loads((Path(__file__).parent / "fixtures_or_voters_pamphlet_2026.json").read_text())
BOOK = [FX["cover"], "Voters' Pamphlet Translations 3", FX["contents"], FX["statement_line"]]


def test_the_2026_pamphlet_proves_no_state_measure():
    assert orr.no_state_measures(BOOK, 2026)
    # A past measure named mid-sentence in a party's statement is not a
    # measure heading.
    assert "Measure 114" in FX["statement_line"]


def test_another_years_pamphlet_proves_nothing():
    assert not orr.no_state_measures(BOOK, 2028)


def test_a_measure_heading_or_contents_entry_refuses_to_conclude():
    assert not orr.no_state_measures(BOOK + ["Measure 120\nAmends Constitution: ..."], 2026)
    listed = FX["contents"].replace("Index of Candidates 29", "Measure 120 Transportation 27\nIndex of Candidates 29")
    assert not orr.no_state_measures([FX["cover"], listed], 2026)


def test_the_first_book_is_read_and_another_pamphlet_document_refuses():
    url, ok = orr.book_url(FX["page_links"], 2026)
    assert ok and url.endswith("/VP_G26-Book1_web.pdf")
    assert orr.book_url(FX["page_links"], 2028) == (None, True)
    volume = FX["page_links"].replace("</body>", '<a href="/elections/Documents/VP_G26-Vol1-Measures_web.pdf">x</a></body>')
    assert orr.book_url(volume, 2026) == (None, False)


@pytest.mark.asyncio
async def test_no_book_yet_is_not_yet_published(monkeypatch):
    async def page(client, url, label, **kw):
        return "<html><body><a href='/x.pdf'>Something else</a></body></html>"

    monkeypatch.setattr(orr, "get_text", page)
    with pytest.raises(NotYetPublished):
        await orr.fetch_measures(None, 2026)
