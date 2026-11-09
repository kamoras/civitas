"""Minnesota's live reader (state_candidates_mn.fetch_general_results).

Fixtures, read 2026-11-09 from electionresultsfiles.sos.mn.gov, CRLF as
served: the 2026 general's ussenate.txt whole, and the first five lines of
ushouse.txt (districts 1 and 2, district 1 with its write-in line).
"""

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_mn as mn
from app.pipeline.fetch.election_results import FeedUnreadable
from app.pipeline.fetch.state_candidate_sources import source_for_state

FIXTURES = Path(__file__).parent
DAY = date(2026, 11, 3)
SENATE = (FIXTURES / "fixtures_mn_ussenate_2026.txt").read_bytes().decode()
HOUSE = (FIXTURES / "fixtures_mn_ushouse_2026.txt").read_bytes().decode()
SOURCE = election_results.live_source(source_for_state("MN"))
FOLDER = "https://electionresultsfiles.sos.mn.gov/20261103/"


def _serve(monkeypatch, pages: dict):
    async def fake(client, limiter, method, url, **kwargs):
        got = pages.get(url)
        if got is None:
            return None
        if isinstance(got, int):
            return SimpleNamespace(status_code=got, text="", headers={})
        return SimpleNamespace(status_code=200, text=got, headers={"Last-Modified": "Mon, 09 Nov 2026 21:58:17 GMT"})

    monkeypatch.setattr(mn, "fetch_with_retry", fake)


async def test_the_registered_reader_reads_both_files(monkeypatch):
    _serve(monkeypatch, {FOLDER + "ussenate.txt": SENATE, FOLDER + "ushouse.txt": HOUSE})
    assert election_results._reader_for(source_for_state("MN")) is mn.fetch_general_results
    count = await mn.fetch_general_results(None, DAY, "MN", SOURCE)
    assert count.official is False and count.units_complete is False and count.unit_label == "precincts"
    assert count.source_updated == datetime(2026, 11, 9, 21, 58, 17)
    seats = {(c.office, c.district): c for c in count.contests}
    assert set(seats) == {("S", None), ("H", 1), ("H", 2)}
    senate = seats[("S", None)]
    assert ("Peggy Flanagan", "D", 1520908) in senate.candidates
    assert ("Michele Tafoya", "R", 1233468) in senate.candidates
    assert (senate.reporting_units, senate.total_units, senate.total_votes) == (4105, 4105, 2831759)
    first = seats[("H", 1)]
    # The aggregated write-in line is no candidate, but the total counts it.
    assert first.candidates == [("Jake Johnson", "D", 163055), ("Brad Finstad", "R", 162733)]
    assert first.total_votes == 326179 == 163055 + 162733 + 391


async def test_a_date_with_no_files_is_none(monkeypatch):
    other = "https://electionresultsfiles.sos.mn.gov/20261104/"
    _serve(monkeypatch, {other + "ussenate.txt": 404, other + "ushouse.txt": 404})
    assert await mn.fetch_general_results(None, date(2026, 11, 4), "MN", SOURCE) is None


async def test_a_year_with_no_senate_race_reads_the_house_alone(monkeypatch):
    _serve(monkeypatch, {FOLDER + "ussenate.txt": 404, FOLDER + "ushouse.txt": HOUSE})
    count = await mn.fetch_general_results(None, DAY, "MN", SOURCE)
    assert {c.office for c in count.contests} == {"H"}


async def test_an_unreadable_file_is_unreadable(monkeypatch):
    _serve(monkeypatch, {FOLDER + "ussenate.txt": SENATE})
    with pytest.raises(FeedUnreadable):
        await mn.fetch_general_results(None, DAY, "MN", SOURCE)


def test_a_line_in_another_layout_is_unreadable():
    with pytest.raises(FeedUnreadable):
        mn.parse_media_file("MN;;;0102;U.S. Senator;;1001;Someone\r\n")
