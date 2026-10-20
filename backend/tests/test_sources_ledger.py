"""The homepage's source ledger: each source's count and newest record,
counted from the stored records."""

from datetime import datetime

from fastapi.testclient import TestClient

from app.api import sources
from app.database import get_db
from app.main import app
from app.models import ApiCache, ExploreDocument, RollCall, Senator, SponsoredBill, StockTrade


def _by_label(rows):
    return {r["label"]: r for r in rows}


def test_counts_and_dates_from_the_records(db_session):
    db_session.add_all([
        RollCall(chamber="senate", congress=119, session=2, number=1, date="2026-09-29"),
        RollCall(chamber="house", congress=119, session=2, number=2, date="2026-09-30"),
        # The last Congress's vote is outside the scored window.
        RollCall(chamber="senate", congress=118, session=2, number=3, date="2024-12-20"),
        Senator(id="pat-able", name="Pat Able", state="AA", party="D", is_current=True, total_raised=10.0),
        Senator(id="lee-baker", name="Lee Baker", state="AA", party="R", is_current=True, total_raised=5.0),
        # No filings, or no longer serving: not counted.
        Senator(id="kim-cole", name="Kim Cole", state="BB", party="R", is_current=True, total_raised=0.0),
        Senator(id="ash-dorn", name="Ash Dorn", state="BB", party="D", is_current=False, total_raised=9.0),
        SponsoredBill(senator_id="pat-able", bill_id="S.1", title="A bill", congress=119,
                      latest_action_date="2026-10-01"),
        StockTrade(senator_id="pat-able", asset_name="Fund", transaction_type="purchase",
                   disclosure_date="2026-10-02", filing_id="f1"),
        ExploreDocument(source=sources._FEDERAL_REGISTER, doc_type="Final Rule", title="A rule",
                        date="2026-10-03"),
        ApiCache(tier="fec", cache_key="k", data_json="{}", cached_at=datetime(2026, 10, 4, 5)),
    ])
    db_session.commit()
    rows = _by_label(sources.source_ledger(db_session))
    assert rows["Votes"]["coverage"] == "2 roll calls, 119th Congress"
    assert rows["Votes"]["latest"] == "2026-09-30"
    assert rows["Bills"]["count"] == 1 and rows["Bills"]["latest"] == "2026-10-01"
    assert rows["Money"]["coverage"] == "2 serving members with campaign filings"
    assert (rows["Money"]["latest"], rows["Money"]["dateLabel"]) == ("2026-10-04", "last read")
    assert rows["Rules"]["latest"] == "2026-10-03"
    assert rows["Trades"]["latest"] == "2026-10-02"


def test_an_empty_source_is_listed_with_nothing_on_record(db_session):
    rows = _by_label(sources.source_ledger(db_session))
    assert rows["Court"]["count"] == 0 and rows["Court"]["latest"] is None
    assert len(rows) == 7


def test_ordinals():
    assert [sources._ordinal(n) for n in (119, 121, 122, 123, 111, 112)] == [
        "119th", "121st", "122nd", "123rd", "111th", "112th"]


def test_the_endpoint(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        r = TestClient(app).get("/api/sources")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200 and len(r.json()["sources"]) == 7
    assert r.headers["cache-control"].startswith("public, max-age=300")
