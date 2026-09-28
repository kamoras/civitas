"""Tests for fetch_committee_meta's caching behavior.

The per-committee API is the fallback for a committee the FEC's bulk
committee master lacks (see resolve_committee_meta). Uses the real
api_cache_get/api_cache_set against the in-memory db_session fixture;
only the outbound HTTP call is mocked.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.fec import fetch_committee_meta


@pytest.mark.asyncio
async def test_returns_type_and_designation(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry",
        new=AsyncMock(return_value={"results": [{"committee_type": "Q", "designation": "D"}]}),
    ) as mocked:
        meta = await fetch_committee_meta(client=None, db=db_session, committee_id="C00429613")
    assert meta == {"type": "Q", "designation": "D", "connectedOrg": None}
    assert mocked.call_count == 1


@pytest.mark.asyncio
async def test_caches_across_calls(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry",
        new=AsyncMock(return_value={"results": [{"committee_type": "N", "designation": "U"}]}),
    ) as mocked:
        first = await fetch_committee_meta(client=None, db=db_session, committee_id="C00500587")
        second = await fetch_committee_meta(client=None, db=db_session, committee_id="C00500587")
    assert first == second
    assert mocked.call_count == 1


@pytest.mark.asyncio
async def test_none_when_committee_not_found(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry",
        new=AsyncMock(return_value={"results": []}),
    ):
        assert await fetch_committee_meta(client=None, db=db_session, committee_id="C99999999") is None


@pytest.mark.asyncio
async def test_a_failed_fetch_is_not_cached(db_session):
    mocked = AsyncMock(side_effect=[None, {"results": [{"committee_type": "Y", "designation": "U"}]}])
    with patch("app.pipeline.fetch.fec._fetch_with_retry", new=mocked):
        assert await fetch_committee_meta(client=None, db=db_session, committee_id="C00027466") is None
        again = await fetch_committee_meta(client=None, db=db_session, committee_id="C00027466")
    assert again["type"] == "Y"
