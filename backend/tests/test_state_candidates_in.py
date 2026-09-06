"""Tests for Indiana's confirmed-general-candidate strategy
(state_candidates_in.py).

fixtures_in_manifest.json and fixtures_in_house_races.json are REAL —
trimmed JSON responses from enr.indianavoters.in.gov, fetched live
2026-09-03 from the real, certified 2026 primary. The manifest carries
exactly one Federal category ("US Representative", id 1005) and no
Senate category, matching the real 2026 ballot (no Indiana Senate seat
this cycle). The races fixture carries 3 real districts: the First
(a clean 2-winner race, and its real SubSortOrder — "...(1) District"
— exercises the code-based district match), the Fourth (11 real
candidates, most losing, to confirm only the real plurality winner per
party survives), and the Seventh (a real case where the Democratic
incumbent, André Carson, includes a non-ASCII character in his own
name).

The tie-refusal safety branch (two same-party candidates both with the
top vote count) is NOT exercised by any real district here, so it is
tested with a small CONSTRUCTED race dict instead of a fixture.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import http_utils, state_candidates_in as ind

MANIFEST = json.loads((Path(__file__).parent / "fixtures_in_manifest.json").read_text())
RACES = json.loads((Path(__file__).parent / "fixtures_in_house_races.json").read_text())


class TestOfficeAndDistrict:
    def test_house_district_by_ordinal_word(self):
        assert ind._office_and_district("United States Representative, First District") == ("H", 1)
        assert ind._office_and_district("United States Representative, Ninth District") == ("H", 9)

    def test_house_district_prefers_the_real_sub_sort_order_number(self):
        # Real 2026 field: OFFICE_TITLE says "First District" (ordinal
        # word) while SubSortOrder says "(1) District" (evergreen
        # number) for the SAME race -- the number wins.
        assert ind._office_and_district(
            "United States Representative, First District",
            "United States Representative, (1) District",
        ) == ("H", 1)

    def test_senate_has_no_district(self):
        assert ind._office_and_district("United States Senator") == ("S", None)

    def test_non_federal_title_is_none(self):
        assert ind._office_and_district("State Representative, District 001") is None

    def test_unrecognized_ordinal_is_none(self):
        # Bounded to the ordinals this module actually knows, rather than
        # open-ended -- an unrecognized word must never be silently
        # coerced into a district number, and SubSortOrder isn't there
        # to bail it out either.
        assert ind._office_and_district("United States Representative, Nowhereth District") is None


class TestCandidates:
    def test_single_candidate_dict_becomes_a_list(self):
        race = {"Candidates": {"Candidate": {"NAME": "solo"}}}
        assert ind._candidates(race) == [{"NAME": "solo"}]

    def test_missing_candidates_key_is_empty(self):
        assert ind._candidates({}) == []

    def test_null_candidates_value_is_empty_not_a_crash(self):
        # A real API can legitimately serialize an absent value as a
        # JSON null rather than omitting the key -- .get(..., {}) alone
        # would return None here and crash on the next .get() call.
        assert ind._candidates({"Candidates": None}) == []


class TestRaceResults:
    def test_a_genuine_tie_confirms_nobody_for_that_party(self):
        # Constructed: two real-shaped Republican candidates tied at
        # the top vote count. Indiana's own isWinner flag has no
        # visible tie-handling of its own, so this must be refused via
        # the same shared pick_nominee every other strategy uses --
        # not silently confirm both.
        race = {"Candidates": {"Candidate": [
            {"PARTY": "R", "CandidateName": "Alpha, A.", "TOTAL": 500, "isWinner": "T"},
            {"PARTY": "R", "CandidateName": "Beta, B.", "TOTAL": 500, "isWinner": "T"},
            {"PARTY": "D", "CandidateName": "Gamma, G.", "TOTAL": 900, "isWinner": "T"},
        ]}}
        results = ind._race_results(race, "H", 1)
        assert results == [{"office": "H", "district": 1, "party": "D", "last_name": "Gamma"}]


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    def _patched(self, monkeypatch, *, certified="T", version="A", manifest=None, races=None):
        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            if url.endswith("settings.json"):
                body = {"Root": {"Certified": certified, "VersionType": version}}
            elif "statewideElectionsC" in url:
                body = manifest if manifest is not None else MANIFEST
            elif "OffCatC_1005" in url:
                body = races if races is not None else RACES
            else:
                return None
            return SimpleNamespace(json=lambda: body)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake_fetch_with_retry)

    async def test_real_districts_resolve_to_real_winners(self, monkeypatch):
        self._patched(monkeypatch)
        result = await ind.fetch_confirmed_candidates(None, 2026, "IN", {})
        assert {"office": "H", "district": 1, "party": "D", "last_name": "Mrvan"} in result
        assert {"office": "H", "district": 1, "party": "R", "last_name": "Regnitz"} in result
        assert {"office": "H", "district": 4, "party": "R", "last_name": "Baird"} in result
        assert {"office": "H", "district": 4, "party": "D", "last_name": "Cox"} in result

    async def test_losing_candidates_in_a_crowded_field_are_excluded(self, monkeypatch):
        # District 4's real field has 11 candidates; only 2 (one per
        # party) actually won their party's primary.
        self._patched(monkeypatch)
        result = await ind.fetch_confirmed_candidates(None, 2026, "IN", {})
        district4 = [r for r in result if r["district"] == 4]
        assert len(district4) == 2

    async def test_non_ascii_name_is_handled(self, monkeypatch):
        self._patched(monkeypatch)
        result = await ind.fetch_confirmed_candidates(None, 2026, "IN", {})
        assert {"office": "H", "district": 7, "party": "D", "last_name": "Carson"} in result

    async def test_settings_fetch_failure_returns_none(self, monkeypatch):
        async def fake(*a, **kw):
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await ind.fetch_confirmed_candidates(None, 2026, "IN", {}) is None

    async def test_not_yet_certified_returns_empty_list(self, monkeypatch):
        self._patched(monkeypatch, certified="F")
        assert await ind.fetch_confirmed_candidates(None, 2026, "IN", {}) == []

    async def test_manifest_with_no_federal_category_returns_none(self, monkeypatch):
        self._patched(monkeypatch, manifest={"Root": {"List": [{"Heading": "State", "Items": {"Item": []}}]}})
        assert await ind.fetch_confirmed_candidates(None, 2026, "IN", {}) is None

    async def test_category_fetch_failure_returns_none(self, monkeypatch):
        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            if url.endswith("settings.json"):
                return SimpleNamespace(json=lambda: {"Root": {"Certified": "T", "VersionType": "A"}})
            if "statewideElectionsC" in url:
                return SimpleNamespace(json=lambda: MANIFEST)
            return None  # the OffCatC fetch fails

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake_fetch_with_retry)
        assert await ind.fetch_confirmed_candidates(None, 2026, "IN", {}) is None
