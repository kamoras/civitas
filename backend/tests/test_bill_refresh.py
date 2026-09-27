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

    def test_an_older_listed_action_never_replaces_a_newer_stored_one(self, db_session, actions_stub):
        """The listing can lag what the nightly pipeline stored from the bill
        itself: its older action must not win."""
        bill = _make_senate_bill(db_session, latest_action="Became Public Law.", latest_action_date="2026-07-25")
        recent = {"S.100": _feed_item("S.100", "Passed Senate with an amendment.", "2026-07-20")}

        summary = asyncio.run(bill_refresh._apply_updates(db_session, None, recent))

        assert summary["changed"] == 0
        assert (bill.latest_action, bill.latest_action_date) == ("Became Public Law.", "2026-07-25")
        assert actions_stub.calls == []  # not even fetched

    def test_a_same_day_listed_action_never_replaces_the_stored_one(self, db_session, actions_stub):
        """A date can't order two actions on one day, and the listing can lag
        a later one that day the nightly pipeline stored; that run settles it."""
        bill = _make_senate_bill(db_session, latest_action="Passed Senate.", latest_action_date="2026-07-20")
        recent = {"S.100": _feed_item("S.100", "Motion to proceed agreed to.", "2026-07-20")}

        summary = asyncio.run(bill_refresh._apply_updates(db_session, None, recent))

        assert summary["changed"] == 0
        assert bill.latest_action == "Passed Senate."

    def test_an_undated_listed_action_never_replaces_a_dated_one(self, db_session, actions_stub):
        bill = _make_senate_bill(db_session, latest_action="Passed Senate.", latest_action_date="2026-07-20")
        recent = {"S.100": _feed_item("S.100", "Something undated.", "")}

        assert asyncio.run(bill_refresh._apply_updates(db_session, None, recent))["changed"] == 0
        assert (bill.latest_action, bill.latest_action_date) == ("Passed Senate.", "2026-07-20")

    def test_a_row_with_no_date_takes_the_listed_action(self, db_session, actions_stub):
        bill = _make_senate_bill(db_session, latest_action="", latest_action_date="")
        actions_stub.result = []
        recent = {"S.100": _feed_item("S.100", "Passed Senate.", "2026-07-20")}

        assert asyncio.run(bill_refresh._apply_updates(db_session, None, recent))["changed"] == 1
        assert (bill.latest_action, bill.latest_action_date) == ("Passed Senate.", "2026-07-20")

    def test_a_row_that_moved_on_mid_pass_is_left_alone(self, db_session, actions_stub, monkeypatch):
        """The write rechecks the date: a pipeline that stored a same-day or
        newer action while the pass fetched keeps it — and a law it recorded
        stays a law, whatever the pass wrote."""
        bill = _make_senate_bill(db_session)
        db_session.commit()

        def fetch_after(update):
            async def _fetch(db, client, congress, bill_type, number):
                db.query(SponsoredBill).filter(SponsoredBill.id == bill.id).update(update)
                return [{"actionCode": "17000", "type": "Floor", "text": "Passed Senate."}]
            return _fetch

        recent = {"S.100": _feed_item("S.100", "Passed Senate with an amendment.", "2026-07-20")}
        monkeypatch.setattr(bill_refresh, "_fetch_fresh_actions", fetch_after(
            {"latest_action": "Became Public Law.", "latest_action_date": "2026-07-20", "is_law": True},
        ))
        assert asyncio.run(bill_refresh._apply_updates(db_session, None, recent))["changed"] == 0

        # Law recorded mid-pass under an older date: the pass's newer action
        # lands, and is_law stays set.
        db_session.query(SponsoredBill).update(
            {"latest_action": "Read twice.", "latest_action_date": "2026-07-01", "is_law": False},
        )
        db_session.commit()
        monkeypatch.setattr(bill_refresh, "_fetch_fresh_actions", fetch_after({"is_law": True}))
        assert asyncio.run(bill_refresh._apply_updates(db_session, None, recent))["changed"] == 1
        db_session.expire_all()
        row = db_session.get(SponsoredBill, bill.id)
        assert (row.latest_action_date, row.is_law, row.stage) == ("2026-07-20", True, "ENACTED")

    def test_becoming_law_on_the_stored_actions_day_is_recorded(self, db_session, actions_stub):
        """Congress.gov dates the public-law action the day it was signed —
        often the stored signing action's day — and it ends the history."""
        bill = _make_senate_bill(
            db_session, stage="TO_PRESIDENT", latest_action="Signed by President.", latest_action_date="2026-09-20",
        )
        actions_stub.result = []
        recent = {"S.100": _feed_item("S.100", "Became Public Law No: 119-52.", "2026-09-20")}

        assert asyncio.run(bill_refresh._apply_updates(db_session, None, recent))["changed"] == 1
        db_session.expire_all()
        row = db_session.get(SponsoredBill, bill.id)
        assert (row.latest_action, row.is_law, row.stage) == ("Became Public Law No: 119-52.", True, "ENACTED")

    @pytest.mark.parametrize("text, date", [
        # Cites a Public Law without becoming one.
        ("Motion to waive pursuant to section 904 of Public Law 93-344 agreed to.", "2026-09-22"),
        # Becomes law, but lags the stored action, or has no date to order by.
        ("Became Public Law No: 119-52.", "2026-09-19"),
        ("Became Public Law No: 119-52.", ""),
    ])
    def test_only_a_law_it_can_order_is_recorded(self, db_session, actions_stub, text, date):
        bill = _make_senate_bill(
            db_session, stage="PASSED_CHAMBER", latest_action="Passed House.", latest_action_date="2026-09-20",
        )
        actions_stub.result = [{"actionCode": "17000", "type": "Floor", "text": "Passed Senate."}]
        recent = {"S.100": _feed_item("S.100", text, date)}

        asyncio.run(bill_refresh._apply_updates(db_session, None, recent))
        db_session.expire_all()
        row = db_session.get(SponsoredBill, bill.id)
        assert row.is_law is False and row.stage != "ENACTED"
        if date <= "2026-09-20":
            assert (row.latest_action, row.latest_action_date) == ("Passed House.", "2026-09-20")

    def test_a_reused_row_id_is_not_written_as_another_bill(self, db_session, actions_stub, monkeypatch):
        """The nightly pipeline rewrites rows by delete and insert; SQLite
        can give the deleted row's id to another bill."""
        bill = _make_senate_bill(db_session)
        row_id = bill.id
        db_session.commit()

        async def reinserted(db, client, congress, bill_type, number):
            db.query(SponsoredBill).filter(SponsoredBill.id == row_id).delete()
            db.add(SponsoredBill(
                id=row_id, senator_id="s1", bill_id="S.250", title="Another bill", stage="REFERRED",
                congress=CURRENT, latest_action="Read twice.", latest_action_date="2026-07-01", bill_type="S",
            ))
            db.flush()
            return [{"actionCode": "17000", "type": "Floor", "text": "Passed Senate."}]

        monkeypatch.setattr(bill_refresh, "_fetch_fresh_actions", reinserted)
        recent = {"S.100": _feed_item("S.100", "Passed Senate.", "2026-07-20")}
        assert asyncio.run(bill_refresh._apply_updates(db_session, None, recent))["changed"] == 0
        db_session.expire_all()
        assert db_session.get(SponsoredBill, row_id).latest_action == "Read twice."

    def test_a_row_deleted_mid_pass_is_skipped_not_a_crash(self, db_session, actions_stub, monkeypatch):
        """A pipeline can rewrite a member's bills while a pass holds them."""
        bill = _make_senate_bill(db_session)
        db_session.commit()
        actions_stub.result = [{"actionCode": "17000", "type": "Floor", "text": "Passed Senate."}]

        async def deleted_meanwhile(db, client, congress, bill_type, number):
            db.query(SponsoredBill).filter(SponsoredBill.id == bill.id).delete()
            return actions_stub.result

        monkeypatch.setattr(bill_refresh, "_fetch_fresh_actions", deleted_meanwhile)
        recent = {"S.100": _feed_item("S.100", "Passed Senate with an amendment.", "2026-07-20")}
        assert asyncio.run(bill_refresh._apply_updates(db_session, None, recent))["changed"] == 0

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
