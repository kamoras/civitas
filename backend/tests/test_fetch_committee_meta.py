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
async def test_returns_type_and_designation_and_caches_across_calls(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry",
        new=AsyncMock(return_value={"results": [{"committee_type": "Q", "designation": "D", "organization_type": "C"}]}),
    ) as mocked:
        first = await fetch_committee_meta(client=None, db=db_session, committee_id="C00429613")
        second = await fetch_committee_meta(client=None, db=db_session, committee_id="C00429613")
    assert first == {"type": "Q", "designation": "D", "orgType": "C", "connectedOrg": None}
    assert second == first
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


@pytest.mark.asyncio
async def test_a_warm_type_only_entry_answers_without_a_request(db_session):
    from app.pipeline.cache import api_cache_set

    api_cache_set(db_session, "fec", "committee-type-v1-C00350744", {"committee_type": "Q"})
    with patch("app.pipeline.fetch.fec._fetch_with_retry", new=AsyncMock()) as mocked:
        meta = await fetch_committee_meta(client=None, db=db_session, committee_id="C00350744")
    assert meta == {"type": "Q", "designation": None, "connectedOrg": None}
    mocked.assert_not_awaited()
