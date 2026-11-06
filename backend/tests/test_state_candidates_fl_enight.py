"""Florida's live reader (fetch/state_candidates_fl_enight.py).

Fixtures, read 2026-11-06 from the Division of Elections, trimmed:

  fixtures_fl_enight_2026.txt  The 2026 general's election-night file
      (20261103_ElecResultsFL.txt), CRLF as served: the Senate special in
      Alachua, Franklin and Lake; districts 5 (a write-in candidate with no
      votes) and 11 in every county they span; and three Governor rows.
  fixtures_fl_county_status_2026.html  County Reporting Status: its
      heading and the rows of the seven counties above plus TOTAL. Only
      Franklin had all five completion flags set.
"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_fl_enight as fl
from app.pipeline.fetch.election_results import FeedUnreadable, UntrustedCount
from app.pipeline.fetch.state_candidate_sources import source_for_state

FIXTURES = Path(__file__).parent
DAY = date(2026, 11, 3)
RESULTS = (FIXTURES / "fixtures_fl_enight_2026.txt").read_bytes().decode()
STATUS = (FIXTURES / "fixtures_fl_county_status_2026.html").read_text()
SOURCE = election_results.live_source(source_for_state("FL"))
FILE = "https://flelectionfiles.floridados.gov/enightfilespublic/20261103_ElecResultsFL.txt"
DOWNLOADS = (f'<a href="{FILE}?v=163630267" title="Tab Delimited Text File">Tab Delimited Text File</a>'
             f'<a href="{FILE[:-4]}.xls?v=163630267">Excel File</a>')


def _serve(monkeypatch, pages: dict):
    """fetch_with_retry answering from `pages` (url -> text, or a status
    int); an address not listed fails as unreadable (None)."""
    asked = []

    async def fake(client, limiter, method, url, **kwargs):
        asked.append(url)
        got = pages.get(url)
        if got is None:
            return None
        if isinstance(got, int):
            return SimpleNamespace(status_code=got, text="") if got in kwargs.get("expected_statuses", ()) else None
        return SimpleNamespace(status_code=200, text=got)

    monkeypatch.setattr(fl, "fetch_with_retry", fake)
    return asked


def _by_seat(count):
    return {(c.office, c.district): c for c in count.contests}


async def test_the_registered_reader_reads_the_dated_file_and_county_completion(monkeypatch):
    asked = _serve(monkeypatch, {
        SOURCE["downloads_url"]: DOWNLOADS,
        f"{FILE}?v=163630267": RESULTS,
        SOURCE["status_url"]: STATUS,
    })
    assert election_results._reader_for(source_for_state("FL")) is fl.fetch_general_results
    count = await fl.fetch_general_results(None, DAY, "FL", SOURCE)
    assert f"{FILE}?v=163630267" in asked
    assert count.official is False and count.unit_label == "counties" and count.units_complete is True
    seats = _by_seat(count)
    assert set(seats) == {("S", None), ("H", 5), ("H", 11)}
    senate = seats[("S", None)]
    # Votes summed over the counties; NPA reads as an independent.
    assert senate.candidates == [("Ashley Moody", "R", 142490), ("Angie Nixon", "D", 142746),
                                 ("Neil J. Gillespie", "I", 3157)]
    # Only Franklin had every part of its count complete.
    assert (senate.reporting_units, senate.total_units) == (1, 3)
    assert (seats[("H", 11)].reporting_units, seats[("H", 11)].total_units) == (0, 3)
    # A write-in candidate keeps their row (and their votes) with no party.
    assert ("William Lintag Upham", None, 0) in seats[("H", 5)].candidates
    # The Governor's contest is no federal seat: kept unplaced, not dropped.
    assert [c.office for c in count.unplaced] == [""]


async def test_a_date_the_page_does_not_link_is_asked_by_its_name(monkeypatch):
    earlier = date(2024, 11, 5)
    file_2024 = "https://flelectionfiles.floridados.gov/enightfilespublic/20241105_ElecResultsFL.txt"
    asked = _serve(monkeypatch, {SOURCE["downloads_url"]: DOWNLOADS, file_2024: 404})
    assert await fl.fetch_general_results(None, earlier, "FL", SOURCE) is None
    assert asked[-1] == file_2024


async def test_an_unreadable_downloads_page_is_not_a_missing_election(monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(FeedUnreadable):
        await fl.fetch_general_results(None, DAY, "FL", SOURCE)


async def test_an_unreadable_file_is_unreadable(monkeypatch):
    _serve(monkeypatch, {SOURCE["downloads_url"]: DOWNLOADS})
    with pytest.raises(FeedUnreadable):
        await fl.fetch_general_results(None, DAY, "FL", SOURCE)


async def test_a_row_for_another_day_stores_nothing(monkeypatch):
    _serve(monkeypatch, {
        SOURCE["downloads_url"]: DOWNLOADS,
        f"{FILE}?v=163630267": RESULTS.replace("11/03/2026", "11/05/2024", 1),
    })
    with pytest.raises(UntrustedCount):
        await fl.fetch_general_results(None, DAY, "FL", SOURCE)


async def test_a_status_page_for_another_election_leaves_the_figure_out(monkeypatch):
    _serve(monkeypatch, {
        SOURCE["downloads_url"]: DOWNLOADS,
        f"{FILE}?v=163630267": RESULTS,
        SOURCE["status_url"]: STATUS.replace("November 3, 2026", "August 18, 2026"),
    })
    count = await fl.fetch_general_results(None, DAY, "FL", SOURCE)
    assert all(c.reporting_units is None and c.total_units is None for c in count.contests)
    assert _by_seat(count)[("S", None)].candidates[0] == ("Ashley Moody", "R", 142490)


async def test_an_unreadable_status_page_keeps_the_count(monkeypatch):
    _serve(monkeypatch, {SOURCE["downloads_url"]: DOWNLOADS, f"{FILE}?v=163630267": RESULTS})
    count = await fl.fetch_general_results(None, DAY, "FL", SOURCE)
    assert len(count.contests) == 3
    assert all(c.reporting_units is None for c in count.contests)


async def test_a_county_the_status_page_does_not_name_leaves_its_contests_unfigured(monkeypatch):
    _serve(monkeypatch, {
        SOURCE["downloads_url"]: DOWNLOADS,
        f"{FILE}?v=163630267": RESULTS,
        SOURCE["status_url"]: STATUS.replace("<u>Lake</u>", "<u>Lake County</u>"),
    })
    seats = _by_seat(await fl.fetch_general_results(None, DAY, "FL", SOURCE))
    assert seats[("S", None)].total_units is None and seats[("H", 11)].total_units is None
    assert (seats[("H", 5)].reporting_units, seats[("H", 5)].total_units) == (0, 2)


def test_the_status_page_is_read_county_by_county():
    status = fl.parse_status(STATUS, DAY)
    assert status["Franklin"] is True and status["Alachua"] is False and "TOTAL" not in status
    # A row with other than five flags is a page this doesn't know.
    assert fl.parse_status(STATUS.replace("<span class=Y></span>", "", 1), DAY) is None


def test_columns_it_does_not_know_are_unreadable():
    with pytest.raises(FeedUnreadable):
        fl.parse_results("Date\tRace\tVotes\r\n11/03/2026\tUnited States Senator\t1\r\n", DAY, "FL")
