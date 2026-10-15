"""How long the nightly's upstream fetches stay cached: per-key expiry
spread (cache.spread_ttl_hours), and a bill's own records kept until the
hourly refresh's update list says the bill changed
(congress._bill_record_get, bill_refresh.mark_bill_records_stale).

Both exist because one TTL for thousands of entries written in one run made
them all expire on the same later night, and that night refetched every one
through the rate limits."""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.pipeline import bill_refresh
from app.pipeline.cache import (
    EMPTY_RESPONSE_TTL_HOURS,
    EXPIRY_SPREAD,
    api_cache_get,
    api_cache_set,
    spread_ttl_hours,
)
from app.pipeline.fetch import congress
from tests.conftest import TEST_CONGRESS

T0 = datetime(2026, 10, 1, 6, 0)
ACTIONS_KEY = f"bill-actions-{TEST_CONGRESS}-hr-7"


class TestExpirySpread:
    def test_each_key_expires_inside_its_ttl_and_keys_differ(self):
        spreads = {spread_ttl_hours(f"k{i}", 72) for i in range(200)}
        assert all(72 * (1 - EXPIRY_SPREAD) <= s <= 72 for s in spreads)
        # Spread across the window, not bunched: some in each third.
        assert min(spreads) < 72 * (1 - EXPIRY_SPREAD * 2 / 3) and max(spreads) > 72 * (1 - EXPIRY_SPREAD / 3)

    def test_a_key_always_gets_the_same_expiry(self):
        assert spread_ttl_hours("bill-119-hr-1", 72) == spread_ttl_hours("bill-119-hr-1", 72)

    def test_spread_read_expires_at_the_keys_own_point(self, db_session, freeze_utcnow):
        freeze_utcnow(T0)
        api_cache_set(db_session, "t", "k", [1])
        own = spread_ttl_hours("k", 72)
        freeze_utcnow(T0 + timedelta(hours=own - 0.01))
        assert api_cache_get(db_session, "t", "k", spread=True) == [1]
        freeze_utcnow(T0 + timedelta(hours=own + 0.01))
        assert api_cache_get(db_session, "t", "k", spread=True) is None
        assert api_cache_get(db_session, "t", "k") == [1]  # unspread readers keep the full TTL

    def test_an_empty_payload_keeps_its_short_life(self, db_session, freeze_utcnow):
        freeze_utcnow(T0)
        api_cache_set(db_session, "t", "k", [])
        freeze_utcnow(T0 + timedelta(hours=EMPTY_RESPONSE_TTL_HOURS - 1))
        assert api_cache_get(db_session, "t", "k", spread=True) == []

    def test_written_since_expires_older_entries(self, db_session, freeze_utcnow):
        freeze_utcnow(T0)
        api_cache_set(db_session, "t", "k", [1])
        assert api_cache_get(db_session, "t", "k", written_since=T0 + timedelta(seconds=1)) is None
        assert api_cache_get(db_session, "t", "k", written_since=T0) == [1]


def _marker(db, last_run, covered_since):
    api_cache_set(
        db, "congress", congress.BILL_REFRESH_MARKER_KEY,
        {"lastRun": last_run.isoformat(), "coveredSince": covered_since.isoformat()},
    )


class TestBillRecordFreshness:
    """A current-Congress bill's record, past its TTL."""

    LATER = T0 + timedelta(days=10)  # past 72 h, inside the backstop

    def _write(self, db, freeze_utcnow):
        freeze_utcnow(T0)
        api_cache_set(db, "congress", ACTIONS_KEY, [{"text": "Referred."}])

    def test_kept_while_the_update_list_has_covered_it(self, db_session, freeze_utcnow):
        self._write(db_session, freeze_utcnow)
        freeze_utcnow(self.LATER)
        _marker(db_session, self.LATER - timedelta(hours=1), T0 - timedelta(days=1))
        assert congress._bill_record_get(db_session, TEST_CONGRESS, ACTIONS_KEY) == [{"text": "Referred."}]

    def test_the_backstop_still_ends_it(self, db_session, freeze_utcnow):
        self._write(db_session, freeze_utcnow)
        later = T0 + timedelta(hours=congress.BILL_RECORD_BACKSTOP_HOURS + 1)
        freeze_utcnow(later)
        _marker(db_session, later - timedelta(hours=1), T0 - timedelta(days=1))
        assert congress._bill_record_get(db_session, TEST_CONGRESS, ACTIONS_KEY) is None

    def test_not_kept_without_a_marker(self, db_session, freeze_utcnow):
        self._write(db_session, freeze_utcnow)
        freeze_utcnow(self.LATER)
        assert congress._bill_record_get(db_session, TEST_CONGRESS, ACTIONS_KEY) is None

    def test_not_kept_when_written_before_coverage_began(self, db_session, freeze_utcnow):
        self._write(db_session, freeze_utcnow)
        freeze_utcnow(self.LATER)
        _marker(db_session, self.LATER - timedelta(hours=1), T0 + timedelta(hours=1))
        assert congress._bill_record_get(db_session, TEST_CONGRESS, ACTIONS_KEY) is None

    def test_not_kept_when_the_refresh_has_stopped_running(self, db_session, freeze_utcnow):
        self._write(db_session, freeze_utcnow)
        freeze_utcnow(self.LATER)
        # Its last pass is older than the plain TTL: updates since are unapplied.
        _marker(db_session, self.LATER - timedelta(hours=73), T0 - timedelta(days=1))
        assert congress._bill_record_get(db_session, TEST_CONGRESS, ACTIONS_KEY) is None

    def test_a_previous_congress_bill_keeps_the_plain_ttl(self, db_session, freeze_utcnow):
        key = f"bill-actions-{TEST_CONGRESS - 1}-hr-7"
        freeze_utcnow(T0)
        api_cache_set(db_session, "congress", key, [{"text": "Referred."}])
        freeze_utcnow(self.LATER)
        _marker(db_session, self.LATER - timedelta(hours=1), T0 - timedelta(days=1))
        assert congress._bill_record_get(db_session, TEST_CONGRESS - 1, key) is None

    def test_a_bill_the_update_list_names_is_read_again(self, db_session, freeze_utcnow):
        self._write(db_session, freeze_utcnow)
        freeze_utcnow(T0 + timedelta(hours=1))
        _marker(db_session, T0 + timedelta(minutes=30), T0 - timedelta(days=1))
        marked = bill_refresh.mark_bill_records_stale(
            db_session, {"HR.7": {"congress": TEST_CONGRESS, "type": "HR", "number": "7"}},
        )
        assert marked == 1
        assert congress._bill_record_get(db_session, TEST_CONGRESS, ACTIONS_KEY) is None


class TestActionsFetchFailure:
    def _fail(self, monkeypatch):
        async def _none(client, url):
            return None
        monkeypatch.setattr(congress, "_fetch_with_retry", _none)

    def test_falls_back_to_the_cached_history(self, db_session, freeze_utcnow, monkeypatch):
        freeze_utcnow(T0)
        api_cache_set(db_session, "congress", ACTIONS_KEY, [{"text": "Referred."}])
        bill_refresh.mark_bill_records_stale(
            db_session, {"HR.7": {"congress": TEST_CONGRESS, "type": "HR", "number": "7"}},
        )
        self._fail(monkeypatch)
        got = asyncio.run(congress.fetch_bill_actions(None, db_session, TEST_CONGRESS, "hr", 7))
        assert got == [{"text": "Referred."}]

    def test_raises_with_nothing_cached(self, db_session, monkeypatch):
        self._fail(monkeypatch)
        with pytest.raises(congress.CongressUnavailable):
            asyncio.run(congress.fetch_bill_actions(None, db_session, TEST_CONGRESS, "hr", 7))


class TestCoverage:
    def test_starts_at_the_window_without_a_marker(self, db_session):
        since = T0 - timedelta(hours=24)
        assert bill_refresh._covered_since(db_session, T0, since) == since

    def test_carries_over_while_windows_join(self, db_session, freeze_utcnow):
        freeze_utcnow(T0)
        _marker(db_session, T0 - timedelta(hours=1), T0 - timedelta(days=3))
        since = bill_refresh._window_start(db_session, T0)
        assert bill_refresh._covered_since(db_session, T0, since) == T0 - timedelta(days=3)

    def test_starts_again_after_a_gap(self, db_session, freeze_utcnow):
        freeze_utcnow(T0)
        # Its last pass applied updates only up to eight days ago (a resume
        # point), and this window reaches back only _MAX_LOOKBACK.
        _marker(db_session, T0 - timedelta(days=8), T0 - timedelta(days=20))
        since = bill_refresh._window_start(db_session, T0)
        assert since == T0 - bill_refresh._MAX_LOOKBACK
        assert bill_refresh._covered_since(db_session, T0, since) == since

    def test_a_pass_records_coverage_and_marks_the_listed_bills(self, db_session, freeze_utcnow, monkeypatch):
        freeze_utcnow(T0)
        api_cache_set(db_session, "congress", ACTIONS_KEY, [{"text": "Referred."}])
        freeze_utcnow(T0 + timedelta(hours=2))

        async def _recent(client, since):
            return {"HR.7": {"congress": TEST_CONGRESS, "type": "HR", "number": "7",
                             "latestAction": {"text": "Referred.", "actionDate": "2026-09-01"}}}, None

        async def _no_laws(db, client, n):
            return 0

        monkeypatch.setattr(bill_refresh, "_fetch_recently_updated", _recent)
        monkeypatch.setattr(bill_refresh, "sync_laws", _no_laws)
        summary = asyncio.run(bill_refresh.refresh_bill_statuses(db=db_session))

        assert summary["records_marked_stale"] == 1
        marker = api_cache_get(db_session, "congress", bill_refresh.LAST_RUN_CACHE_KEY)
        assert marker["coveredSince"] == (T0 + timedelta(hours=2) - timedelta(hours=24)).isoformat()
        assert congress._bill_record_get(db_session, TEST_CONGRESS, ACTIONS_KEY) is None
