"""Tests for fetch_committee_type's caching behavior.

fetch_committee_type is looked up once per unique contributing PAC across
ALL senators/reps in a pipeline run (see senate_pipeline.py's global
pre-pass and house_pipeline.py's per-representative pre-pass) — its
long-TTL cache (COMMITTEE_TYPE_CACHE_TTL_HOURS) is what makes that cheap.
Uses the real api_cache_get/api_cache_set against the in-memory
db_session fixture rather than mocking the cache layer, so this exercises
the real cache read/write path; only the outbound HTTP call is mocked.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.fec import fetch_committee_type


@pytest.mark.asyncio
async def test_fetch_committee_type_returns_committee_type_code(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry",
        new=AsyncMock(return_value={"results": [{"committee_type": "Q"}]}),
    ) as mocked:
        result = await fetch_committee_type(client=None, db=db_session, committee_id="C00429613")
        assert result == "Q"
        assert mocked.call_count == 1


@pytest.mark.asyncio
async def test_fetch_committee_type_caches_across_calls(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry",
        new=AsyncMock(return_value={"results": [{"committee_type": "N"}]}),
    ) as mocked:
        first = await fetch_committee_type(client=None, db=db_session, committee_id="C00500587")
        second = await fetch_committee_type(client=None, db=db_session, committee_id="C00500587")
        assert first == second == "N"
        # Second call hit the cache — no second HTTP fetch.
        assert mocked.call_count == 1


@pytest.mark.asyncio
async def test_fetch_committee_type_none_when_committee_not_found(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry",
        new=AsyncMock(return_value={"results": []}),
    ):
        result = await fetch_committee_type(client=None, db=db_session, committee_id="C99999999")
        assert result is None
