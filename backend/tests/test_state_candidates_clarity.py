"""Tests for the shared Clarity ENR adapter (state_candidates_clarity.py).

The summary fixture is a real cut of Colorado's official 2026 primary feed
(fetched live 2026-08-12), including the CO-01 Democratic contest whose
real-world outcome — Melat Kiros unseating 15-term incumbent Diana DeGette
— is the ground truth this adapter is verified against, plus a Governor
contest as a control that must NOT be picked up as federal.
"""

import json
import os

import pytest

from app.pipeline.fetch import state_candidates_clarity as cl

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures_co_clarity_summary.json")


def _contests() -> list[dict]:
    with open(_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)["Contests"]


def _contest(needle: str) -> dict:
    return next(c for c in _contests() if needle in c["C"])


class TestParseOffice:
    def test_senate(self):
        assert cl._parse_office("United States Senator - Democratic Party") == ("S", None)

    def test_house_with_district(self):
        assert cl._parse_office(
            "Representative to the 120th United States Congress - District 7 - Democratic Party"
        ) == ("H", 7)

    def test_house_at_large_has_no_district(self):
        """AK/DE/MT/ND/SD/VT/WY print no district number; FEC models those
        as district 0, which _race_id_for already falls back to."""
        assert cl._parse_office(
            "Representative to the 120th United States Congress - Republican Party"
        ) == ("H", None)

    def test_ordinal_advances_between_congresses(self):
        """The "120th" is not load-bearing — the same label in the next
        cycle must still parse, or the adapter silently dies every 2 years."""
        assert cl._parse_office(
            "Representative to the 121st United States Congress - District 2 - Democratic Party"
        ) == ("H", 2)

    def test_non_federal_contest_is_not_guessed_into_a_federal_one(self):
        assert cl._parse_office("Governor - Democratic Party") is None
        assert cl._parse_office("State Senate District 4 - Republican Party") is None


class TestParseParty:
    def test_maps_spelled_out_party_to_the_states_own_code(self):
        assert cl._parse_party("United States Senator - Democratic Party") == "D"
        assert cl._parse_party("... - Republican Party") == "R"
        assert cl._parse_party("... - Libertarian Party") == "L"

    def test_unrecognised_party_is_skipped_not_defaulted(self):
        assert cl._parse_party("United States Senator - Nonpartisan") is None


class TestSurname:
    def test_takes_the_trailing_token(self):
        assert cl._surname("Melat Kiros") == "Kiros"
        assert cl._surname("Dwayne L. Romero") == "Romero"

    def test_drops_a_generational_suffix(self):
        assert cl._surname("Robert Cruz Jr.") == "Cruz"
        assert cl._surname("Harold Ford III") == "Ford"

    def test_blank_name_yields_none(self):
        assert cl._surname("   ") is None


class TestNominee:
    def test_picks_the_real_winner_from_the_live_co01_contest(self):
        """Ground truth: Kiros beat DeGette on 2026-06-30."""
        won = cl._nominee(_contest("District 1 - Democratic"), None)
        assert won is not None and won[0] == "Melat Kiros"

    def test_unopposed_candidate_wins(self):
        won = cl._nominee(_contest("District 4 - Republican"), None)
        assert won is not None and won[0] == "Lauren Boebert"

    def test_runoff_state_withholds_a_sub_threshold_leader(self):
        """A 53% plurality leader is the nominee in Colorado but is headed
        to a runoff in a majority-required state — that must yield nothing
        rather than mislabel them as the confirmed nominee."""
        co01 = _contest("District 1 - Democratic")
        assert cl._nominee(co01, None) is not None
        assert cl._nominee(co01, 60.0) is None

    def test_exact_tie_has_no_winner(self):
        tied = {"CH": ["A", "B"], "V": [500, 500], "PCT": [50.0, 50.0]}
        assert cl._nominee(tied, None) is None

    def test_no_votes_cast_yet_yields_nothing(self):
        assert cl._nominee({"CH": ["A"], "V": [0], "PCT": [0.0]}, None) is None

    def test_malformed_contest_yields_nothing_rather_than_raising(self):
        assert cl._nominee({"CH": ["A", "B"], "V": [1], "PCT": []}, None) is None
        assert cl._nominee({}, None) is None


class TestIsPrimary:
    def test_matches_the_cycles_regular_primary(self):
        assert cl._is_primary(
            {"ElectionName": "2026 Primary", "Date": "6/30/2026 12:00:00 AM"}, 2026,
        ) is True

    def test_rejects_another_years_primary(self):
        assert cl._is_primary(
            {"ElectionName": "2024 Primary", "Date": "6/25/2024 12:00:00 AM"}, 2026,
        ) is False

    def test_rejects_the_separate_presidential_primary(self):
        assert cl._is_primary(
            {"ElectionName": "2026 Presidential Primary", "Date": "3/3/2026 12:00:00 AM"}, 2026,
        ) is False

    def test_rejects_a_general_election(self):
        assert cl._is_primary(
            {"ElectionName": "2026 General", "Date": "11/3/2026 12:00:00 AM"}, 2026,
        ) is False


class TestFetchConfirmedCandidates:
    """End-to-end over the real fixture, with the three Clarity HTTP hops
    stubbed — proves the whole parse produces exactly the federal nominees
    and nothing else."""

    @pytest.mark.asyncio
    async def test_returns_only_federal_nominees(self, monkeypatch):
        async def fake_get(client, url, label):
            if url.endswith("elections.json"):
                return _Resp(json_body=[
                    {"EID": "126592", "ElectionName": "2026 Primary",
                     "Date": "6/30/2026 12:00:00 AM"},
                ])
            if url.endswith("current_ver.txt"):
                return _Resp(text="377440")
            return _Resp(json_body={"Contests": _contests()})

        monkeypatch.setattr(cl, "_get", fake_get)
        records = await cl.fetch_confirmed_candidates(None, 2026, "CO", {})

        assert {r["last_name"] for r in records} == {"Hickenlooper", "Kiros", "Boebert"}
        # The Governor contest in the same feed must not appear.
        assert all(r["office"] in ("S", "H") for r in records)
        assert {"office": "H", "district": 1, "party": "D", "last_name": "Kiros"} in records

    @pytest.mark.asyncio
    async def test_missing_primary_returns_none_not_empty(self, monkeypatch):
        """None means "couldn't check" and leaves the FEC list alone; [] would
        mean "checked, nobody is confirmed" — a very different claim."""
        async def fake_get(client, url, label):
            if url.endswith("elections.json"):
                return _Resp(json_body=[
                    {"EID": "1", "ElectionName": "2024 Primary",
                     "Date": "6/25/2024 12:00:00 AM"},
                ])
            return _Resp(text="x")

        monkeypatch.setattr(cl, "_get", fake_get)
        assert await cl.fetch_confirmed_candidates(None, 2026, "CO", {}) is None

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake_get(client, url, label):
            return None

        monkeypatch.setattr(cl, "_get", fake_get)
        assert await cl.fetch_confirmed_candidates(None, 2026, "CO", {}) is None

    @pytest.mark.asyncio
    async def test_garbage_version_is_rejected(self, monkeypatch):
        """current_ver.txt must be a version id — an error page here would
        otherwise be pasted straight into the results URL."""
        async def fake_get(client, url, label):
            if url.endswith("elections.json"):
                return _Resp(json_body=[
                    {"EID": "126592", "ElectionName": "2026 Primary",
                     "Date": "6/30/2026 12:00:00 AM"},
                ])
            return _Resp(text="<html>Not Found</html>")

        monkeypatch.setattr(cl, "_get", fake_get)
        assert await cl.fetch_confirmed_candidates(None, 2026, "CO", {}) is None


class _Resp:
    def __init__(self, json_body=None, text=""):
        self._json = json_body
        self.text = text

    def json(self):
        return self._json
