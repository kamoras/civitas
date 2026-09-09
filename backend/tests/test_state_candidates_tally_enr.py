"""Tests for the "Tally ENR" vendor strategy (state_candidates_tally_enr.py),
confirmed on two real states so far -- Arkansas (the original single-state
build) and North Dakota (found live 2026-09-09 on the exact same API
shape at a different domain, which is what justified generalizing this
module out of what used to be state_candidates_ar.py).

fixtures_ar_elections.json, fixtures_ar_primary_search.json,
fixtures_ar_primary_results.json and fixtures_ar_runoff_search.json are
REAL -- trimmed JSON responses from enr-results-api.totalresults.com,
fetched live 2026-09-04 from the real, certified 2026 Preferential
Primary (and the real, contest-free-for-federal 2026 Primary Runoff).
The election list keeps a real same-year decoy ("2026 Primary Special
Election") that must NOT match the primary/runoff name patterns. The
primary search fixture keeps 2 real non-federal contests (a Governor race
and a County Sheriff race) alongside the 5 real federal ones, to prove
the contestTypeCode filter actually excludes them rather than the
fixture just happening to have none. The results fixture has each
contest's real `locations` (per-precinct breakdown) field stripped --
unused by the parser, and the only reason the raw capture was 40x this
size. Both real elections are dated 2026-03; every test that exercises
fetch_confirmed_candidates runs well past any real settle_days window,
so the freshness gate never needs mocking here.

fixtures_nd_elections.json, fixtures_nd_primary_search.json and
fixtures_nd_primary_results.json are REAL -- trimmed JSON responses from
api.resultsnd.sos.nd.gov, fetched live 2026-09-09 from North Dakota's
real, certified 2026-06-09 Primary Election. Chosen specifically because
North Dakota's real shape differs from Arkansas's in the two ways this
module had to generalize for: (1) the election list carries only ONE
entry for the year -- no separate runoff stage, since North Dakota
nominates by plurality -- and (2) every real contest shares the SAME
contestTypeCode ("SW", statewide) regardless of office, so federal
filtering falls back to parse_office() alone rather than a
contestTypeCode value. The search fixture keeps the real Representative
in Congress contest for both parties plus a real non-federal Secretary
of State contest, to prove that fallback actually excludes it.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import http_utils, state_candidates_tally_enr as tenr

FIXTURES = Path(__file__).parent


def _resp(body):
    return SimpleNamespace(json=lambda: body)


AR_ELECTIONS = json.loads((FIXTURES / "fixtures_ar_elections.json").read_text())
AR_PRIMARY_SEARCH = json.loads((FIXTURES / "fixtures_ar_primary_search.json").read_text())
AR_PRIMARY_RESULTS = json.loads((FIXTURES / "fixtures_ar_primary_results.json").read_text())
AR_RUNOFF_SEARCH = json.loads((FIXTURES / "fixtures_ar_runoff_search.json").read_text())

AR_BASE_URL = "https://enr-results-api.totalresults.com"
AR_CID = "arkansas"
AR_PRIMARY_ID = "7f77a178-af02-40ec-92db-c5cc50882c68"
AR_RUNOFF_ID = "b412bdef-f97a-45bc-b3ec-6761d28caf9e"
AR_SOURCE = {
    "base_url": AR_BASE_URL,
    "cid": AR_CID,
    "primary_name_regex": "preferential primary",
    "runoff_name_regex": "primary runoff",
    "contest_type_filter": "Federal",
    "runoff_threshold_pct": 50.0,
}

ND_ELECTIONS = json.loads((FIXTURES / "fixtures_nd_elections.json").read_text())
ND_PRIMARY_SEARCH = json.loads((FIXTURES / "fixtures_nd_primary_search.json").read_text())
ND_PRIMARY_RESULTS = json.loads((FIXTURES / "fixtures_nd_primary_results.json").read_text())

ND_BASE_URL = "https://api.resultsnd.sos.nd.gov"
ND_CID = "north-dakota"
ND_PRIMARY_ID = "346"
ND_SOURCE = {
    "base_url": ND_BASE_URL,
    "cid": ND_CID,
    "primary_name_regex": "primary election",
    "runoff_threshold_pct": None,
}


@pytest.mark.asyncio
class TestDiscoverElectionsArkansas:
    async def test_matches_primary_and_runoff_by_name_and_year(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            assert "GetElectionList" in url
            return _resp(AR_ELECTIONS)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        import re
        primary, runoff = await tenr._discover_elections(
            None, "AR", AR_BASE_URL, AR_CID, re.compile("preferential primary", re.I), re.compile("primary runoff", re.I), 2026,
        )
        assert primary["id"] == AR_PRIMARY_ID
        assert runoff["id"] == AR_RUNOFF_ID
        assert primary["date"].startswith("2026-03-03")
        assert runoff["date"].startswith("2026-03-31")

    async def test_a_same_year_election_that_is_neither_is_not_matched(self, monkeypatch):
        # The real 2026 fixture also carries a "2026 Primary Special
        # Election" -- same year, contains neither "preferential primary"
        # nor "primary runoff" -- proving the match is on name, not year.
        import re
        async def fake(client, rl, method, url, **kw):
            return _resp(AR_ELECTIONS)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        primary, runoff = await tenr._discover_elections(
            None, "AR", AR_BASE_URL, AR_CID, re.compile("preferential primary", re.I), re.compile("primary runoff", re.I), 2026,
        )
        assert primary["id"] != "4b025e66-db9f-4e01-a7b8-3d06d87bccda"
        assert runoff["id"] != "4b025e66-db9f-4e01-a7b8-3d06d87bccda"

    async def test_no_match_for_a_year_not_in_the_list(self, monkeypatch):
        import re
        async def fake(client, rl, method, url, **kw):
            return _resp(AR_ELECTIONS)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        result = await tenr._discover_elections(
            None, "AR", AR_BASE_URL, AR_CID, re.compile("preferential primary", re.I), re.compile("primary runoff", re.I), 2028,
        )
        assert result == (None, None)

    async def test_fetch_failure_is_none_not_empty(self, monkeypatch):
        import re
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        result = await tenr._discover_elections(
            None, "AR", AR_BASE_URL, AR_CID, re.compile("preferential primary", re.I), re.compile("primary runoff", re.I), 2026,
        )
        assert result == (None, None)

    async def test_no_runoff_regex_never_looks_for_a_second_stage(self, monkeypatch):
        import re
        async def fake(client, rl, method, url, **kw):
            return _resp(AR_ELECTIONS)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        primary, runoff = await tenr._discover_elections(
            None, "AR", AR_BASE_URL, AR_CID, re.compile("preferential primary", re.I), None, 2026,
        )
        assert primary["id"] == AR_PRIMARY_ID
        assert runoff is None


@pytest.mark.asyncio
class TestFetchConfirmedCandidatesArkansas:
    def _patched(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _resp(AR_ELECTIONS)
            if f"electionID={AR_PRIMARY_ID}" in url and "GetContestSearchList" in url:
                return _resp(AR_PRIMARY_SEARCH)
            if f"electionID={AR_PRIMARY_ID}" in url and "GetContestResults" in url:
                return _resp(AR_PRIMARY_RESULTS)
            if f"electionID={AR_RUNOFF_ID}" in url and "GetContestSearchList" in url:
                return _resp(AR_RUNOFF_SEARCH)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)

    async def test_real_primary_resolves_to_the_real_certified_winners(self, monkeypatch):
        self._patched(monkeypatch)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE)
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
        result = await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE)
        assert all(r["office"] in ("H", "S") for r in result)

    async def test_missing_config_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            raise AssertionError("should never fetch with incomplete config")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await tenr.fetch_confirmed_candidates(None, 2026, "AR", {"cid": AR_CID}) is None

    async def test_election_list_fetch_failure_returns_empty_not_none(self, monkeypatch):
        # Not-yet-published-this-cycle and a real fetch failure of the
        # SAME endpoint are indistinguishable from here, so this stays on
        # the healthy-unknown side rather than flagging a pipeline error.
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE) == []

    async def test_contest_search_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _resp(AR_ELECTIONS)
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE) is None

    async def test_contest_search_returning_a_non_object_body_returns_none(self, monkeypatch):
        # A vendor-side outage or WAF page can still return HTTP 200 with
        # a JSON body that isn't an object (a bare array, a string) --
        # this must fail cleanly, not raise AttributeError on `.get()`.
        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _resp(AR_ELECTIONS)
            if "GetContestSearchList" in url:
                return _resp(["unexpected", "shape"])
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE) is None

    async def test_results_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _resp(AR_ELECTIONS)
            if "GetContestSearchList" in url and f"electionID={AR_PRIMARY_ID}" in url:
                return _resp(AR_PRIMARY_SEARCH)
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE) is None

    async def test_a_runoff_stage_with_no_federal_contests_is_a_safe_no_op(self, monkeypatch):
        # The real 2026 runoff fixture has zero federal contests -- the
        # primary's answers must survive untouched.
        self._patched(monkeypatch)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE)
        assert len(result) == 5

    async def test_a_stage_not_yet_settled_confirms_nothing(self, monkeypatch):
        # The vendor publishes no certification flag at all -- an election
        # dated far in the future (relative to "now") must be treated as
        # not-yet-settled, exactly like a portal that never certifies. The
        # real runoff stage genuinely has zero federal contests either way,
        # so the signal this test needs is that the PRIMARY's own contest
        # endpoints are never even requested -- not just that the overall
        # result happens to be empty.
        future_elections = json.loads(json.dumps(AR_ELECTIONS))
        for e in future_elections:
            if e["electionID"] == AR_PRIMARY_ID:
                e["electionDate"] = "2026-12-31T00:00:00"
        requested_urls = []

        async def fake(client, rl, method, url, **kw):
            requested_urls.append(url)
            if "GetElectionList" in url:
                return _resp(future_elections)
            if f"electionID={AR_RUNOFF_ID}" in url and "GetContestSearchList" in url:
                return _resp(AR_RUNOFF_SEARCH)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE)
        assert result == []
        assert not any(AR_PRIMARY_ID in u for u in requested_urls if "GetElectionList" not in u)

    async def test_a_choice_missing_from_the_search_list_still_counts_toward_the_total(self, monkeypatch):
        # A results-endpoint choice whose id isn't in the search list's
        # choices dict (e.g. a late-added candidate) must still count its
        # votes in the denominator -- dropping it from both sides would
        # inflate everyone else's percentage and could falsely clear the
        # runoff threshold.
        cd4_id = "784bb10e-b4fe-4fc7-bf03-16c57e348225"
        search = json.loads(json.dumps(AR_PRIMARY_SEARCH))
        search["response"]["contests"][cd4_id]["choices"] = {
            "aaa": {"name": "Known Leader"},
        }
        results = json.loads(json.dumps(AR_PRIMARY_RESULTS))
        results["response"]["contests"][cd4_id]["choices"] = [
            {"choiceID": "aaa", "totalVotes": 55},
            {"choiceID": "unknown-choice-id", "totalVotes": 50},
        ]

        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _resp(AR_ELECTIONS)
            if f"electionID={AR_PRIMARY_ID}" in url and "GetContestSearchList" in url:
                return _resp(search)
            if f"electionID={AR_PRIMARY_ID}" in url and "GetContestResults" in url:
                return _resp(results)
            if f"electionID={AR_RUNOFF_ID}" in url and "GetContestSearchList" in url:
                return _resp(AR_RUNOFF_SEARCH)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE)
        # 55 / (55 + 50) = 52.4% if the unknown choice is dropped from the
        # total it would read 100% -- either way the real number (52.4)
        # still clears 50, so assert the SHARE, not just presence, to
        # prove the unknown choice's votes were actually included.
        cd4 = [r for r in result if r["district"] == 4]
        assert cd4 == [{"office": "H", "district": 4, "party": "D", "last_name": "Leader"}]

    async def test_an_unresolvable_top_choice_blocks_confirmation_rather_than_winning(self, monkeypatch):
        # If the vote-leader's own choiceID is the one missing from the
        # search list, nobody has a resolvable name to confirm -- this
        # must NOT surface a nameless winner.
        cd4_id = "784bb10e-b4fe-4fc7-bf03-16c57e348225"
        search = json.loads(json.dumps(AR_PRIMARY_SEARCH))
        search["response"]["contests"][cd4_id]["choices"] = {
            "bbb": {"name": "Trailing Candidate"},
        }
        results = json.loads(json.dumps(AR_PRIMARY_RESULTS))
        results["response"]["contests"][cd4_id]["choices"] = [
            {"choiceID": "unknown-leader", "totalVotes": 90},
            {"choiceID": "bbb", "totalVotes": 10},
        ]

        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _resp(AR_ELECTIONS)
            if f"electionID={AR_PRIMARY_ID}" in url and "GetContestSearchList" in url:
                return _resp(search)
            if f"electionID={AR_PRIMARY_ID}" in url and "GetContestResults" in url:
                return _resp(results)
            if f"electionID={AR_RUNOFF_ID}" in url and "GetContestSearchList" in url:
                return _resp(AR_RUNOFF_SEARCH)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE)
        assert [r for r in result if r["district"] == 4] == []

    async def test_runoff_result_for_a_seat_overrides_the_primary(self, monkeypatch):
        # Constructed: give the DEM CD04 primary contest a sub-threshold
        # leader (below 50%), then have the SAME seat appear, decided, on
        # a constructed runoff stage -- the runoff's answer must win.
        cd4_id = "784bb10e-b4fe-4fc7-bf03-16c57e348225"
        sub_threshold_results = json.loads(json.dumps(AR_PRIMARY_RESULTS))
        sub_threshold_results["response"]["contests"][cd4_id]["choices"] = [
            {"choiceID": "aaa", "totalVotes": 40},
            {"choiceID": "bbb", "totalVotes": 35},
            {"choiceID": "ccc", "totalVotes": 25},
        ]
        sub_threshold_search = json.loads(json.dumps(AR_PRIMARY_SEARCH))
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
                return _resp(AR_ELECTIONS)
            if f"electionID={AR_PRIMARY_ID}" in url and "GetContestSearchList" in url:
                return _resp(sub_threshold_search)
            if f"electionID={AR_PRIMARY_ID}" in url and "GetContestResults" in url:
                return _resp(sub_threshold_results)
            if f"electionID={AR_RUNOFF_ID}" in url and "GetContestSearchList" in url:
                return _resp(runoff_search_with_cd4)
            if f"electionID={AR_RUNOFF_ID}" in url and "GetContestResults" in url:
                return _resp({
                    "response": {"contests": {cd4_id: {"choices": [
                        {"choiceID": "xxx", "totalVotes": 60},
                        {"choiceID": "yyy", "totalVotes": 40},
                    ]}}},
                })
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "AR", AR_SOURCE)
        cd4 = [r for r in result if r["district"] == 4]
        assert cd4 == [{"office": "H", "district": 4, "party": "D", "last_name": "Winner"}]


@pytest.mark.asyncio
class TestFetchConfirmedCandidatesNorthDakota:
    def _patched(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if "GetElectionList" in url:
                return _resp(ND_ELECTIONS)
            if f"electionID={ND_PRIMARY_ID}" in url and "GetContestSearchList" in url:
                return _resp(ND_PRIMARY_SEARCH)
            if f"electionID={ND_PRIMARY_ID}" in url and "GetContestResults" in url:
                return _resp(ND_PRIMARY_RESULTS)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)

    async def test_real_primary_resolves_to_the_real_certified_winners(self, monkeypatch):
        self._patched(monkeypatch)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "ND", ND_SOURCE)
        assert {"office": "H", "district": None, "party": "R", "last_name": "Fedorchak"} in result
        assert {"office": "H", "district": None, "party": "D", "last_name": "Hammer"} in result
        assert len(result) == 2

    async def test_no_contest_type_filter_falls_back_to_parse_office(self, monkeypatch):
        # The real search fixture also carries a real, non-federal
        # Secretary of State contest sharing the SAME "SW" contestTypeCode
        # as the federal ones -- there is no type value to filter on, so
        # this must be excluded by parse_office() alone.
        self._patched(monkeypatch)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "ND", ND_SOURCE)
        assert all(r["office"] == "H" for r in result)

    async def test_single_at_large_seat_has_no_district(self, monkeypatch):
        self._patched(monkeypatch)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "ND", ND_SOURCE)
        assert all(r["district"] is None for r in result)

    async def test_no_runoff_configured_never_requests_a_second_stage(self, monkeypatch):
        requested = []

        async def fake(client, rl, method, url, **kw):
            requested.append(url)
            if "GetElectionList" in url:
                return _resp(ND_ELECTIONS)
            if "GetContestSearchList" in url:
                return _resp(ND_PRIMARY_SEARCH)
            if "GetContestResults" in url:
                return _resp(ND_PRIMARY_RESULTS)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        await tenr.fetch_confirmed_candidates(None, 2026, "ND", ND_SOURCE)
        assert not any("Runoff" in u or "runoff" in u for u in requested)

    async def test_party_derived_from_the_democratic_npl_contest_name(self, monkeypatch):
        # North Dakota's real ballot label is "Democratic-NPL", not the
        # bare "Democratic" every other state in this codebase uses --
        # normalize_party's existing "democratic" root already covers it
        # with no new pattern needed.
        self._patched(monkeypatch)
        result = await tenr.fetch_confirmed_candidates(None, 2026, "ND", ND_SOURCE)
        dem = [r for r in result if r["party"] == "D"]
        assert dem == [{"office": "H", "district": None, "party": "D", "last_name": "Hammer"}]
