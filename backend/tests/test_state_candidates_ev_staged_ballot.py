"""The staged November ballot on a state's Enhanced Voting results portal,
read as a general list (state_candidates_enhanced_voting.fetch_staged_ballot),
on trimmed real captures of Georgia's and Utah's staged 2026 generals
(fixtures_ev_staged_ballots.json)."""

import copy
import json
from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_enhanced_voting as ev

FIXTURES = json.loads((Path(__file__).parent / "fixtures_ev_staged_ballots.json").read_text())
SOURCES = json.loads((Path(__file__).resolve().parents[1] / "app" / "data" / "state_candidate_sources.json").read_text())


def _source(state):
    entry = SOURCES.get("states", SOURCES)[state]["general_list"]
    assert entry["strategy"] == "enhanced_voting_ballot"
    return entry


def _serve(monkeypatch, state, index=None, data=None):
    fixture = FIXTURES[state]
    index = fixture["index"] if index is None else index
    data = fixture["data"] if data is None else data

    async def fake(client, rl, url, label, **kw):
        if url == fixture["index_url"]:
            return index
        if url == fixture["data_url"]:
            return data
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(ev, "fetch_json_with_retry", fake)


async def _read(state, source=None):
    return await ev.fetch_staged_ballot(None, 2026, state, source or _source(state))


@pytest.mark.asyncio
async def test_every_printed_candidate_is_read_with_the_state_offices(monkeypatch):
    _serve(monkeypatch, "GA")
    records = await _read("GA")
    seats = {(r["office"], r["district"]) for r in records}
    assert seats == {("S", None), ("H", 1), ("governor", None), ("upper", "1"), ("lower", "1")}
    senate = {r["display_name"]: r["party"] for r in records if r["office"] == "S"}
    # The ballot's own annotations, "(Rep)" and the incumbent's "(I)", are not names.
    assert senate == {"Mike Collins": "R", "Jon Ossoff": "D"}
    assert {r["last_name"] for r in records if r["office"] == "governor"} == {"Rick Jackson", "Keisha Lance Bottoms"}
    assert records.ballot_list


@pytest.mark.asyncio
async def test_a_party_printed_only_by_name_is_read_and_a_write_in_never_is(monkeypatch):
    _serve(monkeypatch, "UT")
    records = await _read("UT")
    house = {r["display_name"]: (r["party"], r.get("party_label")) for r in records if r["office"] == "H"}
    assert house["BEN MCADAMS"] == ("D", "Democratic") and house["JESSE WEST"] == ("L", "Libertarian")
    assert not any("DARVISH" in name for name in house)  # a write-in line
    # Utah's entry reads federal races only: its portal holds only the
    # legislative districts that cross a county line.
    assert {r["office"] for r in records} == {"H"}


@pytest.mark.asyncio
async def test_not_staged_is_an_empty_list_and_a_failed_request_is_none(monkeypatch):
    _serve(monkeypatch, "GA", index={"elections": [e for e in FIXTURES["GA"]["index"]["elections"]
                                                   if not e["electionDate"].startswith("2026-11")]})
    assert await _read("GA") == []

    async def failed(client, rl, url, label, **kw):
        return None

    monkeypatch.setattr(ev, "fetch_json_with_retry", failed)
    assert await _read("GA") is None


@pytest.mark.asyncio
async def test_test_data_is_never_the_ballot(monkeypatch):
    data = copy.deepcopy(FIXTURES["GA"]["data"])
    data["election"]["isProduction"] = False
    _serve(monkeypatch, "GA", data=data)
    assert await _read("GA") == []


@pytest.mark.asyncio
async def test_a_staged_general_with_no_federal_contest_is_a_changed_layout(monkeypatch):
    data = copy.deepcopy(FIXTURES["GA"]["data"])
    data["ballotItems"] = [i for i in data["ballotItems"] if "US " not in i["name"][0]["text"]]
    _serve(monkeypatch, "GA", data=data)
    assert await _read("GA") is None
