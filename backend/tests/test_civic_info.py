"""Tests for town-level ballot lookups (Google Civic Information API).

The discipline under test is the same one test_ballot_measures.py exercises
for statewide measures: an unconfigured or unknown-town lookup must read as
"not covered", never as "no local races" — and a candidate contest must
never be misread as a measure or vice versa.
"""

import json

import pytest
from fastapi import HTTPException

from app.api import elections
from app.pipeline.fetch import civic_info, town_directory


def _body(response):
    return json.loads(response.body)


# ── directory ────────────────────────────────────────────────────────


def test_towns_for_state_empty_for_uncurated_state():
    assert town_directory.towns_for_state("ZZ") == []


def test_towns_for_state_returns_curated_entries():
    towns = town_directory.towns_for_state("MA")
    names = {t["name"] for t in towns}
    assert names == {"Cambridge", "Somerville"}
    assert all(t["address"] for t in towns)


def test_address_for_town_is_case_insensitive():
    assert town_directory.address_for_town("MA", "cambridge") == \
        town_directory.address_for_town("MA", "Cambridge")


def test_address_for_town_none_when_not_curated():
    assert town_directory.address_for_town("MA", "Nowhereville") is None


# ── parsing ──────────────────────────────────────────────────────────


def test_text_treats_non_string_values_as_absent():
    assert civic_info._text({"a": {}}, "a") is None
    assert civic_info._text({"a": "", "b": " x "}, "a", "b") == "x"


def test_referendum_takes_priority_over_candidate_fields():
    """A contest with both referendumTitle and office (shouldn't happen,
    but the API is external) must resolve to a measure — referendumTitle
    is the reliable discriminator, `type` free-texts across jurisdictions."""
    raw = {"office": "City Council", "referendumTitle": "Prop 1"}
    assert civic_info._parse_referendum(raw) is not None
    assert civic_info._parse_candidate_contest(raw) is not None  # both parseable alone
    parsed = civic_info._parse_contests({"contests": [raw]})
    assert len(parsed) == 1
    assert parsed[0]["kind"] == "measure"


def test_parse_contests_splits_candidates_and_measures():
    payload = {
        "contests": [
            {"office": "City Council Ward 1", "candidates": [
                {"name": "Jane Doe", "party": "D"},
                {"name": "", "party": "R"},  # dropped: no name
            ]},
            {"referendumTitle": "Question 1", "referendumText": "Shall the town..."},
            {"office": ""},  # dropped: no office, no referendum
            "not a dict",  # dropped: malformed entry
        ],
    }
    parsed = civic_info._parse_contests(payload)
    assert len(parsed) == 2
    contest = next(p for p in parsed if p["kind"] == "contest")
    assert contest["office"] == "City Council Ward 1"
    assert [c["name"] for c in contest["candidates"]] == ["Jane Doe"]
    measure = next(p for p in parsed if p["kind"] == "measure")
    assert measure["title"] == "Question 1"


def test_parse_contests_empty_on_missing_key():
    """Not hypothetical: a real voterInfoQuery response for a real
    address + a real currently-indexed election (2026-08-04 MI primary,
    live-verified with a real key) came back with `election`/`state`/
    `normalizedInput` but no `contests` key at all — Google's coverage
    is per-jurisdiction and doesn't guarantee contest-level data even
    for an address it recognizes. This must not raise."""
    assert civic_info._parse_contests({}) == []


# ── fetch layer ──────────────────────────────────────────────────────


def test_is_configured_false_without_key(monkeypatch):
    monkeypatch.setattr(civic_info.settings, "GOOGLE_CIVIC_API_KEY", "")
    assert civic_info.is_configured() is False


@pytest.mark.asyncio
async def test_fetch_returns_none_when_unconfigured(monkeypatch, db_session):
    monkeypatch.setattr(civic_info.settings, "GOOGLE_CIVIC_API_KEY", "")
    result = await civic_info.fetch_town_ballot(None, db_session, "MA", "Cambridge")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_none_for_uncurated_town(monkeypatch, db_session):
    """A configured key but a town outside the curated list must not
    attempt a lookup — never guess an address."""
    monkeypatch.setattr(civic_info.settings, "GOOGLE_CIVIC_API_KEY", "test-key")
    result = await civic_info.fetch_town_ballot(None, db_session, "MA", "Nowhereville")
    assert result is None


# ── API ───────────────────────────────────────────────────────────────


def test_state_towns_404s_on_unknown_state():
    with pytest.raises(HTTPException) as exc:
        elections.state_towns("ZZ")
    assert exc.value.status_code == 404


def test_state_towns_empty_without_key(monkeypatch):
    monkeypatch.setattr(civic_info.settings, "GOOGLE_CIVIC_API_KEY", "")
    data = _body(elections.state_towns("MA"))
    assert data["towns"] == []


def test_state_towns_lists_curated_entries_when_configured(monkeypatch):
    monkeypatch.setattr(civic_info.settings, "GOOGLE_CIVIC_API_KEY", "test-key")
    data = _body(elections.state_towns("MA"))
    assert {t["name"] for t in data["towns"]} == {"Cambridge", "Somerville"}


@pytest.mark.asyncio
async def test_town_ballot_404s_on_unknown_state(db_session):
    with pytest.raises(HTTPException) as exc:
        await elections.town_ballot("ZZ", "Anytown", db=db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_town_ballot_not_yet_covered_without_key(monkeypatch, db_session):
    monkeypatch.setattr(civic_info.settings, "GOOGLE_CIVIC_API_KEY", "")
    data = _body(await elections.town_ballot("MA", "Cambridge", db=db_session))
    assert data["status"] == "not_yet_covered"
    assert data["contests"] == []


@pytest.mark.asyncio
async def test_town_ballot_not_yet_covered_for_uncurated_town(monkeypatch, db_session):
    monkeypatch.setattr(civic_info.settings, "GOOGLE_CIVIC_API_KEY", "test-key")
    data = _body(await elections.town_ballot("MA", "Nowhereville", db=db_session))
    assert data["status"] == "not_yet_covered"
