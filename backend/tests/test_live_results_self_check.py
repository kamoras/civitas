"""The readers' self-check (live_results/readiness.py) and placing a contest
by its candidates (live_results/sync.place_by_candidates)."""

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.live_results import readiness
from app.live_results import sync as er
from app.models import Candidate, Race, RaceResult
from app.pipeline.fetch.election_results import ContestCount, StateCount

DAY = date(2026, 11, 3)
SEATS = {"IA": 4, "ID": 2, "MT": 2, "RI": 2}


def _house_races(db, state, n):
    for d in range(1, n + 1):
        db.add(Race(id=f"2026-HOUSE-{state}-{d}", cycle_year=2026, office="H", state=state, district=d))
    db.flush()


class TestBundledRecordings:
    @pytest.mark.parametrize("state", sorted(SEATS))
    async def test_each_bundled_recording_passes_through_its_real_reader(self, db_session, state):
        """The first-install recordings, replayed offline through the
        state's own reader and source entry, account for every seat."""
        _house_races(db_session, state, SEATS[state])
        assert readiness.load_recording(state) is not None
        assert await readiness.self_check(db_session, None, state, DAY) is None

    async def test_a_reader_that_stops_reading_its_recording_fails(self, db_session, monkeypatch):
        _house_races(db_session, "MT", 2)
        from app.pipeline.fetch import state_candidates_totalvote as tv

        monkeypatch.setattr(tv, "parse_office", lambda label: None)
        assert await readiness.self_check(db_session, None, "MT", DAY) == (
            "its reader, on its recorded 2024-11-05 feed, found no contest for 2026-HOUSE-MT-1, 2026-HOUSE-MT-2")


class TestLiveSelfCheck:
    async def test_a_previous_general_read_live_is_recorded_and_then_replayed(self, db_session, monkeypatch):
        """Utah's index still lists its 2024 general: the self-check reads
        it live through the reader, records the responses, and replays the
        recording on the next check without a request."""
        fixture = json.loads((Path(__file__).parent / "fixtures" / "live_results"
                              / "ut_general_2024_enhanced_voting.json").read_text())
        _house_races(db_session, "UT", 1)
        monkeypatch.setattr(readiness, "_BUNDLED_RECORDINGS", readiness._BUNDLED_RECORDINGS / "none")
        requests = []

        def handler(request):
            requests.append(str(request.url))
            url = str(request.url)
            if url.endswith("/api/jurisdictions/Utah"):
                return httpx.Response(200, json={"elections": [{
                    "publicElectionId": "general11052024", "electionDate": "2024-11-05",
                    "name": [{"languageId": "en", "text": "2024 General Election"}]}]})
            if url.endswith("/general11052024/data"):
                return httpx.Response(200, json=fixture["final"])
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await readiness.self_check(db_session, client, "UT", DAY) is None
        assert requests and readiness.load_recording("UT")["election_day"] == "2024-11-05"
        requests.clear()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await readiness.self_check(db_session, client, "UT", DAY) is None
        assert requests == []

    async def test_a_ready_state_is_recorded_for_the_next_cycle(self, db_session, monkeypatch):
        _house_races(db_session, "UT", 1)

        async def fetch(client, state, day):
            await client.get("https://results.example/ut")
            return StateCount(source_name="X", page_url=None, official=False,
                              contests=[ContestCount(office="H", district=1, candidates=[("A", "R", 0)])])

        monkeypatch.setattr(readiness, "fetch_state_count", fetch)
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text="staged"))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await readiness.state_status(db_session, client, "UT", DAY) == ("ready", None)
        recording = readiness.load_recording("UT")
        assert recording["election_day"] == "2026-11-03"
        assert recording["responses"] == {"https://results.example/ut": {
            "status": 200, "headers": {"content-type": "text/plain; charset=utf-8"}, "body": "staged"}}


AFTER_CLOSE = datetime(2026, 11, 4, 3)


def _race(db, confirmed=False):
    race = Race(id="2026-HOUSE-GA-2", cycle_year=2026, office="H", state="GA", district=2)
    db.add(race)
    db.add(Candidate(id="H6GA02001", race_id=race.id, name="SMITH, DANA", party="DEM", confirmed_general=confirmed))
    db.add(Candidate(id="H6GA02002", race_id=race.id, name="JONES, RAY", party="REP", confirmed_general=confirmed))
    db.flush()
    return race


def _contest(d, r, office="H", district=2):
    return ContestCount(office=office, district=district, candidates=[("Dana Smith", "D", d), ("Ray Jones", "R", r)],
                        reporting_units=1, total_units=10)


def _sync(db, contests=(), unplaced=()):
    count = StateCount(source_name="GA", page_url=None, official=False, contests=list(contests),
                       unplaced=list(unplaced))

    async def fake(client, state, day):
        return count

    with patch.object(er, "fetch_state_count", fake), patch.object(er, "utcnow", return_value=AFTER_CLOSE), \
            patch.object(er, "send_ops_alert"):
        return asyncio.run(er.sync_state(db, None, "GA", DAY))


class TestPlaceByCandidates:
    def test_a_contest_renamed_mid_count_keeps_moving(self, db_session):
        _race(db_session)
        assert _sync(db_session, [_contest(10, 20)])["races"] == 1
        out = _sync(db_session, unplaced=[_contest(30, 25, office="", district=None)])
        assert "dropped" not in out and out["races"] == 1
        assert db_session.get(RaceResult, "2026-HOUSE-GA-2").votes_counted == 55

    def test_a_contest_renamed_before_its_first_count_is_placed_by_the_nominees(self, db_session):
        _race(db_session, confirmed=True)
        assert _sync(db_session, unplaced=[_contest(1, 2, office="", district=None)])["races"] == 1

    def test_unconfirmed_candidates_or_two_matching_contests_place_nothing(self, db_session):
        _race(db_session)
        assert _sync(db_session, unplaced=[_contest(1, 2, office="", district=None)])["races"] == 0
        _sync(db_session, [_contest(10, 20)])
        twin = _contest(30, 25, office="", district=None)
        out = _sync(db_session, unplaced=[twin, twin])
        assert out["dropped"] == ["2026-HOUSE-GA-2"]
        assert db_session.get(RaceResult, "2026-HOUSE-GA-2").votes_counted == 30
