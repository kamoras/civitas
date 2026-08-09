"""Tests for Texas's confirmed-general-election-candidate strategy
(state_candidates_tx.py) — the Civix Candidate Bio Portal.

fixtures_tx_civix_elections.json and fixtures_tx_civix_candidates.json are
REAL — trimmed to the 2026 Senate race (4 candidates) and TX-1 House race
(2 candidates) from a live network capture, 2026-08-09. The Senate fixture
carries the real, known-correct ground truth this parser is checked
against: Ken Paxton beat John Cornyn in the May 2026 runoff, and TX's own
data reflects that — Paxton's row is "CG" ("Candidate in the General
Election"); Cornyn has no row at all under the general-election id.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch import state_candidates_tx as tx

ELECTIONS_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures_tx_civix_elections.json").read_text()
)
CANDIDATES_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures_tx_civix_candidates.json").read_text()
)


def _response(payload):
    return SimpleNamespace(json=lambda: payload)


class TestParseOffice:
    def test_parses_senate(self):
        assert tx._parse_office("U. S. SENATOR ") == ("S", None)

    def test_parses_house_with_district(self):
        assert tx._parse_office("U. S. REPRESENTATIVE DISTRICT 12") == ("H", 12)

    def test_returns_none_for_a_non_federal_office(self):
        assert tx._parse_office("GOVERNOR") is None
        assert tx._parse_office("STATE SENATOR DISTRICT 4") is None

    def test_returns_none_for_empty_or_missing_name(self):
        assert tx._parse_office("") is None
        assert tx._parse_office(None) is None


class TestFindGeneralElectionId:
    @pytest.mark.asyncio
    async def test_finds_the_ge_election_by_type_not_name(self):
        with patch(
            "app.pipeline.fetch.state_candidates_tx.fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = _response(ELECTIONS_FIXTURE)
            election_id = await tx._find_general_election_id(None, 2026)
        assert election_id == 53815

    @pytest.mark.asyncio
    async def test_none_when_no_ge_election_indexed_yet(self):
        with patch(
            "app.pipeline.fetch.state_candidates_tx.fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = _response([
                e for e in ELECTIONS_FIXTURE if e["cdElectionType"] != "GE"
            ])
            election_id = await tx._find_general_election_id(None, 2026)
        assert election_id is None

    @pytest.mark.asyncio
    async def test_none_on_fetch_failure(self):
        with patch(
            "app.pipeline.fetch.state_candidates_tx.fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = None
            election_id = await tx._find_general_election_id(None, 2026)
        assert election_id is None


class TestFetchConfirmedCandidates:
    @pytest.mark.asyncio
    async def test_returns_exactly_the_confirmed_senate_nominees(self):
        """Ground truth: Paxton (R) and Talarico (D) are the real 2026
        general-election nominees; Brown (L) is a real confirmed
        Libertarian nominee (ballot access via convention, not primary,
        but still carries CG); Simmons (I) was REJECTED at the
        declaration stage (cdDeclarationStatus="R", no cdFilingStatus at
        all) and must not appear."""
        with patch(
            "app.pipeline.fetch.state_candidates_tx.fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = [_response(ELECTIONS_FIXTURE), _response(CANDIDATES_FIXTURE)]
            results = await tx.fetch_confirmed_candidates(None, 2026)

        senate = [r for r in results if r["office"] == "S"]
        assert {(r["party"], r["last_name"]) for r in senate} == {("R", "PAXTON"), ("D", "TALARICO"), ("L", "BROWN")}
        assert "SIMMONS" not in {r["last_name"] for r in senate}

    @pytest.mark.asyncio
    async def test_parses_house_office_and_district(self):
        with patch(
            "app.pipeline.fetch.state_candidates_tx.fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = [_response(ELECTIONS_FIXTURE), _response(CANDIDATES_FIXTURE)]
            results = await tx.fetch_confirmed_candidates(None, 2026)

        house = [r for r in results if r["office"] == "H"]
        assert {(r["party"], r["district"], r["last_name"]) for r in house} == {
            ("D", 1, "PRINCE"), ("R", 1, "MORAN"),
        }

    @pytest.mark.asyncio
    async def test_none_when_no_general_election_found(self):
        with patch(
            "app.pipeline.fetch.state_candidates_tx.fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = _response([])
            results = await tx.fetch_confirmed_candidates(None, 2026)
        assert results is None

    @pytest.mark.asyncio
    async def test_none_when_candidate_fetch_fails(self):
        with patch(
            "app.pipeline.fetch.state_candidates_tx.fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = [_response(ELECTIONS_FIXTURE), None]
            results = await tx.fetch_confirmed_candidates(None, 2026)
        assert results is None

    @pytest.mark.asyncio
    async def test_skips_non_federal_office_types(self):
        candidates = CANDIDATES_FIXTURE + [{
            "cdOfficeType": "ST", "cdFilingStatus": "CG",
            "txOfficeName": "GOVERNOR", "txLastNameBallot": "SOMEONE", "cdParty": "R",
        }]
        with patch(
            "app.pipeline.fetch.state_candidates_tx.fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = [_response(ELECTIONS_FIXTURE), _response(candidates)]
            results = await tx.fetch_confirmed_candidates(None, 2026)
        assert "SOMEONE" not in {r["last_name"] for r in results}
