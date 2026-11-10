"""North Carolina's live reader (state_candidates_nc_enr.fetch_general_results).

Fixture, read 2026-11-10 from er.ncsbe.gov and trimmed: the election
list's 2026 general, 2026 primary and 2024 general; the 2026 general's
county.txt cut to its statewide row and one county's, and results_0.txt
cut to the Senate race, the 1st and 2nd districts (the 2nd with its
write-in line) and two State Senate rows; the 2024 general's statewide
county row.
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.certification_deadlines import deadline
from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_nc_enr as nc
from app.pipeline.fetch.election_results import FeedUnreadable, UntrustedCount
from app.pipeline.fetch.state_candidate_sources import source_for_state

FILES = json.loads((Path(__file__).parent / "fixtures_nc_enr_2026.json").read_text())
SOURCE = election_results.live_source(source_for_state("NC"))
HOST = "https://er.ncsbe.gov/"
DAY = date(2026, 11, 3)


def _serve(monkeypatch, files: dict):
    async def fake(client, limiter, method, url, **kwargs):
        got = files.get(url.removeprefix(HOST))
        if got is None:
            return None
        return SimpleNamespace(status_code=200, json=lambda: got,
                               headers={"Last-Modified": "Mon, 09 Nov 2026 20:40:34 GMT"})

    monkeypatch.setattr(nc, "fetch_with_retry", fake)


async def test_the_registered_reader_reads_the_statewide_results(monkeypatch):
    _serve(monkeypatch, FILES)
    assert election_results._reader_for(source_for_state("NC")) is nc.fetch_general_results
    count = await nc.fetch_general_results(None, DAY, "NC", SOURCE)
    assert count.official is False and count.units_complete is False and count.unit_label == "precincts"
    assert str(count.source_updated) == "2026-11-09 20:40:34"
    seats = {(c.office, c.district): c for c in count.contests}
    # The State Senate rows are no federal office.
    assert set(seats) == {("S", None), ("H", 1), ("H", 2)}
    assert ("Roy Cooper", "D", 2467962) in seats[("S", None)].candidates
    second = seats[("H", 2)]
    # The write-in line is no candidate, but the total counts it.
    assert second.candidates == [("Deborah K. Ross", "D", 245402), ("Eugene F. Douglass", "R", 76763),
                                 ("Matthew Laszacs", "L", 4267)]
    assert second.total_votes == 245402 + 76763 + 4267 + 530
    assert (second.reporting_units, second.total_units) == (156, 156)


async def test_an_official_title_is_official(monkeypatch):
    counties = [dict(FILES["enr/20261103/data/county.txt"][0])] + FILES["enr/20261103/data/county.txt"][1:]
    counties[0]["tle"] = counties[0]["tle"].replace("UNOFFICIAL", "OFFICIAL")
    _serve(monkeypatch, {**FILES, "enr/20261103/data/county.txt": counties})
    assert (await nc.fetch_general_results(None, DAY, "NC", SOURCE)).official is True


async def test_a_folder_titled_another_election_is_untrusted(monkeypatch):
    # The 2024 general's statewide row served in the 2026 general's folder.
    _serve(monkeypatch, {**FILES, "enr/20261103/data/county.txt": FILES["enr/20241105/data/county.txt"]})
    with pytest.raises(UntrustedCount):
        await nc.fetch_general_results(None, DAY, "NC", SOURCE)


async def test_a_date_the_list_does_not_hold_is_none(monkeypatch):
    _serve(monkeypatch, FILES)
    assert await nc.fetch_general_results(None, date(2026, 11, 4), "NC", SOURCE) is None


async def test_an_unreadable_file_is_unreadable(monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(FeedUnreadable):
        await nc.fetch_general_results(None, DAY, "NC", SOURCE)
    _serve(monkeypatch, {k: v for k, v in FILES.items() if not k.endswith("results_0.txt")})
    with pytest.raises(FeedUnreadable):
        await nc.fetch_general_results(None, DAY, "NC", SOURCE)


def test_the_state_canvass_is_the_tuesday_three_weeks_after():
    assert deadline("NC", DAY) == date(2026, 11, 24)
