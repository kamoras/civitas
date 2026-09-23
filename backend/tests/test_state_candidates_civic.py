"""Tests for the Google Civic confirmed-candidate strategy
(state_candidates_civic.py).

No real fixture data exists for this module to test against. Google's
election index DOES now carry the November 2026 general (live-verified
2026-09-23 against production's real GOOGLE_CIVIC_API_KEY: one
country-level entry, id 12000), but no address has real contest data
behind it yet — 71 of the 80 configured addresses answer HTTP 404 "No
information for this address", and the one that answers 200 carries no
`contests` key. See the module's own docstring. The response
shapes below are constructed directly from Google's public Discovery
Document schema (the same one civic_info.py's own docstring verifies
its parsing against) — labeled honestly as constructed, not claimed as
real captured data, the same discipline KY's own test fixtures use for
their one genuinely-unverifiable branch.
"""

import httpx
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
            "district": {"id": "12", "name": "Congressional District 12", "scope": "congressional"},
            "candidates": [
                {"name": "A House Candidate", "party": "Democratic Party"},
            ],
        },
    ],
}

# A real house_addresses query response: the district field's own real
# schema (id/name/scope — see civic_info.py's docstring) is the only
# source of the district number this module trusts, matching the shape
# a house_addresses-configured district 12 query would really return.
_VOTERINFO_HOUSE_DISTRICT_12 = {
    "election": {"id": "9999", "name": "Michigan General Election", "electionDay": "2026-11-03"},
    "contests": [
        {
            "type": "General",
            "office": "U.S. Representative",
            "district": {"id": "12", "name": "Congressional District 12", "scope": "congressional"},
            "candidates": [
                {"name": "Rashida Tlaib", "party": "Democratic Party"},
                {"name": "A Republican Candidate", "party": "Republican Party"},
            ],
        },
    ],
}

# The real failure mode this module's own docstring documents: an
# address configured for district 9 that actually resolves to district
# 10 (Rep. McClain's real Shelby Township office, live-verified) — the
# contest returned is for the WRONG district and must be refused.
_VOTERINFO_HOUSE_WRONG_DISTRICT = {
    "election": {"id": "9999", "name": "Michigan General Election", "electionDay": "2026-11-03"},
    "contests": [
        {
            "type": "General",
            "office": "U.S. Representative",
            "district": {"id": "10", "name": "Congressional District 10", "scope": "congressional"},
            "candidates": [{"name": "John James", "party": "Republican Party"}],
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

    def test_house_contest_at_the_senate_address_is_out_of_scope(self):
        """A House contest that happens to also appear when querying the
        statewide Senate address is real (Civic returns every contest
        for that precinct, not just Senate) but out of scope for THIS
        query -- fetch_confirmed_candidates only asks house_addresses
        entries about House races, never the Senate address, regardless
        of whether the contest itself carries a real, schema-valid
        district id (see TestHouseAddresses for that path)."""
        house_contest = _VOTERINFO_SENATE_AND_HOUSE["contests"][1]
        office_district = civic.parse_office(house_contest.get("office") or "")
        assert office_district != ("S", None)


class TestFetchConfirmedCandidates:
    def _patch(self, monkeypatch, elections=None, voterinfo=None):
        async def fake_get_json(client, url, params, label, expected_statuses=()):
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


class TestHouseAddresses:
    """house_addresses is optional -- a state with none configured
    (every existing test above) gets Senate-only confirmation exactly
    as before. These tests are the only ones that set it."""

    def _patch(self, monkeypatch, voterinfo_by_address: dict[str, dict]):
        async def fake_get_json(client, url, params, label, expected_statuses=()):
            if url.endswith("/elections"):
                return _ELECTIONS_INDEX
            return voterinfo_by_address.get(params.get("address"))

        monkeypatch.setattr(civic, "_get_json", fake_get_json)
        monkeypatch.setattr(civic.settings, "GOOGLE_CIVIC_API_KEY", "test-key")

    @pytest.mark.asyncio
    async def test_confirms_a_house_district_whose_reported_id_matches(self, monkeypatch):
        self._patch(monkeypatch, {
            "capitol": _VOTERINFO_SENATE_AND_HOUSE,
            "district-12-address": _VOTERINFO_HOUSE_DISTRICT_12,
        })
        result = await civic.fetch_confirmed_candidates(
            None, 2026, "MI", {"address": "capitol", "house_addresses": {"12": "district-12-address"}},
        )
        assert result is not None
        by_seat = {(r["office"], r["district"], r["party"]): r["last_name"] for r in result}
        assert by_seat[("H", 12, "D")] == "Tlaib"
        assert by_seat[("H", 12, "R")] == "Candidate"
        assert by_seat[("S", None, "D")] == "El-Sayed"  # Senate still confirmed from the capitol address

    @pytest.mark.asyncio
    async def test_refuses_a_house_district_whose_reported_id_does_not_match(self, monkeypatch):
        """Real regression case from this module's own docstring: an
        address configured for district 9 (Rep. McClain's real Shelby
        Township office) actually resolves to district 10. Must never
        confirm a candidate under the WRONG district number — refuse
        the whole contest instead."""
        self._patch(monkeypatch, {
            "capitol": {"election": {"id": "9999"}},  # no Senate contest this run
            "shelby-township-address": _VOTERINFO_HOUSE_WRONG_DISTRICT,
        })
        result = await civic.fetch_confirmed_candidates(
            None, 2026, "MI", {"address": "capitol", "house_addresses": {"9": "shelby-township-address"}},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_refuses_a_house_contest_with_no_congressional_scope_district(self, monkeypatch):
        """Never falls back to trusting the office label's own free text
        when the schema-guaranteed district field is missing or isn't
        scoped "congressional" — refuses rather than guessing."""
        no_district_info = {
            "election": {"id": "9999"},
            "contests": [{
                "type": "General", "office": "U.S. Representative",
                "candidates": [{"name": "Someone", "party": "Democratic Party"}],
            }],
        }
        self._patch(monkeypatch, {
            "capitol": {"election": {"id": "9999"}},
            "district-1-address": no_district_info,
        })
        result = await civic.fetch_confirmed_candidates(
            None, 2026, "MI", {"address": "capitol", "house_addresses": {"1": "district-1-address"}},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_a_non_numeric_house_addresses_key_is_skipped_not_fatal(self, monkeypatch):
        self._patch(monkeypatch, {
            "capitol": {"election": {"id": "9999"}},
            "bad-address": _VOTERINFO_HOUSE_DISTRICT_12,
        })
        result = await civic.fetch_confirmed_candidates(
            None, 2026, "MI", {"address": "capitol", "house_addresses": {"at-large": "bad-address"}},
        )
        assert result == []  # skipped, not a fetch_failed

    @pytest.mark.asyncio
    async def test_a_house_address_fetch_failure_fails_the_whole_run(self, monkeypatch):
        self._patch(monkeypatch, {"capitol": {"election": {"id": "9999"}}})  # district-1-address -> None
        result = await civic.fetch_confirmed_candidates(
            None, 2026, "MI", {"address": "capitol", "house_addresses": {"1": "district-1-address"}},
        )
        assert result is None


class TestConfiguredAddressesAreParseable:
    """Google's voterInfoQuery rejects an address with no numeric street
    number outright — HTTP 400 "Failed to parse address", not an empty
    result — so such an entry is a permanent dead end for that state, not
    a slow day.

    It is invisible until Google publishes real data for the election,
    because every address returns an error either way until then. Two of
    NY's addresses were in exactly that state (the statewide "State St,
    Albany, NY 12224" and district 17's "One Blue Hill Plaza"), which
    would have left the largest of these states the one still broken on
    the day the rest started working. Checked here instead, where adding
    a bad address fails immediately and offline.
    """

    def _entries(self):
        import json
        import pathlib

        path = (pathlib.Path(__file__).resolve().parent.parent
                / "app" / "data" / "state_candidate_sources.json")
        states = json.loads(path.read_text())["states"]
        for code, entry in states.items():
            if entry.get("strategy") != "google_civic":
                continue
            if entry.get("address"):
                yield f"{code} statewide", entry["address"]
            for district, addr in (entry.get("house_addresses") or {}).items():
                yield f"{code}-{district}", addr

    def test_every_address_starts_with_a_street_number(self):
        bad = [(label, addr) for label, addr in self._entries()
               if not addr.strip()[:1].isdigit()]
        assert bad == [], (
            "Google 400s these — spell the street number as a digit "
            f"('One Blue Hill Plaza' -> '1 Blue Hill Plaza'): {bad}")

    def test_there_is_something_to_check(self):
        """Guards the guard: a renamed strategy or moved key would make
        the assertion above pass over an empty list forever."""
        assert len(list(self._entries())) > 50


class TestAddressWithNoPublishedBallot:
    """Google answers HTTP 404 "No information for this address" for a
    precinct whose ballot it has not published yet. That is its normal
    answer, not a failure -- and because the shared retry path treats
    any 4xx as transient, it read as one: on 2026-09-23, the morning
    after Google first listed the November 3 general at all, 8 states
    reported fetch_failed (3 attempts each) instead of a healthy empty
    night. 71 of the 80 configured addresses answered 404 that morning,
    every state capitol among them.

    These exercise the REAL fetch_with_retry, not a patched _get_json,
    because the bug lived in the status handling between them."""

    def _patch(self, monkeypatch, voterinfo_status: int):
        async def fake_fetch(client, limiter, method, url, **kwargs):
            if url.endswith("/elections"):
                return httpx.Response(200, json=_ELECTIONS_INDEX)
            if voterinfo_status not in kwargs.get("expected_statuses", ()):
                return None  # what the retry path does with an unexpected status
            return httpx.Response(voterinfo_status, json={"error": {"message": "x"}})

        monkeypatch.setattr(civic, "fetch_with_retry", fake_fetch)
        monkeypatch.setattr(civic.settings, "GOOGLE_CIVIC_API_KEY", "test-key")

    @pytest.mark.asyncio
    async def test_a_404_for_the_address_is_a_healthy_empty_night(self, monkeypatch):
        self._patch(monkeypatch, 404)
        assert await civic.fetch_confirmed_candidates(None, 2026, "MI", {"address": "capitol"}) == []

    @pytest.mark.asyncio
    async def test_every_house_address_404ing_is_still_healthy(self, monkeypatch):
        """The statewide 404 must not short-circuit the state into a
        failure before its districts are even tried."""
        self._patch(monkeypatch, 404)
        result = await civic.fetch_confirmed_candidates(
            None, 2026, "MI",
            {"address": "capitol", "house_addresses": {"1": "a", "2": "b"}},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_a_genuine_failure_is_still_not_healthy(self, monkeypatch):
        """The other half of the same line: 404 reading as [] must not
        drag a real fetch failure along with it."""
        self._patch(monkeypatch, 500)
        assert await civic.fetch_confirmed_candidates(None, 2026, "MI", {"address": "capitol"}) is None
