"""Tests for the unified "bills currently moving through Congress" feed
(app.services.bill_service.get_bills_in_flight).

Uses the shared in-memory-SQLite `db_session` fixture from conftest.py.
"""

import json

import pytest

from app.config import settings
from app.models import ActionIssue, Representative, RepSponsoredBill, Senator, SponsoredBill
from app.services.bill_service import clear_bill_collection_cache, get_bills_in_flight

CURRENT = settings.CURRENT_CONGRESS


@pytest.fixture(autouse=True)
def _reset_bill_collection_cache():
    # _collect_bills caches its result at module scope for 120s in
    # production (see bill_service.py) — without this, a cache populated
    # by an earlier test's in-memory SQLite db would leak into a later
    # test's assertions, since the cache doesn't know db_session gives
    # every test a fresh database.
    clear_bill_collection_cache()
    yield
    clear_bill_collection_cache()


def _make_senator(db, id="s1", name="Sen. Alpha", state="CA", party="D", is_current=True):
    senator = Senator(id=id, name=name, state=state, party=party, is_current=is_current)
    db.add(senator)
    db.flush()
    return senator


def _make_rep(db, id="r1", name="Rep. Beta", state="TX", party="R", is_current=True):
    rep = Representative(id=id, name=name, state=state, party=party, is_current=is_current)
    db.add(rep)
    db.flush()
    return rep


def _make_sponsored_bill(db, senator_id, bill_id, stage, congress=CURRENT, title="A bill", latest_action_date="2026-01-01"):
    bill = SponsoredBill(
        senator_id=senator_id,
        bill_id=bill_id,
        title=title,
        stage=stage,
        congress=congress,
        latest_action_date=latest_action_date,
    )
    db.add(bill)
    db.flush()
    return bill


def _make_rep_sponsored_bill(db, representative_id, bill_id, stage, congress=CURRENT, title="A bill", latest_action_date="2026-01-01"):
    bill = RepSponsoredBill(
        representative_id=representative_id,
        bill_id=bill_id,
        title=title,
        stage=stage,
        congress=congress,
        latest_action_date=latest_action_date,
    )
    db.add(bill)
    db.flush()
    return bill


def _make_action_issue(db, bill_ids, is_current=True, date="2026-07-01", rank=1):
    issue = ActionIssue(
        date=date,
        rank=rank,
        title="Some trending issue",
        related_bill_ids=json.dumps([{"name": b, "id": b, "url": ""} for b in bill_ids]),
        is_current=is_current,
    )
    db.add(issue)
    db.flush()
    return issue


class TestUnionAcrossChambers:
    def test_unions_senate_and_house_bills(self, db_session):
        senator = _make_senator(db_session)
        rep = _make_rep(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        _make_rep_sponsored_bill(db_session, rep.id, "HR.1", "IN_COMMITTEE")

        result = get_bills_in_flight(db_session)

        assert result.total == 2
        chambers = {b.chamber for b in result.bills}
        assert chambers == {"senate", "house"}

    def test_excludes_non_current_members(self, db_session):
        senator = _make_senator(db_session, is_current=False)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")

        result = get_bills_in_flight(db_session)

        assert result.total == 0

    def test_excludes_bills_from_a_prior_congress(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED", congress=CURRENT - 1)

        result = get_bills_in_flight(db_session)

        assert result.total == 0

    def test_blank_stage_defaults_to_introduced(self, db_session):
        # Rows written before the stage classifier existed (or not yet
        # backfilled) have stage="" — the feed should still place them
        # somewhere sane rather than drop or mis-bucket them.
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", stage="")

        result = get_bills_in_flight(db_session)

        assert result.bills[0].stage == "INTRODUCED"


class TestFiltering:
    def test_filters_by_stage(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        _make_sponsored_bill(db_session, senator.id, "S.2", "ENACTED")

        result = get_bills_in_flight(db_session, stage="ENACTED")

        assert result.total == 1
        assert result.bills[0].bill_id == "S.2"

    def test_filters_by_chamber(self, db_session):
        senator = _make_senator(db_session)
        rep = _make_rep(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        _make_rep_sponsored_bill(db_session, rep.id, "HR.1", "INTRODUCED")

        result = get_bills_in_flight(db_session, chamber="house")

        assert result.total == 1
        assert result.bills[0].chamber == "house"

    def test_filters_by_party(self, db_session):
        dem = _make_senator(db_session, id="s1", party="D")
        rep_member = _make_senator(db_session, id="s2", party="R")
        _make_sponsored_bill(db_session, dem.id, "S.1", "INTRODUCED")
        _make_sponsored_bill(db_session, rep_member.id, "S.2", "INTRODUCED")

        result = get_bills_in_flight(db_session, party="R")

        assert result.total == 1
        assert result.bills[0].sponsor_party == "R"

    def test_filters_by_search_query_case_insensitive(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED", title="Clean Water Act")
        _make_sponsored_bill(db_session, senator.id, "S.2", "INTRODUCED", title="Tax Reform Act")

        result = get_bills_in_flight(db_session, q="clean water")

        assert result.total == 1
        assert result.bills[0].bill_id == "S.1"

    def test_search_matches_sponsor_name(self, db_session):
        warren = _make_senator(db_session, id="s1", name="Sen. Elizabeth Warren")
        other = _make_senator(db_session, id="s2", name="Sen. Someone Else")
        _make_sponsored_bill(db_session, warren.id, "S.1", "INTRODUCED", title="A bill")
        _make_sponsored_bill(db_session, other.id, "S.2", "INTRODUCED", title="Another bill")

        result = get_bills_in_flight(db_session, q="warren")

        assert result.total == 1
        assert result.bills[0].bill_id == "S.1"

    def test_search_matches_bill_id(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.4521", "INTRODUCED", title="A bill")
        _make_sponsored_bill(db_session, senator.id, "S.99", "INTRODUCED", title="Another bill")

        result = get_bills_in_flight(db_session, q="4521")

        assert result.total == 1
        assert result.bills[0].bill_id == "S.4521"

    def test_stage_counts_reflect_unfiltered_set(self, db_session):
        # The funnel counts should describe the whole pipeline, not just
        # whatever the current filter narrowed the list down to.
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        _make_sponsored_bill(db_session, senator.id, "S.2", "ENACTED")

        result = get_bills_in_flight(db_session, stage="ENACTED")

        assert result.stage_counts == {"INTRODUCED": 1, "ENACTED": 1}


class TestPagination:
    def test_paginates_results(self, db_session):
        senator = _make_senator(db_session)
        for i in range(5):
            _make_sponsored_bill(db_session, senator.id, f"S.{i}", "INTRODUCED")

        page1 = get_bills_in_flight(db_session, page=1, per_page=2)
        page2 = get_bills_in_flight(db_session, page=2, per_page=2)
        page3 = get_bills_in_flight(db_session, page=3, per_page=2)

        assert page1.total == 5
        assert page1.total_pages == 3
        assert len(page1.bills) == 2
        assert len(page2.bills) == 2
        assert len(page3.bills) == 1

    def test_sorts_by_latest_action_date_descending(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED", latest_action_date="2026-01-01")
        _make_sponsored_bill(db_session, senator.id, "S.2", "INTRODUCED", latest_action_date="2026-06-01")

        result = get_bills_in_flight(db_session)

        assert [b.bill_id for b in result.bills] == ["S.2", "S.1"]


class TestHotSort:
    def test_hot_sort_excludes_bills_with_no_action_center_mentions(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        _make_action_issue(db_session, ["S.1"])

        _make_sponsored_bill(db_session, senator.id, "S.2", "INTRODUCED")

        result = get_bills_in_flight(db_session, sort="hot")

        assert result.total == 1
        assert result.bills[0].bill_id == "S.1"

    def test_hot_sort_ranks_by_mention_count(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        _make_sponsored_bill(db_session, senator.id, "S.2", "INTRODUCED")
        _make_action_issue(db_session, ["S.1"])
        _make_action_issue(db_session, ["S.1", "S.2"])
        _make_action_issue(db_session, ["S.2"])
        # S.2 is mentioned by 2 issues, S.1 by 2 issues as well — bump S.2
        # with one more so ranking is unambiguous.
        _make_action_issue(db_session, ["S.2"])

        result = get_bills_in_flight(db_session, sort="hot")

        assert [b.bill_id for b in result.bills] == ["S.2", "S.1"]
        assert result.bills[0].mention_count == 3
        assert result.bills[1].mention_count == 2

    def test_hot_sort_ignores_non_current_issues(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        _make_action_issue(db_session, ["S.1"], is_current=False)

        result = get_bills_in_flight(db_session, sort="hot")

        assert result.total == 0

    def test_mention_count_is_populated_regardless_of_sort_mode(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        _make_action_issue(db_session, ["S.1"])

        result = get_bills_in_flight(db_session, sort="recent")

        assert result.bills[0].mention_count == 1


class TestStaleSort:
    def test_stale_sort_orders_oldest_action_first(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "IN_COMMITTEE", latest_action_date="2026-06-01")
        _make_sponsored_bill(db_session, senator.id, "S.2", "IN_COMMITTEE", latest_action_date="2025-01-01")
        _make_sponsored_bill(db_session, senator.id, "S.3", "IN_COMMITTEE", latest_action_date="2026-01-01")

        result = get_bills_in_flight(db_session, sort="stale")

        assert [b.bill_id for b in result.bills] == ["S.2", "S.3", "S.1"]

    def test_stale_sort_combines_with_stage_filter(self, db_session):
        # The intended usage: staleness is only meaningful within one
        # stage, since normal dwell time varies wildly by stage.
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "IN_COMMITTEE", latest_action_date="2025-01-01")
        _make_sponsored_bill(db_session, senator.id, "S.2", "ENACTED", latest_action_date="2024-01-01")

        result = get_bills_in_flight(db_session, sort="stale", stage="IN_COMMITTEE")

        assert result.total == 1
        assert result.bills[0].bill_id == "S.1"


class TestCollectionCache:
    def test_cached_result_reflects_data_at_first_call(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")

        first = get_bills_in_flight(db_session)
        assert first.total == 1

        # Written after the cache was populated — within the TTL window a
        # second call should still see the cached (pre-S.2) snapshot, not
        # requery the database.
        _make_sponsored_bill(db_session, senator.id, "S.2", "INTRODUCED")
        second = get_bills_in_flight(db_session)

        assert second.total == 1

    def test_clearing_cache_picks_up_new_data(self, db_session):
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED")
        get_bills_in_flight(db_session)

        _make_sponsored_bill(db_session, senator.id, "S.2", "INTRODUCED")
        clear_bill_collection_cache()
        result = get_bills_in_flight(db_session)

        assert result.total == 2

    def test_unfiltered_call_does_not_mutate_the_cached_list_order(self, db_session):
        # get_bills_in_flight sorts `filtered` in place; when no filter is
        # active `filtered is all_rows`, the exact list _collect_bills
        # returned. If that were the cached list itself (not a copy), the
        # first call's sort would permanently reorder every later cache hit.
        senator = _make_senator(db_session)
        _make_sponsored_bill(db_session, senator.id, "S.1", "INTRODUCED", latest_action_date="2026-01-01")
        _make_sponsored_bill(db_session, senator.id, "S.2", "INTRODUCED", latest_action_date="2026-06-01")

        recent = get_bills_in_flight(db_session, sort="recent")
        stale = get_bills_in_flight(db_session, sort="stale")

        assert [b.bill_id for b in recent.bills] == ["S.2", "S.1"]
        assert [b.bill_id for b in stale.bills] == ["S.1", "S.2"]
