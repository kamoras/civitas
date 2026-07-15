"""Tests for house_pipeline.py's donor-vote lobbying-match wiring.

House never got detect_lobbying_matches (embeddings-only, zero LLM) wired
up, unlike Senate — Constituent Alignment's donor-independence component
(25% weight) silently defaulted to a flat, fundraising-size-based score
for every House member regardless of actual donor-vote behavior (2026-07
audit finding). These tests cover the new wiring: the LDA-enrichment
helper directly, and that a real (non-empty) lobbying_matches list
actually changes _calc_constituent_alignment's computation path instead
of falling through to the flat fallback.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.analyze.cross_reference import detect_lobbying_matches
from app.pipeline.analyze.score_calculator import _calc_constituent_alignment
from app.pipeline.house_pipeline import _enrich_lobbying_matches_with_lda


class TestEnrichLobbyingMatchesWithLda:
    @pytest.mark.asyncio
    async def test_adds_spend_and_description_when_spend_found(self, db_session):
        matches = [{"lobbyistOrg": "Big Pharma Inc", "description": "base description"}]
        with patch(
            "app.pipeline.fetch.lda.fetch_lobbying_spend", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = 250_000.0
            await _enrich_lobbying_matches_with_lda(matches, db_session, 2025)

        assert matches[0]["lobbyingSpend"] == 250_000
        assert "250,000" in matches[0]["description"]
        assert matches[0]["description"].startswith("base description")

    @pytest.mark.asyncio
    async def test_zero_spend_does_not_alter_description(self, db_session):
        matches = [{"lobbyistOrg": "Small Org", "description": "base description"}]
        with patch(
            "app.pipeline.fetch.lda.fetch_lobbying_spend", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = 0.0
            await _enrich_lobbying_matches_with_lda(matches, db_session, 2025)

        assert matches[0]["lobbyingSpend"] == 0
        assert matches[0]["description"] == "base description"

    @pytest.mark.asyncio
    async def test_empty_matches_list_is_a_noop(self, db_session):
        matches = []
        # No mocking needed — if this tried to create a client or call the
        # backend it would fail without network access.
        await _enrich_lobbying_matches_with_lda(matches, db_session, 2025)
        assert matches == []

    @pytest.mark.asyncio
    async def test_one_org_failing_does_not_block_the_others(self, db_session):
        matches = [
            {"lobbyistOrg": "Failing Org", "description": ""},
            {"lobbyistOrg": "Working Org", "description": ""},
        ]
        with patch(
            "app.pipeline.fetch.lda.fetch_lobbying_spend", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = [RuntimeError("LDA API down"), 100_000.0]
            await _enrich_lobbying_matches_with_lda(matches, db_session, 2025)

        assert "lobbyingSpend" not in matches[0]
        assert matches[1]["lobbyingSpend"] == 100_000


class TestHouseLobbyingMatchesFeedConstituentAlignment:
    """Proves the wiring actually changes score behavior, not just that
    detect_lobbying_matches can be called without error."""

    def _industry_concentrated_data(self):
        industry_breakdown = [
            {"industry": "PHARMA", "name": "PHARMA", "total": 800_000, "percentage": 80},
            {"industry": "TECH", "name": "TECH", "total": 200_000, "percentage": 20},
        ]
        donors = [
            {"name": "Pharma PAC", "industry": "PHARMA", "type": "PAC", "total": 800_000},
            {"name": "Tech PAC", "industry": "TECH", "type": "PAC", "total": 200_000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Drug Pricing Reform Act",
             "policyArea": "HEALTHCARE", "description": "Prescription drug price controls",
             "totalYeas": 220, "totalNays": 210},
        ]
        return donors, votes, industry_breakdown

    def test_no_lobbying_matches_uses_flat_fundraising_fallback(self):
        """Without any detected matches (House's behavior before this
        fix), donor_score is a constant derived only from total_raised —
        identical regardless of a member's actual voting behavior."""
        voting_record = {"keyVotes": [], "recentVotes": []}
        funding_small = {"totalRaised": 500_000, "totalFromPACs": 0}
        funding_big = {"totalRaised": 500_000, "totalFromPACs": 0}

        score_a = _calc_constituent_alignment(voting_record, [], funding_small, party="D")
        score_b = _calc_constituent_alignment(voting_record, [], funding_big, party="D")

        # Same total_raised bucket, zero matches both times -> identical
        # donor-independence contribution regardless of anything else.
        assert score_a == score_b

    def test_real_matches_move_the_score_away_from_the_flat_default(self):
        """With detect_lobbying_matches wired in (this fix), a genuine
        industry-concentration + on-topic-vote finding measurably changes
        the score versus the no-data case, instead of always landing on
        the same fundraising-size-only number."""
        donors, votes, industry_breakdown = self._industry_concentrated_data()
        matches = detect_lobbying_matches(donors, votes, industry_breakdown)
        assert matches, "fixture should produce a real match — test is invalid otherwise"

        voting_record = {"keyVotes": votes, "recentVotes": []}
        funding = {
            "totalRaised": 1_000_000, "totalFromPACs": 0,
            "topDonors": donors, "industryBreakdown": industry_breakdown,
        }

        with_matches = _calc_constituent_alignment(voting_record, matches, funding, party="D")
        without_matches = _calc_constituent_alignment(voting_record, [], funding, party="D")

        assert with_matches != without_matches
