"""Tests for fetch_all_candidates — the bulk FEC candidate-roster fetch
used by the midterm-elections feature (as opposed to find_candidate,
which resolves one incumbent's own record by name+state+office)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.fec import fetch_all_candidates


def _page(results: list[dict], page: int, pages: int) -> dict:
    return {"results": results, "pagination": {"page": page, "pages": pages}}


@pytest.mark.asyncio
async def test_single_page(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = _page(
            [{"candidate_id": "S6ID00146", "name": "ACHILLES, TODD"}], page=1, pages=1,
        )
        results = await fetch_all_candidates(None, db_session, cycle=2026, office="S")
    assert len(results) == 1
    assert results[0]["candidate_id"] == "S6ID00146"
    mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_pages_through_all_results(db_session):
    async def fake_fetch(client, url, retries=None):
        if "&page=1" in url:
            return _page([{"candidate_id": "A"}], page=1, pages=3)
        if "&page=2" in url:
            return _page([{"candidate_id": "B"}], page=2, pages=3)
        return _page([{"candidate_id": "C"}], page=3, pages=3)

    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = fake_fetch
        results = await fetch_all_candidates(None, db_session, cycle=2026, office="H")

    assert [r["candidate_id"] for r in results] == ["A", "B", "C"]
    assert mock_fetch.call_count == 3


@pytest.mark.asyncio
async def test_empty_response_stops_pagination(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = None
        results = await fetch_all_candidates(None, db_session, cycle=2026, office="S")
    assert results == []
    mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_queries_election_year_not_cycle(db_session):
    """Regression for 2026-07 review F1: FEC's `cycle` param matches any
    filing period a committee was active in (sitting senators up in
    2028/2030, prior-cycle committees winding down), fabricating phantom
    races. The query must use `election_year` — the actual ballot year —
    and the cache key must carry the new ey-prefix so stale cycle-keyed
    entries can't be served."""
    from app.models import ApiCache

    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = _page(
            [{"candidate_id": "S6ID00146"}], page=1, pages=1,
        )
        await fetch_all_candidates(None, db_session, cycle=2026, office="S")

    url = mock_fetch.call_args.args[1]
    assert "election_year=2026" in url
    assert "cycle=" not in url

    keys = [row.cache_key for row in db_session.query(ApiCache).all()]
    assert keys == ["candidates-roster-ey2026-S-page1"]


@pytest.mark.asyncio
async def test_uses_cache_on_second_call(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = _page(
            [{"candidate_id": "S6ID00146"}], page=1, pages=1,
        )
        await fetch_all_candidates(None, db_session, cycle=2026, office="S")
        await fetch_all_candidates(None, db_session, cycle=2026, office="S")
    mock_fetch.assert_called_once()  # second call served from ApiCache
