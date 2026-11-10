"""Hawaii's live reader (state_candidates_hi.fetch_general_results).

Fixtures, read 2026-11-10 from elections.hawaii.gov and files.hawaii.gov:

  fixtures_hi_summary_general_2026.txt  The 2026 general's summary file
      (UTF-8, tab-separated) cut to its two U.S. House contests and one
      State Senate contest.
  fixtures_hi_summary_general_2024.txt  The 2024 general's (UTF-16,
      comma-separated) cut to its federal contests.
  fixtures_hi_statewide_general_2026.pdf  The 2026 general's statewide
      summary report, whole ("REPORT 4").
  fixtures_hi_statewide_general_2024.pdf  The 2024 general's, whole ("Final").
"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_hi as hi
from app.pipeline.fetch.election_results import FeedUnreadable, UntrustedCount
from app.pipeline.fetch.state_candidate_sources import source_for_state

FIXTURES = Path(__file__).parent
SOURCE = election_results.live_source(source_for_state("HI"))
PAGE = SOURCE["page_url"]
FOLDER_2026 = "https://elections.hawaii.gov/wp-content/results/2026 General/"
FOLDER_2024 = "https://files.hawaii.gov/elections/files/results/2024/General/"
LANDING = (
    f'<a href="{FOLDER_2026}summary.txt">Summary</a>'
    f'<a href="{FOLDER_2026}histatewide.pdf">Statewide</a>'
    f'<a href="{FOLDER_2024}summary.txt">Summary</a>'
)


def _bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


FILES = {
    PAGE: LANDING.encode(),
    FOLDER_2026 + "summary.txt": _bytes("fixtures_hi_summary_general_2026.txt"),
    FOLDER_2026 + "histatewide.pdf": _bytes("fixtures_hi_statewide_general_2026.pdf"),
    FOLDER_2024 + "summary.txt": _bytes("fixtures_hi_summary_general_2024.txt"),
    FOLDER_2024 + "histatewide.pdf": _bytes("fixtures_hi_statewide_general_2024.pdf"),
}


def _serve(monkeypatch, pages: dict):
    async def fake(client, limiter, method, url, **kwargs):
        got = pages.get(url)
        if got is None:
            return None
        if isinstance(got, int):
            return SimpleNamespace(status_code=got, text="", content=b"")
        return SimpleNamespace(status_code=200, text=got.decode("latin-1"), content=got)

    monkeypatch.setattr(hi, "fetch_with_retry", fake)


async def test_the_registered_reader_reads_the_general_summary(monkeypatch):
    _serve(monkeypatch, FILES)
    assert election_results._reader_for(source_for_state("HI")) is hi.fetch_general_results
    count = await hi.fetch_general_results(None, date(2026, 11, 3), "HI", SOURCE)
    # "REPORT 4" is no final report.
    assert count.official is False and count.units_complete is False and count.unit_label == "precincts"
    seats = {(c.office, c.district): c for c in count.contests}
    # The State Senate contest is no federal office.
    assert set(seats) == {("H", 1), ("H", 2)}
    first = seats[("H", 1)]
    assert first.candidates == [("Ed CASE", "D", 126022), ("Adriel C. LAM", "R", 40235),
                                ("Jordan S. CONLEY", "G", 6977), ("Nathan M. BERNING", None, 2341)]
    assert (first.reporting_units, first.total_units) == (112, 223)


async def test_the_last_general_reads_from_its_utf16_file_as_final(monkeypatch):
    _serve(monkeypatch, FILES)
    count = await hi.fetch_general_results(None, date(2024, 11, 5), "HI", SOURCE)
    assert count.official is True
    seats = {(c.office, c.district): c for c in count.contests}
    assert set(seats) == {("S", None), ("H", 1), ("H", 2)}
    assert ("Mazie K. HIRONO", "D", 324194) in seats[("S", None)].candidates
    assert (seats[("S", None)].reporting_units, seats[("S", None)].total_units) == (496, 496)


async def test_a_report_dated_another_day_is_untrusted(monkeypatch):
    # The 2026 folder served under the 2024 general's link.
    _serve(monkeypatch, {**FILES, FOLDER_2024 + "histatewide.pdf": FILES[FOLDER_2026 + "histatewide.pdf"]})
    with pytest.raises(UntrustedCount):
        await hi.fetch_general_results(None, date(2024, 11, 5), "HI", SOURCE)


async def test_no_general_that_day_or_no_link_for_the_year_is_none(monkeypatch):
    _serve(monkeypatch, FILES)
    assert await hi.fetch_general_results(None, date(2026, 8, 8), "HI", SOURCE) is None
    assert await hi.fetch_general_results(None, date(2028, 11, 7), "HI", SOURCE) is None


async def test_an_unreadable_page_or_file_is_unreadable(monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(FeedUnreadable):
        await hi.fetch_general_results(None, date(2026, 11, 3), "HI", SOURCE)
    _serve(monkeypatch, {k: v for k, v in FILES.items() if not k.endswith(".pdf")})
    with pytest.raises(FeedUnreadable):
        await hi.fetch_general_results(None, date(2026, 11, 3), "HI", SOURCE)


def test_a_file_without_its_columns_is_unreadable():
    text = _bytes("fixtures_hi_summary_general_2026.txt").replace(b"Counted Precincts", b"Precincts")
    with pytest.raises(FeedUnreadable):
        hi.parse_summary(text)
