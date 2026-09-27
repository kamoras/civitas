"""Tests for the hourly incremental bill-status refresh
(app.pipeline.bill_refresh), which keeps sponsored-bill rows current
between nightly pipeline runs.

Uses the shared in-memory-SQLite `db_session` fixture from conftest.py.
The Congress.gov fetchers are monkeypatched — these tests exercise the
matching/update logic, not the HTTP layer.
"""

import asyncio
from datetime import timedelta

import pytest

from app.config import settings
from app.models import Representative, RepSponsoredBill, Senator, SponsoredBill
from app.pipeline import bill_refresh
from app.pipeline.cache import api_cache_set
from app.time_utils import utcnow

CURRENT = settings.CURRENT_CONGRESS


def _make_senate_bill(
    db, bill_id="S.100", stage="REFERRED", congress=CURRENT,
    latest_action="Read twice and referred to the Committee on Finance.",
    latest_action_date="2026-07-01", is_law=False,
):
    senator = db.get(Senator, "s1") or Senator(id="s1", name="Sen. Alpha", state="CA", party="D", is_current=True)
    db.add(senator)
    bill = SponsoredBill(
        senator_id="s1", bill_id=bill_id, title="A bill", stage=stage, congress=congress,
        latest_action=latest_action, latest_action_date=latest_action_date, is_law=is_law,
        bill_type=bill_id.split(".")[0],
    )
    db.add(bill)
    db.flush()
    return bill


def _make_house_bill(db, bill_id="HR.7", stage="REFERRED", latest_action="Referred to committee.", latest_action_date="2026-07-01"):
    rep = db.get(Representative, "r1") or Representative(id="r1", name="Rep. Beta", state="TX", party="R", is_current=True)
    db.add(rep)
    bill = RepSponsoredBill(
        representative_id="r1", bill_id=bill_id, title="A bill", stage=stage, congress=CURRENT,
        latest_action=latest_action, latest_action_date=latest_action_date,
        bill_type=bill_id.split(".")[0],
    )
    db.add(bill)
    db.flush()
    return bill


def _feed_item(bill_id, text, date, congress=CURRENT):
    bill_type, number = bill_id.split(".")
    return {
        "congress": congress,
        "type": bill_type,
        "number": number,
        "latestAction": {"text": text, "actionDate": date},
    }


@pytest.fixture()
def actions_stub(monkeypatch):
    """Replace the per-bill actions fetch; tests set `.result` and read
    `.calls` to assert how many network round trips would have happened."""
    class Stub:
        result: list[dict] = []
        calls: list[tuple] = []

    async def _fake(db, client, congress, bill_type, number):
        Stub.calls.append((congress, bill_type, number))
        return Stub.result

    monkeypatch.setattr(bill_refresh, "_fetch_fresh_actions", _fake)
    return Stub


class TestApplyUpdates:
    def test_updates_action_and_stage_when_latest_action_moved(self, db_session, actions_stub):
        bill = _make_senate_bill(db_session)
        actions_stub.result = [{"actionCode": "17000", "type": "Floor", "text": "Passed Senate."}]
        recent = {"S.100": _feed_item("S.100", "Passed Senate with an amendment.", "2026-07-20")}

        summary = asyncio.run(bill_refresh._apply_updates(db_session, None, recent))

        assert summary["changed"] == 1
        assert bill.latest_action == "Passed Senate with an amendment."
        assert bill.latest_action_date == "2026-07-20"
        assert bill.stage == "PASSED_CHAMBER"

    def test_updates_house_rows_too(self, db_session, actions_stub):
        bill = _make_house_bill(db_session)
        actions_stub.result = [{"actionCode": "H15001", "type": "Committee", "text": "Markup held."}]
        recent = {"HR.7": _feed_item("HR.7", "Committee Consideration and Mark-up Session Held.", "2026-07-21")}

        summary = asyncio.run(bill_refresh._apply_updates(db_session, None, recent))

        assert summary["changed"] == 1
        assert bill.stage == "IN_COMMITTEE"

    def test_skips_bills_whose_latest_action_did_not_change(self, db_session, actions_stub):
        # updateDate churns for reasons that don't move a bill (summaries
        # posted, cosponsors added) — those must not trigger actions fetches.
        bill = _make_senate_bill(db_session)
        recent = {"S.100": _feed_item("S.100", bill.latest_action, bill.latest_action_date)}

        summary = asyncio.run(bill_refresh._apply_updates(db_session, None, recent))

        assert summary == {"matched": 1, "changed": 0, "action_fetches": 0, "skipped_at_cap": 0}
        assert actions_stub.calls == []

    def test_ignores_untracked_and_prior_congress_bills(self, db_session, actions_stub):
        _make_senate_bill(db_session, bill_id="S.100", congress=CURRENT - 1)
        recent = {
            "S.100": _feed_item("S.100", "Passed Senate.", "2026-07-20"),
            "S.999": _feed_item("S.999", "Passed Senate.", "2026-07-20"),
        }

        summary = asyncio.run(bill_refresh._apply_updates(db_session, None, recent))

        assert summary["matched"] == 0
        assert summary["changed"] == 0

    def test_empty_actions_fetch_does_not_regress_stage(self, db_session, actions_stub):
        # classify_bill_stage_from_actions falls back to INTRODUCED on an
        # empty action list — a failed fetch must keep the stored stage.
        bill = _make_senate_bill(db_session, stage="PASSED_CHAMBER")
        actions_stub.result = []
        recent = {"S.100": _feed_item("S.100", "Referred to House committee.", "2026-07-22")}

        asyncio.run(bill_refresh._apply_updates(db_session, None, recent))

        assert bill.stage == "PASSED_CHAMBER"
        assert bill.latest_action == "Referred to House committee."

    def test_public_law_action_sets_is_law_and_enacted(self, db_session, actions_stub):
        bill = _make_senate_bill(db_session, stage="TO_PRESIDENT")
        actions_stub.result = []
        recent = {"S.100": _feed_item("S.100", "Became Public Law No: 119-52.", "2026-07-22")}

        asyncio.run(bill_refresh._apply_updates(db_session, None, recent))

        assert bill.is_law is True
        assert bill.stage == "ENACTED"


class TestWindowStart:
    def test_defaults_to_24h_lookback_without_a_marker(self, db_session):
        now = utcnow()
        assert bill_refresh._window_start(db_session, now) == now - timedelta(hours=24)

    def test_uses_stored_marker_minus_overlap(self, db_session):
        now = utcnow()
        last_run = now - timedelta(hours=1)
        api_cache_set(
            db_session, "congress", bill_refresh.LAST_RUN_CACHE_KEY,
            {"lastRun": last_run.isoformat()},
        )

        start = bill_refresh._window_start(db_session, now)

        assert start == last_run - bill_refresh._WINDOW_OVERLAP

    def test_clamps_a_very_old_marker_to_max_lookback(self, db_session):
        now = utcnow()
        # Just inside the max-lookback window, but with the overlap
        # subtracted it would cross it — the clamp keeps it at the edge.
        api_cache_set(
            db_session, "congress", bill_refresh.LAST_RUN_CACHE_KEY,
            {"lastRun": (now - bill_refresh._MAX_LOOKBACK + timedelta(minutes=10)).isoformat()},
        )

        start = bill_refresh._window_start(db_session, now)

        assert start == now - bill_refresh._MAX_LOOKBACK


class TestFetchRecentlyUpdated:
    def test_maps_to_our_bill_id_format_and_filters_old_congresses(self, monkeypatch):
        pages = [{
            "bills": [
                {"congress": CURRENT, "type": "hr", "number": "22", "latestAction": {"text": "x", "actionDate": "2026-07-20"}},
                {"congress": CURRENT - 1, "type": "s", "number": "1", "latestAction": {"text": "old", "actionDate": "2020-01-01"}},
                {"congress": CURRENT, "type": "s", "number": "4967", "latestAction": {"text": "y", "actionDate": "2026-07-19"}},
            ],
        }]

        async def _fake(client, url):
            return pages.pop(0) if pages else {"bills": []}

        monkeypatch.setattr(bill_refresh, "_fetch_with_retry", _fake)

        found = asyncio.run(bill_refresh._fetch_recently_updated(None, utcnow()))

        assert set(found) == {"HR.22", "S.4967"}


class TestRefreshBillStatuses:
    def test_full_cycle_stores_the_last_run_marker(self, db_session, monkeypatch, actions_stub):
        bill = _make_senate_bill(db_session)
        actions_stub.result = [{"actionCode": "17000", "type": "Floor", "text": "Passed Senate."}]

        async def _fake_recent(client, since):
            return {"S.100": _feed_item("S.100", "Passed Senate.", "2026-07-20")}

        monkeypatch.setattr(bill_refresh, "_fetch_recently_updated", _fake_recent)
        # Don't spawn the real cache-warm thread from a test.
        import app.services.bill_service as bill_service
        monkeypatch.setattr(bill_service, "warm_bill_collection_cache", lambda: None)

        summary = asyncio.run(bill_refresh.refresh_bill_statuses(db=db_session))

        assert summary["status"] == "completed"
        assert summary["changed"] == 1
        assert bill.stage == "PASSED_CHAMBER"
        from app.pipeline.cache import api_cache_get
        marker = api_cache_get(db_session, "congress", bill_refresh.LAST_RUN_CACHE_KEY)
        assert marker and marker.get("lastRun")
        assert not bill_refresh.is_bill_refresh_running()
