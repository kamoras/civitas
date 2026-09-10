"""Tests for the Google Civic confirmed-candidate strategy
(state_candidates_civic.py).

No real fixture data exists for this module to test against: Google's
election index carries nothing for the November 2026 general yet, for
any state (live-verified 2026-09-10 against production's real
GOOGLE_CIVIC_API_KEY — see the module's own docstring). The response
shapes below are constructed directly from Google's public Discovery
Document schema (the same one civic_info.py's own docstring verifies
its parsing against) — labeled honestly as constructed, not claimed as
real captured data, the same discipline KY's own test fixtures use for
their one genuinely-unverifiable branch.
"""

import pytest

from app.pipeline.fetch import state_candidates_civic as civic

_ELECTIONS_INDEX = {
    "elections": [
        {"id": "2000", "name": "VIP Test Election", "electionDay": "2031-12-06"},
        {
            "id": "9468", "name": "Delaware Primary Election", "electionDay": "2026-09-15",
            "ocdDivisionId": "ocd-division/country:us/state:de",
        },
        {
            "id": "9999", "name": "Michigan General Election", "electionDay": "2026-11-03",
            "ocdDivisionId": "ocd-division/country:us/state:mi",
        },
    ],
}

_VOTERINFO_SENATE_AND_HOUSE = {
    "election": {"id": "9999", "name": "Michigan General Election", "electionDay": "2026-11-03"},
    "contests": [
        {
            "type": "General",
            "office": "United States Senator",
            "candidates": [
                {"name": "Abdul El-Sayed", "party": "Democratic Party"},
                {"name": "Mike Rogers", "party": "Republican Party"},
                {"name": "Someone Else", "party": "Independent"},
            ],
        },
        {
            "type": "General",
            "office": "U.S. Representative",
            "district": {"name": "Congressional District 12"},
            "candidates": [
                {"name": "A House Candidate", "party": "Democratic Party"},
            ],
        },
    ],
}


class TestElectionMatching:
    def test_matches_this_states_ocd_division_and_november(self):
        matches = [
            e for e in _ELECTIONS_INDEX["elections"]
            if civic._matches_state(e, "MI") and civic._matches_november(e, 2026)
        ]
        assert [e["id"] for e in matches] == ["9999"]

    def test_a_bare_country_level_division_also_matches(self):
        national = {"ocdDivisionId": "ocd-division/country:us", "electionDay": "2026-11-03"}
        assert civic._matches_state(national, "MI") is True

    def test_a_different_states_division_does_not_match(self):
        assert civic._matches_state(_ELECTIONS_INDEX["elections"][1], "MI") is False

    def test_a_non_november_date_does_not_match(self):
        assert civic._matches_november(_ELECTIONS_INDEX["elections"][1], 2026) is False

    def test_the_permanent_test_election_never_matches_a_real_year(self):
        assert civic._matches_november(_ELECTIONS_INDEX["elections"][0], 2026) is False


class TestParseContests:
    def test_senate_contest_resolves_with_all_candidates_including_independent(self):
        """The independent candidate's party doesn't normalize -- kept
        anyway with party=None, deliberately unlike every vote-counting
        strategy elsewhere in this system (see module docstring)."""
        results: list[dict] = []
        for contest in civic._parse_contests(_VOTERINFO_SENATE_AND_HOUSE):
            if contest.get("kind") != "contest":
                continue
            office_district = civic.parse_office(contest.get("office") or "")
            if office_district != ("S", None):
                continue
            for cand in contest.get("candidates") or []:
                results.append({
                    "party": civic.normalize_party(cand.get("party") or ""),
                    "last_name": civic.surname(cand.get("name") or ""),
                })
        assert {"party": "D", "last_name": "El-Sayed"} in results
        assert {"party": "R", "last_name": "Rogers"} in results
        assert {"party": None, "last_name": "Else"} in results
        assert len(results) == 3

    def test_house_contest_at_the_same_address_is_out_of_scope(self):
        """A bare 'U.S. Representative' office label carries no district
        number of its own -- parse_office correctly can't place it as a
        House seat from the label alone (see module docstring on why
        this module never reads the separate district field either), so
        it resolves to neither ("S", None) nor a usable House id and is
        excluded by the Senate-only filter, not silently mis-tagged."""
        house_contest = _VOTERINFO_SENATE_AND_HOUSE["contests"][1]
        office_district = civic.parse_office(house_contest.get("office") or "")
        assert office_district != ("S", None)


class TestFetchConfirmedCandidates:
    def _patch(self, monkeypatch, elections=None, voterinfo=None):
        async def fake_get_json(client, url, params, label):
            if url.endswith("/elections"):
                return elections
            return voterinfo

        monkeypatch.setattr(civic, "_get_json", fake_get_json)
        monkeypatch.setattr(civic.settings, "GOOGLE_CIVIC_API_KEY", "test-key")

    @pytest.mark.asyncio
    async def test_returns_none_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(civic.settings, "GOOGLE_CIVIC_API_KEY", "")
        assert await civic.fetch_confirmed_candidates(None, 2026, "MI", {"address": "x"}) is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_address_configured(self, monkeypatch):
        monkeypatch.setattr(civic.settings, "GOOGLE_CIVIC_API_KEY", "test-key")
        assert await civic.fetch_confirmed_candidates(None, 2026, "MI", {}) is None

    @pytest.mark.asyncio
    async def test_no_matching_election_yet_is_a_healthy_empty_list(self, monkeypatch):
        """Today's actual live reality for every state -- must read as
        healthy (ok, 0 confirmed), never fetch_failed."""
        self._patch(monkeypatch, elections=_ELECTIONS_INDEX)
        result = await civic.fetch_confirmed_candidates(None, 2026, "WI", {"address": "x"})
        assert result == []

    @pytest.mark.asyncio
    async def test_elections_index_fetch_failure_is_not_silently_healthy(self, monkeypatch):
        """Regression for the sentinel bug: a genuine elections-index
        fetch failure must return None (fetch_failed), never the same []
        a real "not listed yet" answer produces -- collapsing the two
        would report every night's real outage as a healthy empty run."""
        self._patch(monkeypatch, elections=None)
        result = await civic.fetch_confirmed_candidates(None, 2026, "MI", {"address": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_elections_payload_is_also_a_failure(self, monkeypatch):
        self._patch(monkeypatch, elections={"elections": "not-a-list"})
        result = await civic.fetch_confirmed_candidates(None, 2026, "MI", {"address": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_matched_election_with_no_contests_yet_is_healthy_empty(self, monkeypatch):
        """Real, directly-observed shape: Delaware's real, currently-
        listed primary returns HTTP 200 with no `contests` key at all,
        5 days before the real election."""
        self._patch(monkeypatch, elections=_ELECTIONS_INDEX, voterinfo={"election": {"id": "9999"}})
        result = await civic.fetch_confirmed_candidates(None, 2026, "MI", {"address": "x"})
        assert result == []

    @pytest.mark.asyncio
    async def test_voterinfo_fetch_failure_after_a_real_election_match_is_not_healthy(self, monkeypatch):
        self._patch(monkeypatch, elections=_ELECTIONS_INDEX, voterinfo=None)
        result = await civic.fetch_confirmed_candidates(None, 2026, "MI", {"address": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_confirms_the_real_senate_candidates_and_excludes_the_house_one(self, monkeypatch):
        self._patch(monkeypatch, elections=_ELECTIONS_INDEX, voterinfo=_VOTERINFO_SENATE_AND_HOUSE)
        result = await civic.fetch_confirmed_candidates(None, 2026, "MI", {"address": "x"})
        assert result is not None
        by_name = {r["last_name"]: r for r in result}
        assert by_name["El-Sayed"] == {"office": "S", "district": None, "party": "D", "last_name": "El-Sayed"}
        assert by_name["Rogers"]["party"] == "R"
        assert by_name["Else"]["party"] is None  # independent, kept anyway
        assert "Candidate" not in by_name  # the House contest's own candidate never appears
        assert len(result) == 3
