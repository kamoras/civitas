"""Tests for the unified "bills currently moving through Congress" feed
(app.services.bill_service.get_bills_in_flight).

Uses the shared in-memory-SQLite `db_session` fixture from conftest.py.
"""

from app.config import settings
from app.models import Representative, RepSponsoredBill, Senator, SponsoredBill
from app.services.bill_service import get_bills_in_flight

CURRENT = settings.CURRENT_CONGRESS


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
