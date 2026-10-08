"""A source that could not be read is never stored as an empty answer:
roll calls (House Clerk, Senate.gov) and FEC fetches report failure as
None or FecUnavailable, cache nothing, and the runs keep stored records."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.cache import api_cache_get
from app.pipeline.fetch import congress, fec

pytestmark = pytest.mark.asyncio

_HOUSE_KEY = f"recent-house-rollcalls-2026-5-v{congress.ROLL_CALL_PARSE_VERSION}"
_SENATE_KEY = f"recent-rollcalls-119-2-5-v{congress.ROLL_CALL_PARSE_VERSION}"


async def _house(db, highest, roll=None):
    with patch.object(congress, "_find_highest_roll_call", new_callable=AsyncMock, return_value=highest), \
         patch.object(congress, "fetch_house_roll_call_vote", new_callable=AsyncMock, return_value=roll):
        return await congress.fetch_recent_house_roll_calls(None, db, year=2026, count=5)


async def _senate(db, highest, roll=None):
    with patch.object(congress, "_find_highest_roll_call", new_callable=AsyncMock, return_value=highest), \
         patch.object(congress, "fetch_roll_call_vote", new_callable=AsyncMock, return_value=roll):
        return await congress.fetch_recent_roll_calls(None, db, congress=119, session_number=2, count=5)


@pytest.mark.parametrize("fetch,key", [(_house, _HOUSE_KEY), (_senate, _SENATE_KEY)])
class TestRecentRollCalls:
    async def test_an_unreachable_site_is_none(self, db_session, fetch, key):
        assert await fetch(db_session, None) is None
        assert api_cache_get(db_session, "congress", key) is None

    async def test_no_votes_yet_is_empty(self, db_session, fetch, key):
        assert await fetch(db_session, 0) == []

    async def test_rolls_that_all_fail_to_read_are_none_and_not_cached(self, db_session, fetch, key):
        assert await fetch(db_session, 12, roll=None) is None
        assert api_cache_get(db_session, "congress", key) is None

    async def test_rolls_that_read_are_cached(self, db_session, fetch, key):
        rolls = await fetch(db_session, 12, roll={"rollNumber": 1})
        assert len(rolls) == 5
        assert api_cache_get(db_session, "congress", key) == rolls


async def test_early_signal_reads_an_unreachable_source_as_no_new_votes(db_session):
    from app.pipeline.analyze import early_signal

    @asynccontextmanager
    async def client():
        yield None

    with patch.object(early_signal, "make_async_client", client), \
         patch.object(early_signal, "fetch_recent_roll_calls", new_callable=AsyncMock, return_value=None), \
         patch.object(early_signal, "fetch_recent_house_roll_calls", new_callable=AsyncMock, return_value=None):
        assert await asyncio.to_thread(early_signal._fetch_recent_votes, db_session) == []


class TestFecUnavailable:
    async def test_receipts_raise_instead_of_caching_nothing(self, db_session):
        with patch.object(fec, "_fetch_with_retry", new_callable=AsyncMock, return_value=None):
            for fetch in (fec.fetch_committee_receipts, fec.fetch_pac_receipts):
                with pytest.raises(fec.FecUnavailable):
                    await fetch(None, db_session, "C001", cycles=[2024])

    async def test_a_failed_name_search_raises_rather_than_reading_as_no_match(self, db_session):
        with patch.object(fec, "_fetch_with_retry", new_callable=AsyncMock, return_value=None), \
             pytest.raises(fec.FecUnavailable):
            await fec.find_candidate(None, db_session, "Jane Doe", "TX", office="H", district="02")


class TestSenatorFec:
    _SENATOR = {"id": "S1", "name": "Jane Doe", "state": "TX", "bioguideId": "D000001"}

    async def _fetch(self, db, **patches):
        from app.pipeline import senate_pipeline

        defaults = {
            "find_candidate": AsyncMock(return_value={"candidate_id": "S6TX00001"}),
            "fetch_candidate_financials": AsyncMock(return_value=[]),
            "fetch_candidate_committees": AsyncMock(return_value=[{"committee_id": "C001"}]),
            "fetch_committee_receipts": AsyncMock(return_value=[{"r": 1}]),
            "fetch_pac_receipts": AsyncMock(return_value=[]),
            "fetch_contribution_detail": AsyncMock(return_value={"pacs": None}),
        }
        defaults.update(patches)
        with patch.multiple(senate_pipeline, **defaults):
            return await senate_pipeline._fetch_senator_fec(None, db, self._SENATOR, {}, {})

    async def test_read(self, db_session):
        fec_data = await self._fetch(db_session)
        assert fec_data["candidate"] == {"candidate_id": "S6TX00001"}
        assert fec_data["receipts"] == [{"r": 1}]

    async def test_no_match(self, db_session):
        assert await self._fetch(db_session, find_candidate=AsyncMock(return_value=None)) is None

    async def test_an_unreachable_fec_marks_the_senator_unavailable(self, db_session):
        down = AsyncMock(side_effect=fec.FecUnavailable("down"))
        assert await self._fetch(db_session, fetch_candidate_financials=down) == {"unavailable": True}


async def test_a_house_run_that_cannot_read_its_roll_calls_saves_no_member(db_session):
    from app.pipeline import house_pipeline

    saved = MagicMock()
    with (
        patch.object(house_pipeline, "SessionLocal", return_value=db_session),
        patch.object(house_pipeline, "fetch_representatives", new_callable=AsyncMock, return_value=[{"bioguideId": "R000001"}]),
        patch.object(house_pipeline, "fetch_member_detail", new_callable=AsyncMock, return_value={}),
        patch.object(house_pipeline, "normalize_house_members", return_value=[{"bioguideId": "R000001"}]),
        patch.object(house_pipeline, "fetch_significant_bills", new_callable=AsyncMock, return_value=[]),
        patch.object(house_pipeline, "fetch_recent_house_roll_calls", new_callable=AsyncMock, return_value=None) as rcs,
        patch.object(house_pipeline, "upsert_representative", saved),
    ):
        try:
            await house_pipeline.run_house_pipeline()
        except Exception:
            pass
    rcs.assert_awaited_once()  # no prior-year read after a failed one
    saved.assert_not_called()
