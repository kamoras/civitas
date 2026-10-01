"""fetch_member_sponsored tells a failed request from a member with no bills.

It used to return [] for both, so a failed Congress.gov request scored the
member as "0 substantive bills — confirmed inactivity" for that run.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from app.models import ApiCache
from app.pipeline.cache import api_cache_set
from app.pipeline.fetch import congress
from app.time_utils import utcnow
from tests.conftest import TEST_CONGRESS

KEY = "member-sponsored-v2-X000001"
# The Congress conftest pins every test to, not the import-time (real
# clock) settings.CURRENT_CONGRESS.
BILL = {"congress": TEST_CONGRESS, "type": "S", "number": "1", "title": "A bill"}


async def test_failed_request_with_nothing_cached_is_unknown(db_session):
    with patch.object(congress, "_fetch_with_retry", AsyncMock(return_value=None)):
        assert await congress.fetch_member_sponsored(None, db_session, "X000001") is None


async def test_failed_request_falls_back_to_an_expired_cached_list(db_session):
    api_cache_set(db_session, "congress", KEY, [BILL])
    entry = db_session.query(ApiCache).filter(ApiCache.cache_key == KEY).one()
    entry.cached_at = utcnow() - timedelta(days=30)  # past the normal TTL
    db_session.commit()
    with patch.object(congress, "_fetch_with_retry", AsyncMock(return_value=None)):
        assert await congress.fetch_member_sponsored(None, db_session, "X000001") == [BILL]


async def test_a_member_with_no_bills_is_an_empty_list(db_session):
    with patch.object(congress, "_fetch_with_retry", AsyncMock(return_value={"sponsoredLegislation": []})):
        assert await congress.fetch_member_sponsored(None, db_session, "X000001") == []
