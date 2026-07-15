"""Tests for enrich_lobbying_matches_with_lda — real registered lobbying
spend (LDA filings) added to donor-vote lobbying matches. Shared by
senate_pipeline.py and house_pipeline.py (moved here from
test_house_pipeline.py when the House-only helper it originally covered
was consolidated into this shared one — see fetch/lda.py).
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.lda import enrich_lobbying_matches_with_lda


class TestEnrichLobbyingMatchesWithLda:
    @pytest.mark.asyncio
    async def test_adds_spend_and_description_when_spend_found(self, db_session):
        matches = [{"lobbyistOrg": "Big Pharma Inc", "description": "base description"}]
        with patch(
            "app.pipeline.fetch.lda.fetch_lobbying_spend", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = 250_000.0
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025)

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
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025)

        assert matches[0]["lobbyingSpend"] == 0
        assert matches[0]["description"] == "base description"

    @pytest.mark.asyncio
    async def test_empty_matches_list_is_a_noop(self, db_session):
        matches = []
        # No mocking needed — if this tried to create a client or call the
        # backend it would fail without network access.
        await enrich_lobbying_matches_with_lda(matches, db_session, 2025)
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
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025)

        assert "lobbyingSpend" not in matches[0]
        assert matches[1]["lobbyingSpend"] == 100_000
