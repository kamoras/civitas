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


# ── State offices (statewide_offices) ────────────────────────────────
#
# Reduced from the real 2026 primary (election 117), read 2026-09-28:
# the office list is the real one (codes, ids and names), and every name
# and vote total below is the real published figure. The district keys
# carry the API's own "$$<id>$$" suffix.
_STATE_OFFICES = {"Table": [
    {"OfficeID": 3, "OfficeCode": "GOV", "OfficeName": "Governor"},
    {"OfficeID": 4, "OfficeCode": "LTG", "OfficeName": "Lieutenant Governor"},
    {"OfficeID": 11, "OfficeCode": "USC", "OfficeName": "Representative in Congress"},
    {"OfficeID": 12, "OfficeCode": "STS", "OfficeName": "Senator in the General Assembly"},
    {"OfficeID": 13, "OfficeCode": "STH", "OfficeName": "Representative in the General Assembly"},
    {"OfficeID": 21, "OfficeCode": "DSC", "OfficeName": "Member of Democratic State Committee"},
    {"OfficeID": 22, "OfficeCode": "RSC", "OfficeName": "Member of Republican State Committee"},
]}


def _office(name, districts):
    return {"Election": {name: [{
        f"{label}$${i}$$": [{"DistrictId": str(i), "District": label, "Candidates": parties}]
        for i, (label, parties) in enumerate(districts, start=1)
    }]}}


_STATE_RESULTS = {
    3: _office("Governor", [("Statewide", [
        {"Democratic": [{"CandidateName": "JOSH SHAPIRO", "Votes": "1116960"}]},
        {"Republican": [{"CandidateName": "STACY GARRITY", "Votes": "641534"}]},
    ])]),
    4: _office("Lieutenant Governor", [("Statewide", [
        {"Democratic": [{"CandidateName": "AUSTIN DAVIS", "Votes": "1072694"}]},
        {"Republican": [
            {"CandidateName": "JOHN VENTRE", "Votes": "225238"},
            {"CandidateName": "JASON RICHEY", "Votes": "428740"},
        ]},
    ])]),
    11: _RESULTS,
    12: _office("Senator in the General Assembly", [
        ("4th Senatorial District", [
            {"Democratic": [
                {"CandidateName": "ART HAYWOOD", "Votes": "45811"},
                {"CandidateName": "MIKE COGBILL", "Votes": "7926"},
            ]},
            {"Republican": [{"CandidateName": "TODD JOHNSON", "Votes": "3756"}]},
        ]),
        ("10th Senatorial District", [
            {"Democratic": [{"CandidateName": "STEVE SANTARSIERO", "Votes": "31715"}]},
            {"Republican": [{"CandidateName": "GREG BANKOS", "Votes": "13860"}]},
        ]),
    ]),
    13: _office("Representative in the General Assembly", [
        ("3rd Legislative District", [
            {"Democratic": [{"CandidateName": "RYAN A BIZZARRO", "Votes": "6752"}]},
            {"Republican": [{"CandidateName": "DONNA REESE", "Votes": "3151"}]},
        ]),
    ]),
    # Party committee seats: same response shape, never a public office.
    21: _office("Member of Democratic State Committee", [("Statewide", [
        {"Democratic": [{"CandidateName": "SOMEONE ELSE", "Votes": "1000"}]},
    ])]),
    22: _office("Member of Republican State Committee", [("12th Senatorial District", [
        {"Republican": [{"CandidateName": "ANOTHER ONE", "Votes": "900"}]},
    ])]),
}


def _serve_state(monkeypatch, results=_STATE_RESULTS):
    asked = []

    async def fake_get(client, url, label):
        if "GetAllElections" in url:
            return _ELECTIONS
        if "GetOfficeNames" in url:
            return _STATE_OFFICES
        office_id = int(url.split("officeId=")[1].split("&")[0])
        asked.append(office_id)
        return results.get(office_id)

    monkeypatch.setattr(pa, "_get", fake_get)
    return asked


@pytest.mark.asyncio
class TestStateOffices:
    async def test_without_the_flag_only_federal_offices_are_fetched(self, monkeypatch):
        """Pennsylvania's own General Assembly is in the same office list,
        and its "Representative in the General Assembly" must never be
        taken for a seat in Congress."""
        asked = _serve_state(monkeypatch)
        records = await pa.fetch_confirmed_candidates(None, 2026, "PA", {})
        assert asked == [11]
        assert {r["office"] for r in records} == {"H"}

    async def test_governor_and_lieutenant_governor_nominees(self, monkeypatch):
        _serve_state(monkeypatch)
        records = await pa.fetch_confirmed_candidates(
            None, 2026, "PA", {"statewide_offices": True},
        )
        statewide = sorted(
            (r["office"], r["party"], r["last_name"])
            for r in records if r["office"] in ("governor", "lt_governor")
        )
        assert statewide == [
            ("governor", "D", "JOSH SHAPIRO"),
            ("governor", "R", "STACY GARRITY"),
            ("lt_governor", "D", "AUSTIN DAVIS"),
            # The primary WINNER, not the first row the API lists.
            ("lt_governor", "R", "JASON RICHEY"),
        ]
        assert all(
            r["district"] is None for r in records if r["office"] in ("governor", "lt_governor")
        )

    async def test_legislative_seats_read_with_pennsylvanias_article(self, monkeypatch):
        """"Senator in THE General Assembly" — Rhode Island's wording plus
        an article, which the shared chamber gate now allows."""
        _serve_state(monkeypatch)
        records = await pa.fetch_confirmed_candidates(
            None, 2026, "PA", {"statewide_offices": True},
        )
        leg = sorted(
            (r["office"], r["district"], r["party"], r["last_name"])
            for r in records if r["office"] in ("upper", "lower")
        )
        assert leg == [
            ("lower", "3", "D", "RYAN A BIZZARRO"),
            ("lower", "3", "R", "DONNA REESE"),
            ("upper", "10", "D", "STEVE SANTARSIERO"),
            ("upper", "10", "R", "GREG BANKOS"),
            ("upper", "4", "D", "ART HAYWOOD"),
            ("upper", "4", "R", "TODD JOHNSON"),
        ]

    async def test_party_committees_are_never_published(self, monkeypatch):
        asked = _serve_state(monkeypatch)
        records = await pa.fetch_confirmed_candidates(
            None, 2026, "PA", {"statewide_offices": True},
        )
        names = {r["last_name"] for r in records}
        assert "SOMEONE ELSE" not in names and "ANOTHER ONE" not in names
        # Not even requested: a committee that failed to load must not
        # take the state's real contests down with it.
        assert sorted(asked) == [3, 4, 11, 12, 13]

    async def test_the_committee_labels_are_refused_by_the_gates_too(self):
        """The per-contest check stands on its own, not only on the
        office-list pre-filter."""
        from app.pipeline.fetch.state_candidates_common import (
            parse_state_leg_office, parse_statewide_office,
        )
        for label in (
            "Member of Democratic State Committee Statewide",
            "Member of Republican State Committee 12th Senatorial District",
        ):
            assert parse_statewide_office(label) is None
            assert parse_state_leg_office(label) is None

    async def test_federal_nominees_are_unchanged_by_the_flag(self, monkeypatch):
        _serve_state(monkeypatch)
        records = await pa.fetch_confirmed_candidates(
            None, 2026, "PA", {"statewide_offices": True},
        )
        assert sorted((r["party"], r["last_name"]) for r in records if r["office"] == "H") == [
            ("D", "HARVIE"), ("R", "FITZPATRICK"),
        ]

    async def test_an_unreadable_office_list_is_a_failure(self, monkeypatch):
        """With the flag set an empty answer would be stored as "no
        statewide contests" — so a list that cannot be read is None."""
        async def fake_get(client, url, label):
            if "GetAllElections" in url:
                return _ELECTIONS
            return None

        monkeypatch.setattr(pa, "_get", fake_get)
        assert await pa.fetch_confirmed_candidates(
            None, 2026, "PA", {"statewide_offices": True},
        ) is None

    async def test_a_failed_state_office_read_fails_the_whole_answer(self, monkeypatch):
        results = dict(_STATE_RESULTS)
        results.pop(3)
        _serve_state(monkeypatch, results)
        assert await pa.fetch_confirmed_candidates(
            None, 2026, "PA", {"statewide_offices": True},
        ) is None
