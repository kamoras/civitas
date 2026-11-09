"""Maryland's live reader (state_candidates_md.fetch_general_results).

Fixtures, read 2026-11-09 from elections.maryland.gov, trimmed:

  fixtures_md_general_index_2026.html  Three of the 2026 general results
      index's links: Representative in Congress, State Senator, Question 1.
  fixtures_md_congress_2026.html  The Representative in Congress page's
      heading and its District 1 and District 2 tables.
"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_md as md
from app.pipeline.fetch.election_results import FeedUnreadable
from app.pipeline.fetch.state_candidate_sources import source_for_state

FIXTURES = Path(__file__).parent
DAY = date(2026, 11, 3)
INDEX = (FIXTURES / "fixtures_md_general_index_2026.html").read_text()
CONGRESS = (FIXTURES / "fixtures_md_congress_2026.html").read_text()
SOURCE = election_results.live_source(source_for_state("MD"))
BASE = "https://elections.maryland.gov/elections/2026/general_results/"


def _serve(monkeypatch, pages: dict):
    asked = []

    async def fake(client, limiter, method, url, **kwargs):
        asked.append(url)
        got = pages.get(url)
        if got is None:
            return None
        if isinstance(got, int):
            return SimpleNamespace(status_code=got, text="")
        return SimpleNamespace(status_code=200, text=got)

    monkeypatch.setattr(md, "fetch_with_retry", fake)
    return asked


async def test_the_registered_reader_reads_the_federal_pages_the_index_names(monkeypatch):
    asked = _serve(monkeypatch, {BASE + "index.html": INDEX, BASE + "gen_results_2026_4.html": CONGRESS})
    assert election_results._reader_for(source_for_state("MD")) is md.fetch_general_results
    count = await md.fetch_general_results(None, DAY, "MD", SOURCE)
    # The State Senator and Question 1 pages are never asked for.
    assert asked == [BASE + "index.html", BASE + "gen_results_2026_4.html"]
    assert count.official is False
    seats = {(c.office, c.district): c for c in count.contests}
    assert set(seats) == {("H", 1), ("H", 2)}
    first = seats[("H", 1)]
    # A named write-in keeps their votes; "Other Write-Ins" is no candidate,
    # but the Totals row counts it.
    assert first.candidates == [("Dan Schwartz", "D", 139085), ("Andy Harris", "R", 159463),
                                ("Edward Shlikas", "I", 6)]
    assert first.total_votes == 299059 == 139085 + 159463 + 6 + 505
    assert all(c.reporting_units is None for c in count.contests)


async def test_an_official_heading_is_official(monkeypatch):
    _serve(monkeypatch, {
        BASE + "index.html": INDEX,
        BASE + "gen_results_2026_4.html": CONGRESS.replace("<h1> Unofficial", "<h1> Official"),
    })
    assert (await md.fetch_general_results(None, DAY, "MD", SOURCE)).official is True


async def test_no_index_for_the_year_or_no_general_that_day_is_none(monkeypatch):
    _serve(monkeypatch, {BASE + "index.html": 404})
    assert await md.fetch_general_results(None, DAY, "MD", SOURCE) is None
    assert await md.fetch_general_results(None, date(2026, 7, 14), "MD", SOURCE) is None


async def test_an_unreadable_index_or_page_is_unreadable(monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(FeedUnreadable):
        await md.fetch_general_results(None, DAY, "MD", SOURCE)
    _serve(monkeypatch, {BASE + "index.html": INDEX})
    with pytest.raises(FeedUnreadable):
        await md.fetch_general_results(None, DAY, "MD", SOURCE)


def test_a_table_without_its_columns_is_unreadable():
    with pytest.raises(FeedUnreadable):
        md.parse_office_page(CONGRESS.replace('<th class="alignRight" scope="col">Total</th>', ""))
