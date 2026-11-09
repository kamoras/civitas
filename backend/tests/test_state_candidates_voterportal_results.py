"""Louisiana's live reader (state_candidates_voterportal.fetch_general_results).

Fixture, read 2026-11-09 from voterportal.sos.la.gov, trimmed: the election
list's first two dates, and the 2026 general's two files cut to the U.S.
Senate race, the 1st and 5th congressional districts and one Public Service
Commission race.
"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app import recount_rules
from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_voterportal as portal
from app.pipeline.fetch.election_results import FeedUnreadable
from app.pipeline.fetch.state_candidate_sources import source_for_state

DAY = date(2026, 11, 3)
FILES = json.loads((Path(__file__).parent / "fixtures_la_portal_results_2026.json").read_text())
SOURCE = election_results.live_source(source_for_state("LA"))


def _client(files: dict) -> httpx.AsyncClient:
    def handler(request):
        got = files.get(request.url.params.get("blob"))
        return httpx.Response(200, json=got) if got is not None else httpx.Response(404, text="")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_registered_reader_reads_the_general_by_its_date():
    assert election_results._reader_for(source_for_state("LA")) is portal.fetch_general_results
    async with _client(FILES) as client:
        count = await portal.fetch_general_results(client, DAY, "LA", SOURCE)
    assert count.official is False and count.units_complete is False and count.unit_label == "precincts"
    seats = {(c.office, c.district): c for c in count.contests}
    # The Public Service Commission race is no federal office.
    assert set(seats) == {("S", None), ("H", 1), ("H", 5)}
    senate = seats[("S", None)]
    assert senate.candidates == [('"Jamie" Davis', "D", 720150), ("Julia Letlow", "R", 746000)]
    assert (senate.reporting_units, senate.total_units) == (3722, 3722)
    assert ("Steve Scalise", "R", 159892) in seats[("H", 1)].candidates
    assert len(seats[("H", 5)].candidates) == 9


async def test_a_house_seat_without_a_majority_owes_its_runoff():
    async with _client(FILES) as client:
        count = await portal.fetch_general_results(client, DAY, "LA", SOURCE)
    fifth = next(c for c in count.contests if (c.office, c.district) == ("H", 5))
    votes = [v for _, _, v in fifth.candidates]
    # The portal's own "Runoff" outcome is not read; the rule is Civitas's.
    assert recount_rules.close_enough_to_contest("LA", "H", 5, votes, fifth.votes_counted, 2026)


async def test_an_official_flag_on_the_date_is_official():
    dates = json.loads(json.dumps(FILES["ElectionDates.htm"]))
    dates["Dates"]["Date"][0]["ResultsOfficial"] = "1"
    async with _client({**FILES, "ElectionDates.htm": dates}) as client:
        assert (await portal.fetch_general_results(client, DAY, "LA", SOURCE)).official is True


async def test_a_date_the_list_does_not_hold_is_none():
    async with _client(FILES) as client:
        assert await portal.fetch_general_results(client, date(2026, 11, 4), "LA", SOURCE) is None


async def test_an_unreadable_list_or_file_is_unreadable():
    async with _client({}) as client:
        with pytest.raises(FeedUnreadable):
            await portal.fetch_general_results(client, DAY, "LA", SOURCE)
    listed = {"ElectionDates.htm": FILES["ElectionDates.htm"]}
    async with _client(listed) as client:
        with pytest.raises(FeedUnreadable):
            await portal.fetch_general_results(client, DAY, "LA", SOURCE)
