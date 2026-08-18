"""Tests for Pennsylvania's own returns API (state_candidates_pa.py).

Payloads are the real response shape, reduced: everything arrives
double-encoded (a JSON string whose contents are JSON), elections carry a
TYPE code rather than a parseable name, and results nest district -> party
-> candidates.
"""

import json

import pytest

from app.pipeline.fetch import state_candidates_pa as pa

_ELECTIONS = [{"ElectionData": json.dumps([
    {"Electionid": "116", "ElectionType": "S", "ElectionName": "2026 Special Election",
     "ElectionYear": "2026", "ElectionDate": "03/17/2026"},
    {"Electionid": "117", "ElectionType": "P", "ElectionName": "2026 General Primary",
     "ElectionYear": "2026", "ElectionDate": "05/19/2026"},
    {"Electionid": "120", "ElectionType": "G", "ElectionName": "2026 General Election",
     "ElectionYear": "2026", "ElectionDate": "11/03/2026"},
    {"Electionid": "104", "ElectionType": "P", "ElectionName": "2024 General Primary",
     "ElectionYear": "2024", "ElectionDate": "04/23/2024"},
])}]
_OFFICES = {"Table": [
    {"OfficeID": 3, "OfficeCode": "GOV", "OfficeName": "Governor"},
    {"OfficeID": 11, "OfficeCode": "USC", "OfficeName": "Representative in Congress"},
    {"OfficeID": 13, "OfficeCode": "STH", "OfficeName": "Representative in the General Assembly"},
]}
_RESULTS = {"Election": {"Representative in Congress": [
    {"1st Congressional District$$2$$": [{
        "DistrictId": "2", "District": "1st Congressional District",
        "Candidates": [
            {"Democratic": [
                {"CandidateName": "BOB HARVIE", "Votes": "52094"},
                {"CandidateName": "ANOTHER PERSON", "Votes": "27000"},
            ]},
            {"Republican": [{"CandidateName": "BRIAN FITZPATRICK", "Votes": "60000"}]},
        ],
    }]},
]}}


def _serve(monkeypatch, results=_RESULTS):
    async def fake_get(client, url, label):
        if "GetAllElections" in url:
            return _ELECTIONS
        if "GetOfficeNames" in url:
            return _OFFICES
        return results

    monkeypatch.setattr(pa, "_get", fake_get)


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    async def test_returns_one_nominee_per_party(self, monkeypatch):
        _serve(monkeypatch)
        records = await pa.fetch_confirmed_candidates(None, 2026, "PA", {})
        assert sorted((r["party"], r["last_name"]) for r in records) == [
            ("D", "HARVIE"), ("R", "FITZPATRICK"),
        ]
        assert all(r["office"] == "H" and r["district"] == 1 for r in records)

    async def test_matches_the_election_by_TYPE_not_by_name(self, monkeypatch):
        """The id changes every cycle and the name is free text; the code
        is what stays true. A general or a special must never be read as
        the primary."""
        seen = {}

        async def fake_get(client, url, label):
            if "GetAllElections" in url:
                return _ELECTIONS
            if "GetOfficeNames" in url:
                seen["url"] = url
                return _OFFICES
            return _RESULTS

        monkeypatch.setattr(pa, "_get", fake_get)
        await pa.fetch_confirmed_candidates(None, 2026, "PA", {})
        assert "electionid=117" in seen["url"]

    async def test_only_federal_offices_are_read(self, monkeypatch):
        """Pennsylvania's own General Assembly is in the same list, and
        its "Representative in the General Assembly" must never be taken
        for a seat in Congress."""
        asked = []

        async def fake_get(client, url, label):
            if "GetAllElections" in url:
                return _ELECTIONS
            if "GetOfficeNames" in url:
                return _OFFICES
            asked.append(url)
            return _RESULTS

        monkeypatch.setattr(pa, "_get", fake_get)
        await pa.fetch_confirmed_candidates(None, 2026, "PA", {})
        assert len(asked) == 1 and "officeId=11" in asked[0]

    async def test_a_cycle_with_no_primary_yields_none(self, monkeypatch):
        _serve(monkeypatch)
        assert await pa.fetch_confirmed_candidates(None, 2030, "PA", {}) is None

    async def test_a_failed_office_read_is_a_failure_not_an_empty_answer(self, monkeypatch):
        async def fake_get(client, url, label):
            if "GetAllElections" in url:
                return _ELECTIONS
            if "GetOfficeNames" in url:
                return _OFFICES
            return None

        monkeypatch.setattr(pa, "_get", fake_get)
        assert await pa.fetch_confirmed_candidates(None, 2026, "PA", {}) is None

    async def test_a_runoff_threshold_is_honoured_if_a_state_ever_needs_one(
        self, monkeypatch,
    ):
        """Pennsylvania nominates on a plurality, but the rule is read
        from config rather than assumed — the same discipline every other
        adapter follows."""
        _serve(monkeypatch)
        records = await pa.fetch_confirmed_candidates(
            None, 2026, "PA", {"runoff_threshold_pct": 70.0},
        )
        assert [r["last_name"] for r in records] == ["FITZPATRICK"]
