"""Connecticut's live reader (state_candidates_ct.fetch_general_results).

Fixture, read 2026-11-10 from ctemspublic.tgstg.net and trimmed: the
election list's 2026 general and August primaries, and the general's
files cut to the 2nd and 5th districts (both with a cross-endorsed
candidate) and the 1st State Senate district, three towns' statuses, and
candidates' addresses dropped.
"""

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from app.certification_deadlines import deadline
from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_ct as ct
from app.pipeline.fetch.election_results import FeedUnreadable, UntrustedCount
from app.pipeline.fetch.state_candidate_sources import source_for_state

FILES = json.loads((Path(__file__).parent / "fixtures_ct_enr_general_2026.json").read_text())
SOURCE = election_results.live_source(source_for_state("CT"))
DAY = date(2026, 11, 3)
FOLDER = "election/108/53452/"


def _serve(monkeypatch, files: dict):
    async def fake(client, limiter, url, label, **kwargs):
        return copy.deepcopy(files.get(url.removeprefix(ct.BASE + "/")))

    monkeypatch.setattr(ct, "fetch_json_with_retry", fake)


async def test_the_registered_reader_combines_each_candidates_lines(monkeypatch):
    _serve(monkeypatch, FILES)
    assert election_results._reader_for(source_for_state("CT")) is ct.fetch_general_results
    count = await ct.fetch_general_results(None, DAY, "CT", SOURCE)
    assert count.official is False and count.units_complete is False and count.unit_label == "precincts"
    assert count.source_version == "108:53452"
    seats = {(c.office, c.district): c for c in count.contests}
    # The State Senate district is no federal office.
    assert set(seats) == {("H", 2), ("H", 5)}
    fifth = seats[("H", 5)]
    # Democratic 139,000 and Working Families 7,353: one candidate, under
    # the line that carried most, with the app's own grouped total.
    assert fifth.candidates == [("Jahana Hayes", "D", 146353), ("Chris Shea", "R", 106667),
                                ("Jonathan DeBarros", None, 46)]
    assert fifth.total_votes == 146353 + 106667 + 46
    assert (fifth.reporting_units, fifth.total_units) == (161, 161)
    second = seats[("H", 2)]
    assert ("George Patrick Austin", "R", 109628) in second.candidates


async def test_every_town_official_is_official(monkeypatch):
    files = copy.deepcopy(FILES)
    for town in files[FOLDER + "townStatus_Electiondata.json"].values():
        town["TS"] = "( Official Results )"
    _serve(monkeypatch, files)
    assert (await ct.fetch_general_results(None, DAY, "CT", SOURCE)).official is True


async def test_a_grouped_total_that_isnt_the_lines_sum_is_unreadable(monkeypatch):
    files = copy.deepcopy(FILES)
    files[FOLDER + "candidateGrouping_Electiondata.json"]["19670"][0]["43492"]["V"] = "146354"
    _serve(monkeypatch, files)
    with pytest.raises(FeedUnreadable):
        await ct.fetch_general_results(None, DAY, "CT", SOURCE)


async def test_an_election_on_the_date_that_isnt_a_general_is_none(monkeypatch):
    files = copy.deepcopy(FILES)
    files[FOLDER + "Lookupdata.json"]["election"]["ET"] = "P"
    _serve(monkeypatch, files)
    assert await ct.fetch_general_results(None, DAY, "CT", SOURCE) is None
    _serve(monkeypatch, FILES)
    assert await ct.fetch_general_results(None, date(2026, 11, 4), "CT", SOURCE) is None


async def test_two_generals_on_one_date_are_untrusted(monkeypatch):
    files = copy.deepcopy(FILES)
    files["Elections.json"].append({"ID": "109", "Name": "11/03/2026 -- Another", "DefaultElection": "N"})
    files["election/109/Version.json"] = {"Version": 1}
    files["election/109/1/Lookupdata.json"] = files[FOLDER + "Lookupdata.json"]
    _serve(monkeypatch, files)
    with pytest.raises(UntrustedCount):
        await ct.fetch_general_results(None, DAY, "CT", SOURCE)


async def test_an_unreadable_file_is_unreadable(monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(FeedUnreadable):
        await ct.fetch_general_results(None, DAY, "CT", SOURCE)
    _serve(monkeypatch, {k: v for k, v in FILES.items() if not k.endswith("townStatus_Electiondata.json")})
    with pytest.raises(FeedUnreadable):
        await ct.fetch_general_results(None, DAY, "CT", SOURCE)


def test_the_federal_canvass_is_the_last_wednesday_of_the_month():
    assert deadline("CT", DAY) == date(2026, 11, 25)
    assert deadline("CT", date(2024, 11, 5)) == date(2024, 11, 27)
