"""Tests for find_candidate's district-mismatch fallback.

FEC's `district` field on a candidate record can lag a member's current
Congress.gov district after redistricting — a district-constrained search
then finds nothing even though the candidate exists (2026-07 audit: 16
sitting representatives had $0 recorded funding because of exactly this;
Al Green (TX-9 on Congress.gov) is on file with FEC under district 18).
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.fec import find_candidate


def _candidate(name: str, candidate_id: str, district: str) -> dict:
    return {"name": name, "candidate_id": candidate_id, "district": district}


@pytest.mark.asyncio
async def test_district_match_found_directly(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {
            "results": [_candidate("SMITH, JANE", "H2CA01001", "01")]
        }
        result = await find_candidate(
            None, db_session, "Jane Smith", "CA", office="H", district="01"
        )
        assert result["candidate_id"] == "H2CA01001"
        mock_fetch.assert_called_once()  # no fallback needed

    # search resolved with the district in the URL
    assert "district=01" in mock_fetch.call_args.args[1]


@pytest.mark.asyncio
async def test_district_mismatch_falls_back_to_name_only(db_session):
    async def fake_fetch(client, url, retries=None):
        if "district=" in url:
            return {"results": []}  # district-constrained search: nothing
        return {"results": [_candidate("GREEN, ALEXANDER", "H4TX09095", "18")]}

    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = fake_fetch
        result = await find_candidate(
            None, db_session, "Al Green", "TX", office="H", district="09"
        )
        assert result is not None
        assert result["candidate_id"] == "H4TX09095"
        assert mock_fetch.call_count == 2  # district attempt, then fallback


@pytest.mark.asyncio
async def test_fallback_requires_genuine_name_match(db_session):
    """Without a district to disambiguate, an unrelated same-surname
    candidate must NOT be accepted as a fallback match."""
    async def fake_fetch(client, url, retries=None):
        if "district=" in url:
            return {"results": []}
        return {"results": [_candidate("GREENE, MARK", "H0TX12147", "12")]}

    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = fake_fetch
        result = await find_candidate(
            None, db_session, "Al Green", "TX", office="H", district="09"
        )
        assert result is None


@pytest.mark.asyncio
async def test_no_district_provided_no_fallback_attempted(db_session):
    """Senate searches never pass a district — nothing to fall back from."""
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {"results": []}
        result = await find_candidate(
            None, db_session, "Nobody Here", "ZZ", office="S"
        )
        assert result is None
        mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_primary_search_requires_genuine_name_match(db_session):
    """A same-surname/state/office candidate from the primary (non-district)
    search must NOT be accepted as a fallback match either — e.g. a newly
    appointed senator sharing a surname with a long-tenured incumbent must
    not get that incumbent's committee attributed to them."""
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {
            "results": [_candidate("GRAHAM, LINDSEY O", "S0SC00149", "")]
        }
        result = await find_candidate(
            None, db_session, "Darline Graham", "SC", office="S"
        )
        assert result is None


@pytest.mark.asyncio
async def test_both_searches_empty_returns_none(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {"results": []}
        result = await find_candidate(
            None, db_session, "Ghost Person", "TX", office="H", district="09"
        )
        assert result is None
        assert mock_fetch.call_count == 2  # district attempt, then fallback
