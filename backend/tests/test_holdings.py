"""Tests for holdings_pipeline (which report is kept per member) and the
holdings read path (holdings_service + the two API routes)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models import FinancialDisclosure, FinancialHolding, Representative, Senator
from app.pipeline import holdings_pipeline
from app.pipeline.fetch.fd_common import HoldingRow
from app.pipeline.fetch.house_fd import AnnualReport
from app.services.holdings_service import get_rep_holdings, get_senator_holdings


def _row(name="Apple Inc. (AAPL)", category="STOCKS", low=1001.0, high=15000.0, **kw) -> HoldingRow:
    return HoldingRow(
        asset_name=name, asset_type=kw.pop("asset_type", "ST"), category=category, owner=kw.pop("owner", "self"),
        value_text=kw.pop("value_text", f"${low} - ${high}"), value_low=low, value_high=high, **kw,
    )


def _house_filing(doc_id, year=2025, filing_date="2026-05-01", last="Doe", first="John", district="TX01"):
    return {
        "last": last, "first": first, "state_district": district, "filing_type": "O", "year": year,
        "filing_date": filing_date, "doc_id": doc_id, "pdf_url": f"https://clerk.example/{year}/{doc_id}.pdf",
    }


@pytest.fixture()
def rep(db_session):
    r = Representative(id="R1", name="John Doe", state="TX", district=1, party="R", is_current=True)
    db_session.add(r)
    db_session.commit()
    return r


@pytest.fixture()
def senator(db_session):
    s = Senator(id="S1", name="Tammy Baldwin", state="WI", party="D", is_current=True)
    db_session.add(s)
    db_session.commit()
    return s


async def _ingest_house(db_session, index_by_year, reports):
    async def index(_client, _db, year):
        return index_by_year.get(year, [])

    async def fetch(_client, _db, filing):
        return reports.get(filing["doc_id"])

    with patch.object(holdings_pipeline, "fetch_annual_filing_index", side_effect=index), \
         patch.object(holdings_pipeline, "fetch_house_annual", side_effect=fetch) as mock_fetch, \
         patch.object(holdings_pipeline, "utcnow") as mock_now:
        mock_now.return_value.year = 2026
        count = await holdings_pipeline.ingest_house_holdings(db_session, None)
    return count, mock_fetch


class TestIngestHouseHoldings:
    async def test_newest_year_and_latest_amendment_win(self, db_session, rep):
        index = {
            2025: [_house_filing("ORIG", filing_date="2026-05-01"), _house_filing("AMEND", filing_date="2026-08-01")],
            2024: [_house_filing("OLD", year=2024, filing_date="2025-05-01")],
        }
        reports = {
            "AMEND": AnnualReport("Member", [_row(), _row("Bank", "CASH", asset_type="BA")]),
            "ORIG": AnnualReport("Member", [_row()]),
            "OLD": AnnualReport("Member", [_row()]),
        }
        count, _ = await _ingest_house(db_session, index, reports)

        assert count == 2
        disclosure = db_session.query(FinancialDisclosure).one()
        assert (disclosure.filing_id, disclosure.report_year, disclosure.parsed) == ("AMEND", 2025, True)
        assert disclosure.representative_id == "R1"
        assert {h.category for h in disclosure.holdings} == {"STOCKS", "CASH"}

    async def test_falls_back_to_last_year_before_the_new_report_is_filed(self, db_session, rep):
        count, _ = await _ingest_house(
            db_session, {2024: [_house_filing("OLD", year=2024)]}, {"OLD": AnnualReport("Member", [_row()])},
        )
        assert count == 1
        assert db_session.query(FinancialDisclosure).one().report_year == 2024

    async def test_a_candidate_for_the_seat_is_not_the_member(self, db_session, rep):
        """Same surname and district, filed by someone running for the seat:
        the cover page's Status says so, and the member's own report is used."""
        index = {2025: [
            _house_filing("CAND", filing_date="2026-08-01"),
            _house_filing("MEMBER", filing_date="2026-05-01"),
        ]}
        reports = {
            "CAND": AnnualReport("Congressional Candidate", [_row("Candidate's house", "REAL_ESTATE")]),
            "MEMBER": AnnualReport("Member", [_row()]),
        }
        await _ingest_house(db_session, index, reports)
        assert db_session.query(FinancialDisclosure).one().filing_id == "MEMBER"

    async def test_a_scanned_report_is_stored_as_unparsed_with_its_link(self, db_session, rep):
        await _ingest_house(db_session, {2025: [_house_filing("SCAN")]}, {"SCAN": AnnualReport(None, None)})
        disclosure = db_session.query(FinancialDisclosure).one()
        assert disclosure.parsed is False
        assert disclosure.source_url.endswith("/SCAN.pdf")
        assert disclosure.holdings == []

    async def test_already_stored_report_is_not_refetched(self, db_session, rep):
        db_session.add(FinancialDisclosure(representative_id="R1", filing_id="DONE", report_year=2025, source_url="x"))
        db_session.commit()
        count, mock_fetch = await _ingest_house(db_session, {2025: [_house_filing("DONE")]}, {})
        assert count == 0
        mock_fetch.assert_not_called()

    async def test_newer_report_replaces_the_stored_one(self, db_session, rep):
        old = FinancialDisclosure(representative_id="R1", filing_id="OLD", report_year=2024, source_url="x")
        old.holdings.append(FinancialHolding(asset_name="Gone", category="STOCKS", value_low=1.0, value_high=2.0))
        db_session.add(old)
        db_session.commit()

        await _ingest_house(db_session, {2025: [_house_filing("NEW")]}, {"NEW": AnnualReport("Member", [_row()])})

        assert [d.filing_id for d in db_session.query(FinancialDisclosure).all()] == ["NEW"]
        assert [h.asset_name for h in db_session.query(FinancialHolding).all()] == ["Apple Inc. (AAPL)"]

    async def test_fetch_failure_keeps_the_stored_report(self, db_session, rep):
        db_session.add(FinancialDisclosure(representative_id="R1", filing_id="OLD", report_year=2024, source_url="x"))
        db_session.commit()
        await _ingest_house(db_session, {2025: [_house_filing("NEW")]}, {"NEW": None})
        assert db_session.query(FinancialDisclosure).one().filing_id == "OLD"


def _senate_filing(uuid, title="Annual Report for CY 2025", filed="2026-05-11", office="Baldwin, Tammy (Senator)",
                   paper=False, last="Baldwin", first="Tammy"):
    kind = "paper" if paper else "annual"
    return {
        "last": last, "first": first, "office": office, "title": title, "filed_date": filed,
        "report_url": f"https://efdsearch.senate.gov/search/view/{kind}/{uuid}/", "is_paper": paper,
    }


async def _ingest_senate(db_session, filings, parsed):
    async def fetch(_client, _db, filing):
        return parsed.get(filing["report_url"].rstrip("/").rsplit("/", 1)[-1])

    with patch.object(holdings_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value="tok"), \
         patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock, return_value=filings), \
         patch.object(holdings_pipeline, "fetch_senate_annual", side_effect=fetch):
        return await holdings_pipeline.ingest_senate_holdings(db_session, None)


class TestIngestSenateHoldings:
    async def test_newest_calendar_year_wins_over_a_later_filed_older_amendment(self, db_session, senator):
        filings = [
            _senate_filing("cy2025", filed="2026-05-11"),
            _senate_filing("cy2024amend", title="Annual Report for CY 2024 (Amendment 1)", filed="2026-06-01"),
        ]
        count = await _ingest_senate(db_session, filings, {"cy2025": [_row()], "cy2024amend": [_row(), _row()]})
        assert count == 1
        disclosure = db_session.query(FinancialDisclosure).one()
        assert (disclosure.filing_id, disclosure.report_year, disclosure.senator_id) == ("cy2025", 2025, "S1")

    async def test_candidate_and_non_annual_reports_are_ignored(self, db_session, senator):
        filings = [
            _senate_filing("cand", office="Candidate (Candidate)", title="Candidate Report", filed="2026-09-01"),
            _senate_filing("term", title="Termination Report", filed="2026-09-01"),
            _senate_filing("real", filed="2026-05-11"),
        ]
        await _ingest_senate(db_session, filings, {"cand": [_row()], "term": [_row()], "real": [_row()]})
        assert db_session.query(FinancialDisclosure).one().filing_id == "real"

    async def test_paper_report_is_recorded_unparsed(self, db_session, senator):
        filings = [_senate_filing("scan", title="Annual Report", filed="2026-08-13", office="Senator", paper=True)]
        await _ingest_senate(db_session, filings, {})
        disclosure = db_session.query(FinancialDisclosure).one()
        assert disclosure.parsed is False
        assert disclosure.report_year == 2025  # year before it was filed
        assert "/paper/" in disclosure.source_url

    async def test_electronic_report_that_fails_to_load_changes_nothing(self, db_session, senator):
        await _ingest_senate(db_session, [_senate_filing("broken")], {"broken": None})
        assert db_session.query(FinancialDisclosure).count() == 0

    async def test_no_session_no_search(self, db_session, senator):
        with patch.object(holdings_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value=None), \
             patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock) as mock_search:
            assert await holdings_pipeline.ingest_senate_holdings(db_session, None) == 0
        mock_search.assert_not_called()

    def test_report_year(self):
        year = holdings_pipeline._senate_report_year
        assert year({"title": "Annual Report for CY 2025 (Amendment 1)"}) == 2025
        assert year({"title": "New Filer Report for 03/24/2026"}) == 2026
        assert year({"title": "Annual Report", "filed_date": "2026-08-13"}) == 2025
        assert year({"title": "Annual Report", "filed_date": None}) is None


def _store(db_session, holdings, parsed=True, **owner):
    d = FinancialDisclosure(filing_id="F1", report_year=2025, filed_date="2026-05-15",
                            source_url="https://example.com/f.pdf", parsed=parsed, **owner)
    for h in holdings:
        d.holdings.append(h)
    db_session.add(d)
    db_session.commit()
    return d


def _h(name, category, low, high, **kw):
    return FinancialHolding(asset_name=name, category=category, value_low=low, value_high=high,
                            asset_type=kw.pop("asset_type", "ST"), value_text=kw.pop("value_text", "x"), **kw)


class TestHoldingsService:
    def test_unknown_member_is_none_and_no_report_is_unavailable(self, db_session, senator):
        assert get_senator_holdings(db_session, "nope") is None
        result = get_senator_holdings(db_session, "S1")
        assert result.available is False
        assert result.categories == [] and result.holdings == []

    def test_breakdown_shares_and_disclosed_sums(self, db_session, senator):
        _store(db_session, [
            _h("Apple", "STOCKS", 1001.0, 15000.0),         # midpoint 8000.5
            _h("Fund", "FUNDS", 15001.0, 50000.0),          # midpoint 32500.5
            _h("Ranch", "REAL_ESTATE", 1000000.0, 1000000.0, asset_type="RP"),  # open-ended: floor
            _h("Sold", "STOCKS", 0.0, 0.0),                 # nothing held at year end
            _h("Art", "OTHER", None, None, value_text="Undetermined"),
        ], senator_id="S1")

        result = get_senator_holdings(db_session, "S1")

        assert result.available and result.parsed
        assert (result.holdings_count, result.unvalued_count) == (5, 1)
        assert result.total_low == 1001.0 + 15001.0 + 1000000.0
        assert result.total_high == 15000.0 + 50000.0 + 1000000.0
        assert result.total_open_ended is True
        # Largest slice first; OTHER has no stated value so no slice.
        assert [c.category for c in result.categories] == ["REAL_ESTATE", "FUNDS", "STOCKS"]
        total = 1000000.0 + 32500.5 + 8000.5
        assert result.categories[0].share == pytest.approx(1000000.0 / total)
        assert sum(c.share for c in result.categories) == pytest.approx(1.0)
        stocks = result.categories[2]
        assert (stocks.count, stocks.label, stocks.open_ended) == (2, "Stocks", False)
        assert stocks.color.startswith("#")
        # Listed largest first; the unvalued holding last rather than dropped.
        assert [h.asset_name for h in result.holdings] == ["Ranch", "Fund", "Apple", "Sold", "Art"]
        assert result.holdings[0].value_open_ended is True
        assert result.holdings[3].value_open_ended is False  # 0/0 is "none", not open-ended
        assert result.holdings[1].category_label == "Mutual funds & ETFs"

    def test_category_filter_and_pagination(self, db_session, rep):
        _store(db_session, [_h(f"Stock {i:02d}", "STOCKS", 1001.0 * (i + 1), 15000.0 * (i + 1)) for i in range(20)]
               + [_h("Cash", "CASH", 1.0, 1000.0, asset_type="BA")], representative_id="R1")

        page2 = get_rep_holdings(db_session, "R1", page=2, per_page=15, category="STOCKS")
        assert (page2.total, page2.total_pages, page2.page, page2.category_filter) == (20, 2, 2, "STOCKS")
        assert len(page2.holdings) == 5
        # The breakdown always describes the whole report, not the filter.
        assert {c.category for c in page2.categories} == {"STOCKS", "CASH"}

        clamped = get_rep_holdings(db_session, "R1", page=99, per_page=15)
        assert clamped.page == 2

    def test_unparsed_report(self, db_session, senator):
        _store(db_session, [], parsed=False, senator_id="S1")
        result = get_senator_holdings(db_session, "S1")
        assert result.available is True and result.parsed is False
        assert result.source_url == "https://example.com/f.pdf"

    def test_deleting_a_member_deletes_their_holdings(self, db_session, senator):
        _store(db_session, [_h("Apple", "STOCKS", 1.0, 2.0)], senator_id="S1")
        db_session.delete(senator)
        db_session.commit()
        assert db_session.query(FinancialDisclosure).count() == 0
        assert db_session.query(FinancialHolding).count() == 0


class TestHoldingsRoutes:
    @pytest.fixture()
    def client(self, db_session):
        # Just the two routers, not app.main: importing the full app pulls
        # in the embedding stack, which the fast suite runs without.
        from fastapi import FastAPI

        from app.api import representatives, senators
        from app.database import get_db

        app = FastAPI()
        app.include_router(senators.router, prefix="/api")
        app.include_router(representatives.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: db_session
        return TestClient(app)

    def test_senator_route(self, client, db_session, senator):
        _store(db_session, [_h("Apple", "STOCKS", 1001.0, 15000.0, ticker="AAPL")], senator_id="S1")
        resp = client.get("/api/senators/S1/holdings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["holdingsCount"] == 1
        assert body["categories"][0]["label"] == "Stocks"
        assert body["holdings"][0]["valueOpenEnded"] is False
        assert "max-age" in resp.headers.get("cache-control", "")

    def test_rep_route_404_and_bad_category(self, client, rep):
        assert client.get("/api/representatives/nope/holdings").status_code == 404
        assert client.get("/api/representatives/R1/holdings?category=bogus").status_code == 422
        assert client.get("/api/representatives/R1/holdings").json()["available"] is False
