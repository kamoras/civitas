"""Tests for the ApiCache TTL/empty-result interaction (cache.py).

Regression coverage for a real bug found live 2026-07-25: house_ptr.py/
senate_ptr.py read their per-filing parse cache with a 30-day max_age_hours
(intentional — a successfully parsed filing's PDF never changes), but
api_cache_set's empty-result short-TTL backdating assumed the default 72h
window. A single transient parse failure (a real PDF that failed to parse
once, e.g. under CPU contention) got cached empty and then read back as
still-valid for the full 30 days instead of retrying within
EMPTY_RESPONSE_TTL_HOURS — silently blocking that filing from ever being
re-attempted for a month.
"""

from datetime import timedelta

from app.pipeline.cache import EMPTY_RESPONSE_TTL_HOURS, api_cache_get, api_cache_set
from app.time_utils import utcnow


class TestApiCacheEmptyTtl:
    def test_empty_result_is_a_hit_immediately_after_write(self, db_session):
        api_cache_set(db_session, "t", "k", [], normal_ttl_hours=24 * 30)
        assert api_cache_get(db_session, "t", "k", max_age_hours=24 * 30) == []

    def test_empty_result_expires_after_empty_ttl_with_matching_normal_ttl(self, db_session, monkeypatch):
        api_cache_set(db_session, "t", "k", [], normal_ttl_hours=24 * 30)
        future = utcnow() + timedelta(hours=EMPTY_RESPONSE_TTL_HOURS + 1)
        monkeypatch.setattr("app.pipeline.cache.utcnow", lambda: future)
        assert api_cache_get(db_session, "t", "k", max_age_hours=24 * 30) is None

    def test_mismatched_normal_ttl_reproduces_the_original_bug(self, db_session, monkeypatch):
        """Documents the failure mode this fixes: writing WITHOUT the
        matching normal_ttl_hours (defaults to 72h) while the caller reads
        with a much longer custom max_age_hours makes the empty result
        incorrectly survive well past EMPTY_RESPONSE_TTL_HOURS."""
        api_cache_set(db_session, "t", "k", [])  # no normal_ttl_hours passed
        future = utcnow() + timedelta(hours=EMPTY_RESPONSE_TTL_HOURS + 1)
        monkeypatch.setattr("app.pipeline.cache.utcnow", lambda: future)
        assert api_cache_get(db_session, "t", "k", max_age_hours=24 * 30) is not None

    def test_non_empty_result_honors_the_long_custom_ttl(self, db_session):
        api_cache_set(db_session, "t", "k", [{"row": 1}], normal_ttl_hours=24 * 30)
        assert api_cache_get(db_session, "t", "k", max_age_hours=24 * 30) == [{"row": 1}]

    def test_empty_never_overwrites_existing_non_empty(self, db_session):
        api_cache_set(db_session, "t", "k", [{"row": 1}], normal_ttl_hours=24 * 30)
        api_cache_set(db_session, "t", "k", [], normal_ttl_hours=24 * 30)
        assert api_cache_get(db_session, "t", "k", max_age_hours=24 * 30) == [{"row": 1}]
