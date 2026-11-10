"""Delaware's live reader (state_candidates_de.fetch_general_results), on
the bundled recording's responses (data/live_results_recordings/DE.json:
the results index and the 2026 general's statewide file, read 2026-11-10
and cut to the index's links and the federal contests)."""

import copy
import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.certification_deadlines import deadline
from app.live_results import readiness
from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_de as de
from app.pipeline.fetch.election_results import FeedUnreadable, UntrustedCount
from app.pipeline.fetch.state_candidate_sources import source_for_state

RESPONSES = json.loads((readiness._BUNDLED_RECORDINGS / "DE.json").read_text())["responses"]
SOURCE = election_results.live_source(source_for_state("DE"))
DAY = date(2026, 11, 3)
FILE = "https://elections.delaware.gov/results/enr/Election_StatewideResults_ID_GE2026.json"


def _serve(monkeypatch, responses: dict):
    async def fake(client, limiter, method, url, **kwargs):
        got = responses.get(url)
        if got is None:
            return None
        body = got["body"]
        return SimpleNamespace(status_code=got["status"], text=body, json=lambda: json.loads(body))

    monkeypatch.setattr(de, "fetch_with_retry", fake)


def _with_rows(change) -> dict:
    responses = copy.deepcopy(RESPONSES)
    rows = json.loads(responses[FILE]["body"])
    for row in rows:
        change(row)
    responses[FILE]["body"] = json.dumps(rows)
    return responses


async def test_the_registered_reader_reads_the_statewide_file(monkeypatch):
    _serve(monkeypatch, RESPONSES)
    assert election_results._reader_for(source_for_state("DE")) is de.fetch_general_results
    count = await de.fetch_general_results(None, DAY, "DE", SOURCE)
    assert count.official is False and count.units_complete is False and count.unit_label == "precincts"
    assert str(count.source_updated) == "2026-11-04 08:03:18.364000"
    seats = {(c.office, c.district): c for c in count.contests}
    assert set(seats) == {("S", None), ("H", None)}
    house = seats[("H", None)]
    assert house.candidates == [("SARAH MCBRIDE", "D", 243467), ('JOSEPH "DR. JOE" ARMINIO', "R", 134966)]
    assert house.total_votes == 243467 + 134966
    assert (house.reporting_units, house.total_units) == (530, 530)


async def test_official_results_on_every_row_are_official(monkeypatch):
    _serve(monkeypatch, _with_rows(lambda row: row.update({"Results Type": "OFFICIAL RESULTS"})))
    assert (await de.fetch_general_results(None, DAY, "DE", SOURCE)).official is True


async def test_a_file_dated_another_day_is_untrusted(monkeypatch):
    _serve(monkeypatch, _with_rows(lambda row: row.update({"Election Date": "2024-11-05"})))
    with pytest.raises(UntrustedCount):
        await de.fetch_general_results(None, DAY, "DE", SOURCE)


async def test_no_general_or_no_page_for_the_year_is_none(monkeypatch):
    _serve(monkeypatch, RESPONSES)
    assert await de.fetch_general_results(None, date(2026, 9, 8), "DE", SOURCE) is None
    assert await de.fetch_general_results(None, date(2024, 11, 5), "DE", SOURCE) is None


async def test_an_unreadable_index_or_file_is_unreadable(monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(FeedUnreadable):
        await de.fetch_general_results(None, DAY, "DE", SOURCE)
    _serve(monkeypatch, {k: v for k, v in RESPONSES.items() if k != FILE})
    with pytest.raises(FeedUnreadable):
        await de.fetch_general_results(None, DAY, "DE", SOURCE)


def test_the_superior_court_canvasses_on_the_second_day():
    assert deadline("DE", DAY) == date(2026, 11, 5)
