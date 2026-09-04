"""Tests for Arkansas's confirmed-general-candidate strategy
(state_candidates_ar.py).

fixtures_ar_elections.json, fixtures_ar_primary_search.json,
fixtures_ar_primary_results.json and fixtures_ar_runoff_search.json are
REAL — trimmed JSON responses from enr-results-api.totalresults.com,
fetched live 2026-09-04 from the real, certified 2026 Preferential
Primary (and the real, contest-free-for-federal 2026 Primary Runoff).
The election list keeps a real same-year decoy ("2026 Primary Special
Election") that must NOT match the primary/runoff name patterns. The
primary search fixture keeps 2 real non-federal contests (a Governor race
and a County Sheriff race) alongside the 5 real federal ones, to prove
the client-side contestTypeCode filter actually excludes them rather
than the fixture just happening to have none. The results fixture has
each contest's real `locations` (per-precinct breakdown) field stripped
— unused by the parser, and the only reason the raw capture was 40x this
size.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_ar as ar

FIXTURES = Path(__file__).parent
ELECTIONS = json.loads((FIXTURES / "fixtures_ar_elections.json").read_text())
PRIMARY_SEARCH = json.loads((FIXTURES / "fixtures_ar_primary_search.json").read_text())
PRIMARY_RESULTS = json.loads((FIXTURES / "fixtures_ar_primary_results.json").read_text())
RUNOFF_SEARCH = json.loads((FIXTURES / "fixtures_ar_runoff_search.json").read_text())

PRIMARY_ID = "7f77a178-af02-40ec-92db-c5cc50882c68"
RUNOFF_ID = "b412bdef-f97a-45bc-b3ec-6761d28caf9e"


class _JsonResp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.mark.asyncio
class TestDiscoverElectionIds:
    async def test_matches_primary_and_runoff_by_name_and_year(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            assert "GetElectionList" in url
            return _JsonResp(ELECTIONS)

        monkeypatch.setattr(ar, "fetch_with_retry", fake)
        primary_id, runoff_id = await ar._discover_election_ids(None, 2026)
        assert primary_id == PRIMARY_ID
        assert runoff_id == RUNOFF_ID

    async def test_a_same_year_election_that_is_neither_is_not_matched(self, monkeypatch):
        # The real 2026 fixture also carries a "2026 Primary Special
        # Election" -- same year, contains neither "preferential primary"
        # nor "primary runoff" -- proving the match is on name, not year.
        async def fake(client, rl, method, url, **kw):
            return _JsonResp(ELECTIONS)

        monkeypatch.setattr(ar, "fetch_with_retry", fake)
        primary_id, runoff_id = await ar._discover_election_ids(None, 2026)
        assert primary_id != "4b025e66-db9f-4e01-a7b8-3d06d87bccda"
        assert runoff_id != "4b025e66-db9f-4e01-a7b8-3d06d87bccda"

    async def test_no_match_for_a_year_not_in_the_list(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return _JsonResp(ELECTIONS)

        monkeypatch.setattr(ar, "fetch_with_retry", fake)
        assert await ar._discover_election_ids(None, 2028) == (None, None)

    async def test_fetch_failure_is_none_not_empty(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(ar, "fetch_with_retry", fake)
        assert await ar._discover_election_ids(None, 2026) == (None, None)


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    def _patched(self, monkeypatch, *, primary_search=None, primary_results=None, runoff_search=None):
        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _JsonResp(ELECTIONS)
            if f"electionID={PRIMARY_ID}" in url and "GetContestSearchList" in url:
                return _JsonResp(primary_search if primary_search is not None else PRIMARY_SEARCH)
            if f"electionID={PRIMARY_ID}" in url and "GetContestResults" in url:
                return _JsonResp(primary_results if primary_results is not None else PRIMARY_RESULTS)
            if f"electionID={RUNOFF_ID}" in url and "GetContestSearchList" in url:
                return _JsonResp(runoff_search if runoff_search is not None else RUNOFF_SEARCH)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(ar, "fetch_with_retry", fake)

    async def test_real_primary_resolves_to_the_real_certified_winners(self, monkeypatch):
        self._patched(monkeypatch)
        result = await ar.fetch_confirmed_candidates(None, 2026, "AR", {"runoff_threshold_pct": 50.0})
        assert {"office": "S", "district": None, "party": "R", "last_name": "Cotton"} in result
        assert {"office": "S", "district": None, "party": "D", "last_name": "Shoffner"} in result
        assert {"office": "H", "district": 2, "party": "R", "last_name": "Hill"} in result
        assert {"office": "H", "district": 2, "party": "D", "last_name": "Jones"} in result
        assert {"office": "H", "district": 4, "party": "D", "last_name": "Russell"} in result
        assert len(result) == 5

    async def test_non_federal_contests_in_the_search_list_are_excluded(self, monkeypatch):
        # The real primary search fixture also carries a real Governor
        # race and a real County Sheriff race.
        self._patched(monkeypatch)
        result = await ar.fetch_confirmed_candidates(None, 2026, "AR", {"runoff_threshold_pct": 50.0})
        assert all(r["office"] in ("H", "S") for r in result)

    async def test_election_list_fetch_failure_returns_empty_not_none(self, monkeypatch):
        # Not-yet-published-this-cycle and a real fetch failure of the
        # SAME endpoint are indistinguishable from here, so this stays on
        # the healthy-unknown side rather than flagging a pipeline error.
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(ar, "fetch_with_retry", fake)
        assert await ar.fetch_confirmed_candidates(None, 2026, "AR", {}) == []

    async def test_contest_search_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _JsonResp(ELECTIONS)
            return None

        monkeypatch.setattr(ar, "fetch_with_retry", fake)
        assert await ar.fetch_confirmed_candidates(None, 2026, "AR", {}) is None

    async def test_results_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _JsonResp(ELECTIONS)
            if "GetContestSearchList" in url and f"electionID={PRIMARY_ID}" in url:
                return _JsonResp(PRIMARY_SEARCH)
            return None

        monkeypatch.setattr(ar, "fetch_with_retry", fake)
        assert await ar.fetch_confirmed_candidates(None, 2026, "AR", {}) is None

    async def test_a_runoff_stage_with_no_federal_contests_is_a_safe_no_op(self, monkeypatch):
        # The real 2026 runoff fixture has zero federal contests -- the
        # primary's answers must survive untouched.
        self._patched(monkeypatch)
        result = await ar.fetch_confirmed_candidates(None, 2026, "AR", {"runoff_threshold_pct": 50.0})
        assert len(result) == 5

    async def test_runoff_result_for_a_seat_overrides_the_primary(self, monkeypatch):
        # Constructed: give the DEM CD04 primary contest a sub-threshold
        # leader (below 50%), then have the SAME seat appear, decided, on
        # a constructed runoff stage -- the runoff's answer must win.
        cd4_id = "784bb10e-b4fe-4fc7-bf03-16c57e348225"
        sub_threshold_results = json.loads(json.dumps(PRIMARY_RESULTS))
        sub_threshold_results["response"]["contests"][cd4_id]["choices"] = [
            {"choiceID": "aaa", "totalVotes": 40},
            {"choiceID": "bbb", "totalVotes": 35},
            {"choiceID": "ccc", "totalVotes": 25},
        ]
        sub_threshold_search = json.loads(json.dumps(PRIMARY_SEARCH))
        sub_threshold_search["response"]["contests"][cd4_id]["choices"] = {
            "aaa": {"name": "Primary Leader"},
            "bbb": {"name": "Primary Second"},
            "ccc": {"name": "Primary Third"},
        }
        runoff_search_with_cd4 = {
            "response": {
                "contests": {
                    cd4_id: {
                        "contestName": "DEM U.S. Congress District 04",
                        "contestTypeCode": "Federal",
                        "choices": {"xxx": {"name": "Runoff Winner"}, "yyy": {"name": "Runoff Loser"}},
                    },
                },
            },
        }

        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _JsonResp(ELECTIONS)
            if f"electionID={PRIMARY_ID}" in url and "GetContestSearchList" in url:
                return _JsonResp(sub_threshold_search)
            if f"electionID={PRIMARY_ID}" in url and "GetContestResults" in url:
                return _JsonResp(sub_threshold_results)
            if f"electionID={RUNOFF_ID}" in url and "GetContestSearchList" in url:
                return _JsonResp(runoff_search_with_cd4)
            if f"electionID={RUNOFF_ID}" in url and "GetContestResults" in url:
                return _JsonResp({
                    "response": {"contests": {cd4_id: {"choices": [
                        {"choiceID": "xxx", "totalVotes": 60},
                        {"choiceID": "yyy", "totalVotes": 40},
                    ]}}},
                })
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(ar, "fetch_with_retry", fake)
        result = await ar.fetch_confirmed_candidates(None, 2026, "AR", {"runoff_threshold_pct": 50.0})
        cd4 = [r for r in result if r["district"] == 4]
        assert cd4 == [{"office": "H", "district": 4, "party": "D", "last_name": "Winner"}]
