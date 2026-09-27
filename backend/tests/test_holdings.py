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


@pytest.fixture(autouse=True)
def _fresh_breakdown_cache():
    """Each test's in-memory database restarts ids at 1; never let one
    test's cached breakdown answer another's."""
    from app.services import holdings_service

    holdings_service._breakdown_cache.clear()
    yield
    holdings_service._breakdown_cache.clear()


@pytest.fixture(autouse=True)
def _no_live_probes():
    """The outage probes make real requests; by default every stored report
    reads as gone. Tests about the probe patch it themselves (inside this)."""
    with patch.object(holdings_pipeline, "house_report_still_loads", AsyncMock(return_value=False)), \
         patch.object(holdings_pipeline, "senate_report_still_loads", AsyncMock(return_value=False)):
        yield


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


class _Clock:
    """time.monotonic for the holdings phases, advanced by the fetches."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def _ingest_house(db_session, index_by_year, reports, on_fetch=None, on_index=None):
    async def index(_client, _db, year):
        if on_index is not None:
            on_index(year)
        return index_by_year.get(year, [])

    async def fetch(_client, _db, filing, deadline=None):
        if on_fetch is not None:
            on_fetch(filing)
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
        assert (disclosure.filing_id, _year(disclosure.as_of_date), disclosure.parsed) == ("AMEND", 2025, True)
        assert disclosure.representative_id == "R1"
        assert {h.category for h in disclosure.holdings} == {"STOCKS", "CASH"}

    async def test_falls_back_to_last_year_before_the_new_report_is_filed(self, db_session, rep):
        count, _ = await _ingest_house(
            db_session, {2024: [_house_filing("OLD", year=2024)]}, {"OLD": AnnualReport("Member", [_row()])},
        )
        assert count == 1
        assert _year(db_session.query(FinancialDisclosure).one().as_of_date) == 2024

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
        await _ingest_house(
            db_session, {2025: [_house_filing("SCAN")]}, {"SCAN": AnnualReport(None, None, "scanned")},
        )
        disclosure = db_session.query(FinancialDisclosure).one()
        assert disclosure.parsed is False
        assert disclosure.unreadable_reason == "scanned"
        assert disclosure.source_url.endswith("/SCAN.pdf")
        assert disclosure.holdings == []

    async def test_already_stored_report_is_not_refetched(self, db_session, rep):
        db_session.add(FinancialDisclosure(
            representative_id="R1", filing_id="DONE", as_of_date="2025-12-31", source_url="x",
            parser_version=holdings_pipeline.HOUSE_PARSER_VERSION,
        ))
        db_session.commit()
        count, mock_fetch = await _ingest_house(db_session, {2025: [_house_filing("DONE")]}, {})
        assert count == 0
        mock_fetch.assert_not_called()

    async def test_same_report_read_by_an_older_parser_is_re_read(self, db_session, rep):
        """A parser fix must reach reports already ingested, not only members
        who happen to file something new."""
        db_session.add(FinancialDisclosure(
            representative_id="R1", filing_id="DONE", as_of_date="2025-12-31", source_url="x",
            parser_version=holdings_pipeline.HOUSE_PARSER_VERSION - 1,
        ))
        db_session.commit()
        count, mock_fetch = await _ingest_house(
            db_session, {2025: [_house_filing("DONE")]}, {"DONE": AnnualReport("Member", [_row(), _row()])},
        )
        assert count == 2
        stored = db_session.query(FinancialDisclosure).one()
        assert stored.parser_version == holdings_pipeline.HOUSE_PARSER_VERSION

    async def test_an_empty_index_fails_the_phase(self, db_session, rep):
        with pytest.raises(RuntimeError):
            await _ingest_house(db_session, {}, {})

    async def test_newer_report_replaces_the_stored_one(self, db_session, rep):
        old = FinancialDisclosure(representative_id="R1", filing_id="OLD", as_of_date="2024-12-31", source_url="x")
        old.holdings.append(FinancialHolding(asset_name="Gone", category="STOCKS", value_low=1.0, value_high=2.0))
        db_session.add(old)
        db_session.commit()

        await _ingest_house(db_session, {2025: [_house_filing("NEW")]}, {"NEW": AnnualReport("Member", [_row()])})

        assert [d.filing_id for d in db_session.query(FinancialDisclosure).all()] == ["NEW"]
        assert [h.asset_name for h in db_session.query(FinancialHolding).all()] == ["Apple Inc. (AAPL)"]

    async def test_fetch_failure_keeps_the_stored_report(self, db_session, rep):
        db_session.add(FinancialDisclosure(representative_id="R1", filing_id="OLD", as_of_date="2024-12-31", source_url="x"))
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
    """`parsed` maps a report id to its holdings list, None for an unreadable
    report, or is missing the id for a page that failed to load."""
    async def fetch(_client, _db, filing):
        report_id = filing["report_url"].rstrip("/").rsplit("/", 1)[-1]
        if filing.get("is_paper"):
            return AnnualReport(None, None, "scanned", live=False)  # no request, as the real fetch
        if report_id not in parsed:
            return None
        holdings = parsed[report_id]
        return AnnualReport(None, holdings, None if holdings is not None else "unrecognized")

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
        assert (disclosure.filing_id, _year(disclosure.as_of_date), disclosure.senator_id) == ("cy2025", 2025, "S1")

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
        assert (disclosure.parsed, disclosure.unreadable_reason) == (False, "scanned")
        # A paper filing's year isn't stated, so none is claimed.
        assert _year(disclosure.as_of_date) is None
        assert disclosure.report_label == "annual report filed 2026-08-13"
        assert "/paper/" in disclosure.source_url

    async def test_a_page_that_fails_to_load_changes_nothing(self, db_session, senator):
        await _ingest_senate(db_session, [_senate_filing("broken")], {})
        assert db_session.query(FinancialDisclosure).count() == 0

    async def test_an_unreadable_electronic_report_is_stored_unparsed_and_linked(self, db_session, senator):
        """Not skipped: a skipped report left the member with no section at
        all (reading as "no disclosure") and was re-fetched every night."""
        await _ingest_senate(db_session, [_senate_filing("odd")], {"odd": None})
        disclosure = db_session.query(FinancialDisclosure).one()
        assert (disclosure.parsed, disclosure.unreadable_reason) == (False, "unrecognized")
        assert disclosure.source_url.endswith("/odd/")

    async def test_no_session_fails_the_phase_without_searching(self, db_session, senator):
        with patch.object(holdings_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value=None), \
             patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock) as mock_search:
            with pytest.raises(RuntimeError):
                await holdings_pipeline.ingest_senate_holdings(db_session, None)
        mock_search.assert_not_called()

    async def test_a_search_with_no_senators_reports_fails_the_phase(self, db_session, senator):
        """search_filings returns [] on any browser failure — every senator
        files yearly, so none over two years is a broken search."""
        with pytest.raises(RuntimeError):
            await _ingest_senate(db_session, [], {})
        with pytest.raises(RuntimeError):
            await _ingest_senate(
                db_session, [_senate_filing("c", office="Candidate (Candidate)", title="Candidate Report")], {},
            )

    async def test_stored_current_report_is_not_refetched(self, db_session, senator):
        db_session.add(FinancialDisclosure(
            senator_id="S1", filing_id="cy2025", as_of_date="2025-12-31", source_url="x",
            parser_version=holdings_pipeline.SENATE_PARSER_VERSION,
        ))
        db_session.commit()
        with patch.object(holdings_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value="tok"), \
             patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock,
                          return_value=[_senate_filing("cy2025")]), \
             patch.object(holdings_pipeline, "fetch_senate_annual", new_callable=AsyncMock) as mock_fetch:
            assert await holdings_pipeline.ingest_senate_holdings(db_session, None) == 0
        mock_fetch.assert_not_called()

    def test_report_year(self):
        def year(f):
            return _year(holdings_pipeline._senate_as_of(f))

        assert year({"title": "Annual Report for CY 2025 (Amendment 1)"}) == 2025
        assert year({"title": "New Filer Report for 03/24/2026"}) == 2026
        # Paper filings state no year and none is inferred.
        assert year({"title": "Annual Report", "filed_date": "2026-08-13"}) is None
        assert year({"title": "Annual Report (Amendment)", "filed_date": "2026-02-19"}) is None


def _year(as_of):
    return int(as_of[:4]) if as_of else None


def _store(db_session, holdings, parsed=True, **owner):
    d = FinancialDisclosure(filing_id="F1", as_of_date="2025-12-31", filed_date="2026-05-15",
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
        # Largest slice first. OTHER has no stated value, so it draws no
        # slice (weight 0) but is still listed — its holding must stay
        # reachable through the legend filter.
        assert [c.category for c in result.categories] == ["REAL_ESTATE", "FUNDS", "STOCKS", "OTHER"]
        other = result.categories[3]
        assert (other.weight, other.share, other.count, other.unvalued_count) == (0.0, 0.0, 1, 1)
        assert get_senator_holdings(db_session, "S1", category="OTHER").holdings[0].asset_name == "Art"
        total = 1000000.0 + 32500.5 + 8000.5
        assert result.categories[0].share == pytest.approx(1000000.0 / total)
        assert sum(c.share for c in result.categories) == pytest.approx(1.0)
        stocks = result.categories[2]
        assert (stocks.count, stocks.unvalued_count, stocks.label, stocks.open_ended) == (2, 0, "Stocks", False)
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
        _store(db_session, [], parsed=False, senator_id="S1", unreadable_reason="scanned")
        result = get_senator_holdings(db_session, "S1")
        assert result.available is True and result.parsed is False
        assert result.unreadable_reason == "scanned"
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


class TestTransientParseFailures:
    async def test_a_crash_stores_nothing_and_is_retried_next_run(self, db_session, rep):
        """A read that may be transient is no claim about the report: nothing
        is stored (the section stays hidden rather than saying "can't be
        read"), and the next run fetches it again."""
        crashed = AnnualReport(None, None, "unrecognized", final=False)
        await _ingest_house(db_session, {2025: [_house_filing("NEW")]}, {"NEW": crashed})
        assert db_session.query(FinancialDisclosure).count() == 0

        count, mock_fetch = await _ingest_house(
            db_session, {2025: [_house_filing("NEW")]}, {"NEW": AnnualReport("Member", [_row()])},
        )
        assert count == 1
        mock_fetch.assert_called_once()
        assert db_session.query(FinancialDisclosure).one().parsed is True

    async def test_a_crash_never_replaces_a_readable_stored_report(self, db_session, rep):
        db_session.add(FinancialDisclosure(representative_id="R1", filing_id="OLD", as_of_date="2024-12-31", source_url="x"))
        db_session.commit()
        crashed = AnnualReport(None, None, "unrecognized", final=False)
        await _ingest_house(db_session, {2025: [_house_filing("NEW")]}, {"NEW": crashed})
        assert db_session.query(FinancialDisclosure).one().filing_id == "OLD"


class TestNeverRollBack:
    async def test_house_partial_index_keeps_the_newer_stored_report(self, db_session, rep):
        """Last year's index failed to load, so only the year-before report
        turned up: the stored newer report must survive."""
        db_session.add(FinancialDisclosure(
            representative_id="R1", filing_id="CY2025", as_of_date="2025-12-31", filed_date="2026-05-01",
            source_url="x", parser_version=holdings_pipeline.HOUSE_PARSER_VERSION,
        ))
        db_session.commit()
        count, mock_fetch = await _ingest_house(
            db_session, {2024: [_house_filing("CY2024", year=2024, filing_date="2025-05-01")]},
            {"CY2024": AnnualReport("Member", [_row()])},
        )
        assert count == 0
        mock_fetch.assert_not_called()
        assert db_session.query(FinancialDisclosure).one().filing_id == "CY2025"

    async def test_senate_partial_search_keeps_the_newer_stored_report(self, db_session, senator):
        db_session.add(FinancialDisclosure(
            senator_id="S1", filing_id="cy2025", as_of_date="2025-12-31", filed_date="2026-05-11",
            source_url="x", parser_version=holdings_pipeline.SENATE_PARSER_VERSION,
        ))
        db_session.commit()
        await _ingest_senate(
            db_session, [_senate_filing("cy2024", title="Annual Report for CY 2024", filed="2025-05-11")],
            {"cy2024": [_row()]},
        )
        assert db_session.query(FinancialDisclosure).one().filing_id == "cy2025"


class TestTimeBudget:
    async def test_members_with_nothing_stored_go_first_and_the_rest_wait(self, db_session):
        for rid, name, district in (("R1", "John Doe", 1), ("R2", "Mary Roe", 2)):
            db_session.add(Representative(id=rid, name=name, state="TX", district=district, party="R", is_current=True))
        db_session.add(FinancialDisclosure(
            representative_id="R1", filing_id="OLD", as_of_date="2024-12-31", filed_date="2025-05-01", source_url="x",
            parser_version=holdings_pipeline.HOUSE_PARSER_VERSION,
        ))
        db_session.commit()
        index = {2025: [
            _house_filing("R1NEW"),
            _house_filing("R2NEW", last="Roe", first="Mary", district="TX02"),
        ]}
        reports = {"R1NEW": AnnualReport("Member", [_row()]), "R2NEW": AnnualReport("Member", [_row()])}

        clock = _Clock()

        def past_the_deadline(_filing):  # R2's fetch uses up the budget; R1 waits
            clock.now = 10_000.0

        with patch.object(holdings_pipeline.time, "monotonic", clock):
            await _ingest_house(db_session, index, reports, on_fetch=past_the_deadline)

        by_rep = {d.representative_id: d.filing_id for d in db_session.query(FinancialDisclosure).all()}
        assert by_rep == {"R2": "R2NEW", "R1": "OLD"}


class TestReportLabels:
    def test_house(self):
        label = holdings_pipeline._house_report_label
        assert label({"year": 2025, "filing_type": "O"}) == "2025 annual report"
        assert label({"year": 2025, "filing_type": "A"}) == "2025 annual report (amended)"

    def test_senate(self):
        def label(f):
            return holdings_pipeline._senate_fields({"report_url": "u", **f})["report_label"]

        assert label({"title": "Annual Report for CY 2025"}) == "2025 annual report"
        assert label({"title": "Annual Report for CY 2025 (Amendment 1)"}) == "2025 annual report (amended)"
        # A snapshot at its date, not a year-end report.
        assert label({"title": "New Filer Report for 03/24/2026"}) == "new-filer report as of 2026-03-24"
        assert label({"title": "Annual Report", "filed_date": "2026-08-13"}) == "annual report filed 2026-08-13"
        assert label({"title": "Annual Report (Amendment)", "filed_date": "2026-02-19"}) == (
            "annual report amendment filed 2026-02-19"
        )
        assert label({"title": "New Filer Report", "filed_date": "2026-06-01"}) == "new-filer report filed 2026-06-01"

    async def test_label_is_stored_and_served(self, db_session, senator):
        await _ingest_senate(
            db_session, [_senate_filing("nf", title="New Filer Report for 03/24/2026", filed="2026-07-21")],
            {"nf": [_row()]},
        )
        assert get_senator_holdings(db_session, "S1").report_label == "new-filer report as of 2026-03-24"


class TestSourceFailures:
    async def test_a_source_that_serves_nothing_fails_the_phase(self, db_session):
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        for i in range(n + 1):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
        db_session.commit()
        with pytest.raises(RuntimeError):
            await _ingest_house(db_session, index, {})

    async def test_some_bad_filings_do_not_fail_the_phase_and_everyone_is_processed(self, db_session):
        """No early abort: bad filings bunched at the front (members with
        nothing stored go first) must not starve the members after them."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE + 2
        index = {2025: []}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
        db_session.commit()
        # Only the last member's report loads.
        await _ingest_house(db_session, index, {f"D{n - 1}": AnnualReport("Member", [_row()])})
        assert [d.filing_id for d in db_session.query(FinancialDisclosure).all()] == [f"D{n - 1}"]

    async def test_senate_lapsed_session_is_re_accepted_and_the_report_retried(self, db_session, senator):
        from app.pipeline.fetch.senate_fd import SessionLapsed

        attempts = []

        async def fetch(_client, _db, filing):
            attempts.append(filing["report_url"])
            if len(attempts) == 1:
                raise SessionLapsed("terms page")
            return AnnualReport(None, [_row()])

        accept = AsyncMock(return_value="tok")
        with patch.object(holdings_pipeline, "senate_accept_terms", accept), \
             patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock,
                          return_value=[_senate_filing("cy2025")]), \
             patch.object(holdings_pipeline, "fetch_senate_annual", side_effect=fetch):
            assert await holdings_pipeline.ingest_senate_holdings(db_session, None) == 1
        assert len(attempts) == 2
        assert accept.await_count == 2  # once at the start, once after the failure


class TestSenateRankingByStatedYear:
    def test_a_dated_report_outranks_a_later_paper_filing(self):
        electronic = _senate_filing("e", title="Annual Report for CY 2024", filed="2025-05-11")
        paper = _senate_filing("p", title="Annual Report", filed="2025-12-01", paper=True)
        rank = lambda f: holdings_pipeline._rank(  # noqa: E731
            holdings_pipeline._senate_as_of(f), False, f["filed_date"], 0, f["report_url"], False,
        )
        assert rank(electronic) > rank(paper)

    async def test_an_annual_report_outranks_an_amended_new_filer_report_of_its_year(self, db_session, senator):
        new_filer = _senate_filing("nf", title="New Filer Report for 01/20/2025", filed="2025-02-01")
        amended = _senate_filing("nfa", title="New Filer Report for 01/20/2025 (Amendment 1)", filed="2025-06-10")
        annual = _senate_filing("cy2025", title="Annual Report for CY 2025", filed="2026-05-15")
        parsed = {"nf": [_row()], "nfa": [_row()], "cy2025": [_row()]}
        await _ingest_senate(db_session, [new_filer, amended, annual], parsed)
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, stored.as_of_date) == ("cy2025", "2025-12-31")

    async def test_a_later_paper_filing_is_named_beside_the_dated_report(self, db_session, senator):
        """A paper filing states no year, so it doesn't displace the dated
        report — it is named beside it as filed later."""
        e2024 = _senate_filing("e2024", title="Annual Report for CY 2024", filed="2025-05-11")
        paper = _senate_filing("p", title="Annual Report", filed="2026-05-14", office="Senator", paper=True)
        await _ingest_senate(db_session, [e2024, paper], {"e2024": [_row()]})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, stored.later_filing_label, stored.later_filing_url) == (
            "e2024", "annual report filed 2026-05-14", paper["report_url"],
        )
        # A newer dated report, filed after the paper one, clears the note.
        e2025 = _senate_filing("e2025", title="Annual Report for CY 2025", filed="2026-08-01")
        await _ingest_senate(db_session, [e2024, paper, e2025], {"e2024": [_row()], "e2025": [_row()]})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, stored.later_filing_url) == ("e2025", None)

    async def test_a_search_that_misses_a_senator_keeps_their_note(self, db_session, senator):
        e2024 = _senate_filing("e2024", title="Annual Report for CY 2024", filed="2025-05-11")
        paper = _senate_filing("p", title="Annual Report", filed="2026-05-14", office="Senator", paper=True)
        await _ingest_senate(db_session, [e2024, paper], {"e2024": [_row()]})
        other = {**_senate_filing("o"), "last": "Other", "first": "Sam", "office": "Other, Sam (Senator)"}
        await _ingest_senate(db_session, [other], {})
        assert db_session.query(FinancialDisclosure).one().later_filing_url == paper["report_url"]


class TestUnreadableStatus:
    async def test_scanned_amendment_without_member_prefix_is_not_the_members(self, db_session, rep):
        cand = {**_house_filing("CANDAMEND", filing_date="2026-08-01"), "filing_type": "A", "prefix": ""}
        member = {**_house_filing("MEMBER", filing_date="2026-05-01"), "prefix": "Hon."}
        reports = {
            "CANDAMEND": AnnualReport(None, None, "scanned"),  # no text: no Status line
            "MEMBER": AnnualReport("Member", [_row()]),
        }
        await _ingest_house(db_session, {2025: [cand, member]}, reports)
        assert db_session.query(FinancialDisclosure).one().filing_id == "MEMBER"

    async def test_scanned_original_annual_report_is_the_members(self, db_session, rep):
        await _ingest_house(
            db_session, {2025: [{**_house_filing("SCAN"), "prefix": ""}]}, {"SCAN": AnnualReport(None, None, "scanned")},
        )
        assert db_session.query(FinancialDisclosure).one().filing_id == "SCAN"


async def test_trade_and_holdings_ingests_share_one_index_download(db_session):
    import io
    import zipfile

    from app.pipeline.fetch import house_ptr
    from app.pipeline.fetch.house_fd import fetch_annual_filing_index

    xml = b"""<Members>
      <Member><Prefix>Hon.</Prefix><Last>Doe</Last><First>J</First><FilingType>P</FilingType>
        <StateDst>TX01</StateDst><FilingDate>2/1/2025</FilingDate><DocID>1</DocID></Member>
      <Member><Prefix>Hon.</Prefix><Last>Doe</Last><First>J</First><FilingType>O</FilingType>
        <StateDst>TX01</StateDst><FilingDate>5/1/2026</FilingDate><DocID>2</DocID></Member>
    </Members>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("2025FD.xml", xml)

    with patch.object(house_ptr, "fetch_bytes_with_retry", new_callable=AsyncMock, return_value=buf.getvalue()) as get:
        ptrs = await house_ptr.fetch_ptr_filing_index(None, db_session, 2025)
        annuals = await fetch_annual_filing_index(None, db_session, 2025)

    assert get.await_count == 1
    assert [f["pdf_url"].rsplit("/", 2)[-2:] for f in ptrs] == [["2025", "1.pdf"]]
    assert "/ptr-pdfs/" in ptrs[0]["pdf_url"]
    assert [(f["doc_id"], f["prefix"]) for f in annuals] == [("2", "Hon.")]
    assert "/financial-pdfs/2025/2.pdf" in annuals[0]["pdf_url"]


class TestPaperAmendments:
    def test_a_paper_amendment_has_no_year_and_claims_none(self):
        amendment = _senate_filing("a", title="Annual Report (Amendment)", filed="2026-02-19", paper=True)
        assert _year(holdings_pipeline._senate_as_of(amendment)) is None
        assert holdings_pipeline._senate_fields(amendment)["report_label"] == "annual report amendment filed 2026-02-19"

    async def test_a_later_paper_amendment_never_replaces_a_dated_report(self, db_session, senator):
        """Ricketts-shaped (live, 2026-09): an electronic CY2024 report, then
        a paper amendment in February 2026 — before any CY2025 report — that
        a filed-year-minus-one guess would have dated 2025 and put first."""
        db_session.add(FinancialDisclosure(
            senator_id="S1", filing_id="e2024", as_of_date="2024-12-31", filed_date="2025-05-11",
            source_url="x", parser_version=holdings_pipeline.SENATE_PARSER_VERSION,
        ))
        db_session.commit()
        filings = [
            _senate_filing("e2024", title="Annual Report for CY 2024", filed="2025-05-11"),
            _senate_filing("amend", title="Annual Report (Amendment)", filed="2026-02-19", office="Senator", paper=True),
        ]
        await _ingest_senate(db_session, filings, {"e2024": [_row()]})
        assert db_session.query(FinancialDisclosure).one().filing_id == "e2024"

    async def test_a_paper_amendment_is_used_when_it_is_all_there_is(self, db_session, senator):
        filings = [_senate_filing("amend", title="Annual Report (Amendment)", filed="2026-08-10",
                                  office="Senator", paper=True)]
        await _ingest_senate(db_session, filings, {})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, _year(stored.as_of_date), stored.parsed) == ("amend", None, False)


class TestHouseFetchFailureFallsThrough:
    async def test_an_unfetchable_candidate_filing_does_not_block_the_members_own(self, db_session, rep):
        cand = {**_house_filing("CAND", filing_date="2026-08-01"), "filing_type": "A"}
        member = _house_filing("MEMBER", filing_date="2026-05-01")
        await _ingest_house(
            db_session, {2025: [cand, member]}, {"CAND": None, "MEMBER": AnnualReport("Member", [_row()])},
        )
        assert db_session.query(FinancialDisclosure).one().filing_id == "MEMBER"


class TestServiceDetails:
    def test_none_at_year_end_is_counted_apart_from_no_stated_value(self, db_session, senator):
        _store(db_session, [
            _h("Sold", "STOCKS", 0.0, 0.0, value_text="None"),
            _h("Unknown", "STOCKS", None, None, value_text="Undetermined"),
            _h("Kept", "STOCKS", 1001.0, 15000.0),
        ], senator_id="S1")
        stocks = get_senator_holdings(db_session, "S1").categories[0]
        assert (stocks.count, stocks.zero_value_count, stocks.unvalued_count) == (3, 1, 1)

    def test_other_filter_includes_categories_no_longer_in_the_table(self, db_session, senator):
        _store(db_session, [_h("Legacy", "RETIRED_CATEGORY", 1.0, 2.0), _h("Stock", "STOCKS", 1.0, 2.0)],
               senator_id="S1")
        result = get_senator_holdings(db_session, "S1", category="OTHER")
        assert [h.asset_name for h in result.holdings] == ["Legacy"]
        assert result.holdings[0].category == "OTHER"
        assert {c.category for c in result.categories} == {"OTHER", "STOCKS"}

    def test_page_order_is_largest_first_then_name_with_unvalued_last(self, db_session, senator):
        _store(db_session, [
            _h("b small", "STOCKS", 1.0, 1000.0),
            _h("Undetermined thing", "OTHER", None, None),
            _h("A small", "STOCKS", 1.0, 1000.0),
            _h("Big", "FUNDS", 1000001.0, 5000000.0),
        ], senator_id="S1")
        names = [h.asset_name for h in get_senator_holdings(db_session, "S1", per_page=2, page=1).holdings]
        names += [h.asset_name for h in get_senator_holdings(db_session, "S1", per_page=2, page=2).holdings]
        assert names == ["Big", "A small", "b small", "Undetermined thing"]


class TestFallThroughAndMatching:
    async def test_senate_unloadable_top_filing_falls_back_to_the_next(self, db_session, senator):
        filings = [
            _senate_filing("amended", title="Annual Report for CY 2025 (Amendment 1)", filed="2026-09-01"),
            _senate_filing("original", title="Annual Report for CY 2025", filed="2026-05-11"),
        ]
        # "amended" never loads (missing from `parsed`); the original does.
        await _ingest_senate(db_session, filings, {"original": [_row()]})
        assert db_session.query(FinancialDisclosure).one().filing_id == "original"

    async def test_failures_are_counted_per_member_not_per_filing(self, db_session):
        """Members whose first filing fails but whose next one loads are not
        a failing source."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE + 2
        index = {2025: []}
        reports = {}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            bad = {**_house_filing(f"BAD{i}", filing_date="2026-08-01", last=f"Name{i}", first="Person",
                                   district=f"TX{i + 1:02d}"), "filing_type": "A"}
            good = _house_filing(f"GOOD{i}", filing_date="2026-05-01", last=f"Name{i}", first="Person",
                                 district=f"TX{i + 1:02d}")
            index[2025] += [bad, good]
            reports[f"GOOD{i}"] = AnnualReport("Member", [_row()])
        db_session.commit()
        await _ingest_house(db_session, index, reports)  # must not raise
        assert db_session.query(FinancialDisclosure).count() == n

    async def test_budget_is_checked_before_every_fetch_not_only_each_member(self, db_session, rep):
        index = {2025: [
            {**_house_filing("CAND", filing_date="2026-08-01"), "filing_type": "A"},
            _house_filing("MEMBER", filing_date="2026-05-01"),
        ]}
        # The first fetch is in time, and uses up the budget; the second isn't made.
        clock = _Clock()

        def past_the_deadline(_filing):
            clock.now = 10_000.0

        candidates = {"CAND": AnnualReport("Congressional Candidate", [_row()])}
        with patch.object(holdings_pipeline.time, "monotonic", clock):
            count, mock_fetch = await _ingest_house(db_session, index, candidates, on_fetch=past_the_deadline)
        assert (mock_fetch.call_count, count) == (1, 0)

    def test_member_prefix_tolerates_punctuation_and_case(self):
        assert holdings_pipeline._is_member_prefix("Hon.")
        assert holdings_pipeline._is_member_prefix("hon")
        assert not holdings_pipeline._is_member_prefix("Mr.")
        assert not holdings_pipeline._is_member_prefix(None)

    async def test_each_filer_is_matched_once(self, db_session, rep):
        index = {2025: [_house_filing("A1"), _house_filing("A2", filing_date="2026-06-01")],
                 2024: [_house_filing("A0", year=2024)]}
        with patch.object(holdings_pipeline, "match_representative",
                          wraps=holdings_pipeline.match_representative) as spy:
            await _ingest_house(db_session, index, {"A2": AnnualReport("Member", [_row()])})
        assert spy.call_count == 1


class TestBreakdownCache:
    def test_breakdown_is_computed_once_per_report_and_a_new_report_gets_its_own(self, db_session, senator):
        from app.services import holdings_service

        first = _store(db_session, [_h("Apple", "STOCKS", 1001.0, 15000.0)], senator_id="S1")
        with patch.object(holdings_service, "_categories", wraps=holdings_service._categories) as spy:
            get_senator_holdings(db_session, "S1", page=1)
            get_senator_holdings(db_session, "S1", page=2, category="STOCKS")
            assert spy.call_count == 1

            # Replace the report — even if SQLite hands the new row the same
            # id, its ingest time differs, so the old breakdown can't answer.
            db_session.delete(first)
            db_session.commit()
            _store(db_session, [_h("Bank", "CASH", 1.0, 1000.0)], senator_id="S1")
            result = get_senator_holdings(db_session, "S1")
            assert spy.call_count == 2
        assert [c.category for c in result.categories] == ["CASH"]


class TestLapsesAndOutages:
    async def test_a_plain_failed_fetch_does_not_re_accept_terms(self, db_session, senator):
        accept = AsyncMock(return_value="tok")
        with patch.object(holdings_pipeline, "senate_accept_terms", accept), \
             patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock,
                          return_value=[_senate_filing("gone")]), \
             patch.object(holdings_pipeline, "fetch_senate_annual", new_callable=AsyncMock, return_value=None):
            await holdings_pipeline.ingest_senate_holdings(db_session, None)
        assert accept.await_count == 1  # only the phase's own session setup

    async def test_newer_reports_that_wont_load_fail_the_phase_even_with_stored_ones(self, db_session):
        """An outage on a night when only new reports need fetching: every
        newer report fails, each member keeps their stored one. That run of
        members must still fail the phase (and alert), not pass quietly —
        and what was stored stays."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE + 2
        index = {2025: []}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            db_session.add(FinancialDisclosure(
                representative_id=f"R{i}", filing_id=f"CUR{i}", as_of_date="2025-12-31", filed_date="2026-05-01",
                source_url="x", parser_version=holdings_pipeline.HOUSE_PARSER_VERSION,
            ))
            common = {"last": f"Name{i}", "first": "Person", "district": f"TX{i + 1:02d}"}
            index[2025] += [
                {**_house_filing(f"CAND{i}", filing_date="2026-08-01", **common), "filing_type": "A"},
                _house_filing(f"CUR{i}", filing_date="2026-05-01", **common),
            ]
        db_session.commit()
        with pytest.raises(RuntimeError):
            await _ingest_house(db_session, index, {})  # every newer fetch fails
        assert db_session.query(FinancialDisclosure).count() == n


class TestReadabilityRules:
    async def test_a_readable_report_for_the_same_year_replaces_a_stored_scan(self, db_session, senator):
        """The paper copy was stored because the electronic one failed to
        load that run; filed earlier or not, the electronic report wins."""
        db_session.add(FinancialDisclosure(
            senator_id="S1", filing_id="paper", filed_date="2026-08-12", source_url="x",
            parsed=False, unreadable_reason="scanned", parser_version=holdings_pipeline.SENATE_PARSER_VERSION,
        ))
        db_session.commit()
        filings = [
            _senate_filing("paper", title="Annual Report", filed="2026-08-12", office="Senator", paper=True),
            _senate_filing("e2025", title="Annual Report for CY 2025", filed="2026-05-15"),
        ]
        await _ingest_senate(db_session, filings, {"e2025": [_row()]})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, stored.parsed) == ("e2025", True)

    async def test_an_unrecognizable_page_stores_nothing_for_a_new_member(self, db_session, senator):
        """A block or error page looks just like an unknown layout; neither
        may put "can't be read automatically" on a scorecard."""
        async def fetch(_client, _db, filing):
            return AnnualReport(None, None, "unrecognized", final=False)

        with patch.object(holdings_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value="tok"), \
             patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock,
                          return_value=[_senate_filing("x")]), \
             patch.object(holdings_pipeline, "fetch_senate_annual", side_effect=fetch):
            await holdings_pipeline.ingest_senate_holdings(db_session, None)
        assert db_session.query(FinancialDisclosure).count() == 0


class TestOutagesStayVisible:
    async def test_an_outage_fails_the_phase_every_night_it_lasts(self, db_session):
        """Nothing may learn to stop counting failures while the source is
        still down — every night of the outage fails the phase (and alerts)."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
        db_session.commit()
        for _night in range(5):
            with pytest.raises(RuntimeError):
                await _ingest_house(db_session, index, {})

    def _dead_links_beside_a_stored_report(self, db_session):
        """MIN_ATTEMPTS_FOR_OUTAGE members whose only filing always fails,
        plus one member whose report is already stored — a quiet night on
        which only the dead links are left to fetch."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        for i in range(n + 1):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
        db_session.commit()
        return index, {f"D{n}": AnnualReport("Member", [_row()])}

    async def test_dead_links_on_a_quiet_night_are_not_an_outage(self, db_session):
        index, reports = self._dead_links_beside_a_stored_report(db_session)
        await _ingest_house(db_session, index, reports)  # stores the good one
        probe = AsyncMock(return_value=True)
        with patch.object(holdings_pipeline, "house_report_still_loads", probe):
            for _night in range(5):
                await _ingest_house(db_session, index, reports)  # must not raise
        stored_url = db_session.query(FinancialDisclosure).one().source_url
        probe.assert_awaited_with(None, stored_url)

    async def test_an_outage_with_reports_stored_fails_every_night(self, db_session):
        index, reports = self._dead_links_beside_a_stored_report(db_session)
        await _ingest_house(db_session, index, reports)
        with patch.object(holdings_pipeline, "house_report_still_loads", AsyncMock(return_value=False)):
            for _night in range(5):
                with pytest.raises(RuntimeError):
                    await _ingest_house(db_session, index, reports)

    async def test_the_senate_asks_efd_about_a_stored_report_too(self, db_session):
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        filings = []
        for i in range(n + 1):
            db_session.add(Senator(id=f"S{i}", name=f"Pat Name{i}", state="XX", party="D", is_current=True))
            filings.append({**_senate_filing(f"e{i}"), "last": f"Name{i}", "first": "Pat",
                            "office": f"Name{i}, Pat (Senator)"})
        db_session.commit()
        await _ingest_senate(db_session, filings, {f"e{n}": [_row()]})
        for answer, raises in ((True, False), (False, True)):
            with patch.object(holdings_pipeline, "senate_report_still_loads", AsyncMock(return_value=answer)):
                if raises:
                    with pytest.raises(RuntimeError):
                        await _ingest_senate(db_session, filings, {f"e{n}": [_row()]})
                else:
                    await _ingest_senate(db_session, filings, {f"e{n}": [_row()]})

    async def test_cache_hits_are_not_attempts_or_successes(self, db_session):
        """A report answered from the parse cache made no request: it
        proves nothing about the source either way."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        reports = {}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            # Each member: a newer filing that fails live, an older one served from cache.
            index[2025] += [
                _house_filing(f"NEW{i}", filing_date="2026-08-01", last=f"Name{i}", first="Person",
                              district=f"TX{i + 1:02d}"),
                _house_filing(f"OLD{i}", filing_date="2026-05-01", last=f"Name{i}", first="Person",
                              district=f"TX{i + 1:02d}"),
            ]
            reports[f"OLD{i}"] = AnnualReport("Member", [_row()], live=False)
        db_session.commit()
        with pytest.raises(RuntimeError):
            await _ingest_house(db_session, index, reports)

    async def test_failing_to_re_accept_the_terms_stops_the_phase(self, db_session, senator):
        from app.pipeline.fetch.senate_fd import SessionLapsed

        accept = AsyncMock(side_effect=["tok", None])
        with patch.object(holdings_pipeline, "senate_accept_terms", accept), \
             patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock,
                          return_value=[_senate_filing("a")]), \
             patch.object(holdings_pipeline, "fetch_senate_annual", side_effect=SessionLapsed("terms page")):
            with pytest.raises(RuntimeError, match="re-accepted"):
                await holdings_pipeline.ingest_senate_holdings(db_session, None)


class TestPaperDoesNotMaskOutages:
    async def test_paper_fallbacks_do_not_count_as_a_working_source(self, db_session):
        """Every electronic report page fails; each senator falls back to a
        paper filing, which involves no request at all. That must still read
        as eFD being down."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        filings = []
        for i in range(n):
            db_session.add(Senator(id=f"S{i}", name=f"Pat Name{i}", state="XX", party="D", is_current=True))
            common = {"last": f"Name{i}", "first": "Pat", "office": f"Name{i}, Pat (Senator)"}
            filings += [
                {**_senate_filing(f"e{i}"), **common},
                {**_senate_filing(f"p{i}", title="Annual Report", filed="2026-08-01", paper=True), **common},
            ]
        db_session.commit()
        with pytest.raises(RuntimeError):
            await _ingest_senate(db_session, filings, {})  # every electronic fetch fails


class TestParserFailuresAreNotExcused:
    async def test_reports_that_load_but_never_read_fail_the_phase_without_a_probe(self, db_session):
        """A parser regression: every PDF downloads, every read crashes. The
        source is fine — which is exactly why a probe mustn't excuse it."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        reports = {}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
            reports[f"D{i}"] = AnnualReport(None, None, "unrecognized", final=False)
        db_session.commit()
        probe = AsyncMock(return_value=True)
        with patch.object(holdings_pipeline, "house_report_still_loads", probe):
            with pytest.raises(RuntimeError, match="parser regression"):
                await _ingest_house(db_session, index, reports)
        probe.assert_not_awaited()

    @pytest.mark.parametrize("re_read", [
        AnnualReport(None, None, "unrecognized"),  # can't find the schedule
        AnnualReport(None, None, "scanned"),  # no text — in a report an earlier parser read text from
    ])
    async def test_a_re_read_that_comes_back_empty_keeps_the_earlier_parsers_holdings(self, db_session, rep, re_read):
        index = {2025: [_house_filing("SAME")]}
        await _ingest_house(db_session, index, {"SAME": AnnualReport("Member", [_row(), _row()])})
        with patch.object(holdings_pipeline, "HOUSE_PARSER_VERSION", 99):
            await _ingest_house(db_session, index, {"SAME": re_read})
            disclosure = db_session.query(FinancialDisclosure).one()
            assert (disclosure.parsed, db_session.query(FinancialHolding).count()) == (True, 2)
            # Not stamped as read by v99: it is re-read (from the parse cache)
            # each night, so the regression keeps counting until it's fixed.
            assert disclosure.parser_version == 1
            _, mock_fetch = await _ingest_house(db_session, index, {"SAME": re_read})
        assert mock_fetch.call_count == 1

    async def test_an_empty_re_read_replaces_the_earlier_one(self, db_session, rep):
        """A parser fix that stops reading another schedule's rows as assets
        reads exactly this."""
        index = {2025: [_house_filing("SAME")]}
        await _ingest_house(db_session, index, {"SAME": AnnualReport("Member", [_row(), _row()])})
        with patch.object(holdings_pipeline, "HOUSE_PARSER_VERSION", 99):
            await _ingest_house(db_session, index, {"SAME": AnnualReport("Member", [])})
        disclosure = db_session.query(FinancialDisclosure).one()
        assert (disclosure.parsed, disclosure.parser_version, db_session.query(FinancialHolding).count()) == (True, 99, 0)

    async def test_a_re_read_with_rows_replaces_the_earlier_one(self, db_session, rep):
        index = {2025: [_house_filing("SAME")]}
        await _ingest_house(db_session, index, {"SAME": AnnualReport("Member", [_row(), _row()])})
        with patch.object(holdings_pipeline, "HOUSE_PARSER_VERSION", 99):
            await _ingest_house(db_session, index, {"SAME": AnnualReport("Member", [_row()])})
        assert db_session.query(FinancialHolding).count() == 1

    async def test_unrecognized_reads_are_not_reads(self, db_session):
        """A parser whose locator stopped matching returns a final
        "unrecognized" report for everything — that is not the source and
        parser working."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        reports = {}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
            reports[f"D{i}"] = AnnualReport("Member", None, "unrecognized")
        db_session.commit()
        with pytest.raises(RuntimeError, match="parser regression"):
            await _ingest_house(db_session, index, reports)

    async def test_one_unreadable_answer_does_not_hide_an_outage(self, db_session):
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE + 3
        index = {2025: []}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
        db_session.commit()
        # Every fetch fails but one, which answers with something unreadable.
        with pytest.raises(RuntimeError, match="no report fetched"):
            await _ingest_house(db_session, index, {"D0": AnnualReport(None, None, "unrecognized", final=False)})


class TestSenateProbe:
    async def test_a_lapsed_session_is_re_accepted_not_read_as_an_outage(self):
        from app.pipeline.fetch.senate_fd import SessionLapsed

        probe = AsyncMock(side_effect=[SessionLapsed("terms"), True])
        accept = AsyncMock(return_value="tok")
        with patch.object(holdings_pipeline, "senate_report_still_loads", probe), \
             patch.object(holdings_pipeline, "senate_accept_terms", accept):
            assert await holdings_pipeline._senate_probe(None, "https://efd.example/r/") is True
        accept.assert_awaited_once()

    async def test_a_second_lapse_reads_as_down_after_one_re_accept(self):
        from app.pipeline.fetch.senate_fd import SessionLapsed

        accept = AsyncMock(return_value="tok")
        with patch.object(holdings_pipeline, "senate_report_still_loads",
                          AsyncMock(side_effect=SessionLapsed("terms"))), \
             patch.object(holdings_pipeline, "senate_accept_terms", accept):
            assert await holdings_pipeline._senate_probe(None, "https://efd.example/r/") is False
        accept.assert_awaited_once()

    async def test_terms_that_cannot_be_re_accepted_read_as_down(self):
        from app.pipeline.fetch.senate_fd import SessionLapsed

        with patch.object(holdings_pipeline, "senate_report_still_loads",
                          AsyncMock(side_effect=SessionLapsed("terms"))), \
             patch.object(holdings_pipeline, "senate_accept_terms", AsyncMock(return_value=None)):
            assert await holdings_pipeline._senate_probe(None, "https://efd.example/r/") is False


class TestParserRegressionSignal:
    def _members(self, db_session, n):
        index = {2025: []}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
        db_session.commit()
        return index

    async def test_a_scanned_report_does_not_hide_a_regression(self, db_session):
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE + 1
        index = self._members(db_session, n)
        reports = {f"D{i}": AnnualReport("Member", None, "unrecognized") for i in range(n - 1)}
        reports[f"D{n - 1}"] = AnnualReport(None, None, "scanned")
        with pytest.raises(RuntimeError, match="parser regression"):
            await _ingest_house(db_session, index, reports)

    async def test_empty_reads_are_not_counted_either_way(self, db_session):
        """A genuine report can list nothing; zero-row regressions are the
        parser tests' to catch."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = self._members(db_session, n)
        await _ingest_house(db_session, index, {f"D{i}": AnnualReport("Member", []) for i in range(n)})

    async def test_a_re_read_with_fewer_rows_is_not_an_alarm(self, db_session):
        """A parser fix that drops spurious rows looks exactly like this."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = self._members(db_session, n)
        await _ingest_house(db_session, index, {f"D{i}": AnnualReport("Member", [_row(), _row()]) for i in range(n)})
        with patch.object(holdings_pipeline, "HOUSE_PARSER_VERSION", 99):
            await _ingest_house(db_session, index, {f"D{i}": AnnualReport("Member", [_row()]) for i in range(n)})
        assert db_session.query(FinancialHolding).count() == n

    async def test_scanned_where_an_earlier_parser_read_text_is_a_miss(self, db_session):
        """"Scanned" is the parser's own verdict (no words on page one): a
        text-extraction regression calls every report scanned."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = self._members(db_session, n)
        await _ingest_house(db_session, index, {f"D{i}": AnnualReport("Member", [_row()]) for i in range(n)})
        with patch.object(holdings_pipeline, "HOUSE_PARSER_VERSION", 99):
            with pytest.raises(RuntimeError, match="parser regression"):
                await _ingest_house(db_session, index, {f"D{i}": AnnualReport(None, None, "scanned") for i in range(n)})
        assert db_session.query(FinancialHolding).count() == n

    async def test_a_candidates_report_does_not_count_for_the_member(self, db_session):
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        reports = {}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            common = {"last": f"Name{i}", "first": "Person", "district": f"TX{i + 1:02d}"}
            index[2025] += [{**_house_filing(f"C{i}", filing_date="2026-08-01", **common), "filing_type": "A"},
                            _house_filing(f"M{i}", filing_date="2026-05-01", **common)]
            reports[f"C{i}"] = AnnualReport("Congressional Candidate", [])
            reports[f"M{i}"] = AnnualReport("Member", [_row()])
        db_session.commit()
        await _ingest_house(db_session, index, reports)  # must not raise

    async def test_a_kept_regression_keeps_failing_the_phase(self, db_session):
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = self._members(db_session, n)
        await _ingest_house(db_session, index, {f"D{i}": AnnualReport("Member", [_row()]) for i in range(n)})
        # Cached reads (no request) of the broken parser's output, night after night.
        broken = {f"D{i}": AnnualReport("Member", None, "unrecognized", live=False) for i in range(n)}
        with patch.object(holdings_pipeline, "HOUSE_PARSER_VERSION", 99):
            for _night in range(3):
                with pytest.raises(RuntimeError, match="parser regression"):
                    await _ingest_house(db_session, index, broken)
        assert db_session.query(FinancialHolding).count() == n  # the earlier reads stay


class TestProbesSeveralStoredReports:
    async def test_one_withdrawn_stored_report_is_not_an_outage(self, db_session):
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        good = {}
        for i in range(n + 3):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
            if i >= n:
                good[f"D{i}"] = AnnualReport("Member", [_row()])
        db_session.commit()
        await _ingest_house(db_session, index, good)
        probe = AsyncMock(side_effect=[False, True])  # the newest stored one is gone; the next loads
        with patch.object(holdings_pipeline, "house_report_still_loads", probe):
            await _ingest_house(db_session, index, good)  # must not raise
        assert probe.await_count == 2

    async def test_stored_urls_are_only_looked_up_when_needed(self, db_session, rep):
        with patch.object(holdings_pipeline, "_stored_urls") as lookup:
            await _ingest_house(db_session, {2025: [_house_filing("A")]}, {"A": AnnualReport("Member", [_row()])})
        lookup.assert_not_called()


class TestHangingSource:
    def _members(self, db_session, n):
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
        db_session.commit()
        return {2025: [_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}")
                       for i in range(n)]}

    async def test_a_budget_spent_on_failing_requests_asks_the_source(self, db_session):
        """A host that hangs costs minutes per request, so the budget runs
        out after a couple of members — far short of the attempt threshold."""
        index = self._members(db_session, 3)
        clock = _Clock()

        def hangs(_filing):
            clock.now += holdings_pipeline.FETCH_BUDGET.total_seconds() / 2 + 1

        with patch.object(holdings_pipeline.time, "monotonic", clock):
            with pytest.raises(RuntimeError, match="no report fetched"):
                await _ingest_house(db_session, index, {}, on_fetch=hangs)

    async def test_a_budget_spent_elsewhere_is_not_an_outage(self, db_session):
        """A long index download or search, then one quick failure."""
        index = self._members(db_session, 3)
        clock = _Clock()

        budget = holdings_pipeline.FETCH_BUDGET.total_seconds()

        def slow_index(_year):
            clock.now = budget - 1

        def quick_failure(_filing):
            clock.now += 2

        with patch.object(holdings_pipeline.time, "monotonic", clock):
            count, mock_fetch = await _ingest_house(db_session, index, {}, on_fetch=quick_failure,
                                                    on_index=slow_index)  # must not raise
        # The fetch budget starts when fetching does: a slow index leaves it whole.
        assert mock_fetch.call_count == 3

    async def test_a_download_in_flight_is_cut_off_at_the_deadline(self, db_session, rep):
        import asyncio

        from app.pipeline.fetch import house_fd

        async def hangs(_client, _url, headers=None):
            await asyncio.sleep(3600)

        with patch.object(holdings_pipeline, "FETCH_BUDGET", holdings_pipeline.timedelta(milliseconds=50)), \
             patch.object(holdings_pipeline, "fetch_annual_filing_index", AsyncMock(return_value=[_house_filing("A")])), \
             patch.object(house_fd, "download_pdf", side_effect=hangs):
            with pytest.raises(RuntimeError, match="no report fetched"):  # nothing stored to ask about
                await holdings_pipeline.ingest_house_holdings(db_session, None)

    async def test_a_slow_preparation_step_fails_the_phase(self, db_session, rep):
        import asyncio

        async def slow_index(*_args):
            await asyncio.sleep(3600)

        with patch.object(holdings_pipeline, "PREP_BUDGET", holdings_pipeline.timedelta(milliseconds=50)), \
             patch.object(holdings_pipeline, "fetch_annual_filing_index", side_effect=slow_index):
            with pytest.raises(RuntimeError, match="took longer than"):
                await holdings_pipeline.ingest_house_holdings(db_session, None)

    async def test_probes_stop_at_their_budget(self):
        import asyncio

        health = holdings_pipeline._SourceHealth("X")
        health.attempted = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE

        async def hangs(_url):
            await asyncio.sleep(3600)

        with patch.object(holdings_pipeline, "PROBE_BUDGET", holdings_pipeline.timedelta(milliseconds=50)):
            with pytest.raises(RuntimeError, match="no report fetched"):
                await health.check(lambda: ["u1", "u2", "u3"], hangs)


class TestFilerAttribution:
    async def test_a_candidate_status_is_skipped_but_not_a_parser_miss(self, db_session):
        """A former member running again reads as a candidate on a filing
        with a member's honorific; that is not the parser failing."""
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        index = {2025: []}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append({**_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"),
                                "prefix": "Hon."})
        db_session.commit()
        reports = {f"D{i}": AnnualReport("Congressional Candidate", [_row()]) for i in range(n)}
        await _ingest_house(db_session, index, reports)  # must not raise
        assert db_session.query(FinancialDisclosure).count() == 0

    def test_a_candidates_own_filing_is_not_the_members(self):
        cand = {**_house_filing("C"), "filing_type": "A", "prefix": "Mr."}
        assert holdings_pipeline._house_owner(cand, AnnualReport("Congressional Candidate", [])) == "not member"

    async def test_an_amendment_outranks_an_original_filed_the_same_day(self, db_session, rep):
        index = {2025: [_house_filing("ORIG", filing_date="2026-05-01"),
                        {**_house_filing("AMEND", filing_date="2026-05-01"), "filing_type": "A", "prefix": "Hon."}]}
        reports = {"ORIG": AnnualReport("Member", [_row()]), "AMEND": AnnualReport("Member", [_row(), _row()])}
        await _ingest_house(db_session, index, reports)
        assert db_session.query(FinancialDisclosure).one().filing_id == "AMEND"
        await _ingest_house(db_session, index, reports)
        assert db_session.query(FinancialDisclosure).one().filing_id == "AMEND"


def test_the_stock_runs_overrun_budget_allows_for_the_holdings_phases():
    from app.holdings_schedule import HOLDINGS_STEPS, PHASE_CEILING
    from app.ops_alerts import stock_trades_overrun_budget

    assert stock_trades_overrun_budget() == (
        holdings_pipeline.timedelta(hours=2) + len(HOLDINGS_STEPS) * PHASE_CEILING
    )


def test_the_watchdog_reads_the_budget_without_the_fetch_chain():
    """The overrun watchdog must not depend on Playwright or pdfplumber
    importing cleanly."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import sys\n"
        "from app.ops_alerts import stock_trades_overrun_budget\n"
        "stock_trades_overrun_budget()\n"
        "assert 'app.pipeline.holdings_pipeline' not in sys.modules\n"
        "assert 'playwright' not in sys.modules\n"
    )
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(backend)}
    subprocess.run([sys.executable, "-c", code], check=True, cwd=backend, env=env)


class TestLapseAndDeadlineEdges:
    async def test_a_second_lapse_stops_the_member_rather_than_falling_back(self, db_session, senator):
        """Falling back to CY2024 would show it as the senator's latest."""
        from app.pipeline.fetch.senate_fd import SessionLapsed

        filings = [_senate_filing("cy2025"), _senate_filing("cy2024", title="Annual Report for CY 2024", filed="2025-05-11")]

        async def fetch(_client, _db, filing):
            if "cy2025" in filing["report_url"]:
                raise SessionLapsed("terms")
            return AnnualReport(None, [_row()], None)

        with patch.object(holdings_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value="tok"), \
             patch.object(holdings_pipeline, "search_annual_filings", new_callable=AsyncMock, return_value=filings), \
             patch.object(holdings_pipeline, "fetch_senate_annual", side_effect=fetch):
            await holdings_pipeline.ingest_senate_holdings(db_session, None)
        assert db_session.query(FinancialDisclosure).count() == 0

    async def test_a_cut_off_last_fetch_still_counts_as_the_budget_running_out(self, db_session):
        """Three members with new filings on a hanging host, then members
        whose stored reports are current: the loop never reaches another
        deadline check, but the budget did run out."""
        n = 6
        index = {2025: []}
        for i in range(n):
            db_session.add(Representative(id=f"R{i}", name=f"Person Name{i}", state="TX", district=i + 1,
                                          party="R", is_current=True))
            index[2025].append(_house_filing(f"D{i}", last=f"Name{i}", first="Person", district=f"TX{i + 1:02d}"))
            if i >= 3:
                db_session.add(FinancialDisclosure(
                    representative_id=f"R{i}", filing_id=f"D{i}", as_of_date="2025-12-31",
                    filed_date="2026-05-01", source_url="x",
                    parser_version=holdings_pipeline.HOUSE_PARSER_VERSION,
                ))
        db_session.commit()
        clock = _Clock()

        def hangs(_filing):
            clock.now += holdings_pipeline.FETCH_BUDGET.total_seconds() / 3 + 1

        with patch.object(holdings_pipeline.time, "monotonic", clock):
            with pytest.raises(RuntimeError, match="no report fetched"):
                await _ingest_house(db_session, index, {}, on_fetch=hangs)


class TestLaterPaperNoteEdges:
    async def test_no_note_without_a_stored_filing_date(self, db_session, senator):
        db_session.add(FinancialDisclosure(
            senator_id="S1", filing_id="e2024", as_of_date="2024-12-31", filed_date=None, source_url="x",
            parser_version=holdings_pipeline.SENATE_PARSER_VERSION,
        ))
        db_session.commit()
        paper = _senate_filing("p", title="Annual Report (Amendment)", filed="2024-02-01", office="Senator", paper=True)
        await _ingest_senate(db_session, [_senate_filing("e2024", title="Annual Report for CY 2024", filed=""), paper], {})
        assert db_session.query(FinancialDisclosure).one().later_filing_url is None

    async def test_the_note_is_written_even_when_the_phase_fails(self, db_session, senator):
        e2024 = _senate_filing("e2024", title="Annual Report for CY 2024", filed="2025-05-11")
        paper = _senate_filing("p", title="Annual Report", filed="2026-05-14", office="Senator", paper=True)
        n = holdings_pipeline.MIN_ATTEMPTS_FOR_OUTAGE
        others = []
        for i in range(n):
            db_session.add(Senator(id=f"X{i}", name=f"Pat Name{i}", state="XX", party="D", is_current=True))
            others.append({**_senate_filing(f"x{i}"), "last": f"Name{i}", "first": "Pat",
                           "office": f"Name{i}, Pat (Senator)"})
        db_session.commit()
        with pytest.raises(RuntimeError):
            # Five reports nobody can read: a parser regression fails the phase.
            await _ingest_senate(db_session, [e2024, paper, *others],
                                 {"e2024": [_row()], **{f"x{i}": None for i in range(n)}})
        stored = db_session.query(FinancialDisclosure).filter_by(senator_id="S1").one()
        assert stored.later_filing_url == paper["report_url"]


class TestPdfSniffing:
    def test_markup_quoting_the_header_is_not_a_pdf(self):
        from app.pipeline.fetch.house_ptr import _looks_like_pdf

        assert not _looks_like_pdf(b"<!doctype html><script>var x='%PDF-1.4';</script>")
        assert not _looks_like_pdf(b'{"error": "file %PDF-1.4 unavailable"}')
        assert not _looks_like_pdf(b"Service unavailable: %PDF-1.4")
        assert _looks_like_pdf(b"\xef\xbb\xbf\r\n%PDF-1.4 rest")
        assert _looks_like_pdf(b"\x00\x01\xff\xfe%PDF-1.7")


class TestReReadKeepsWhatWasKnown:
    async def test_a_row_that_says_less_doesnt_demote_the_stored_report(self, db_session, rep):
        """Tonight's index row for the stored amendment has no parseable
        filing date: it must still be re-read after a parser bump (not cut
        off by its own original), and keep its date and rank."""
        amend = {**_house_filing("AMEND", filing_date="2026-06-01"), "filing_type": "A", "prefix": "Hon."}
        orig = _house_filing("ORIG", filing_date="2026-05-01")
        await _ingest_house(db_session, {2025: [orig, amend]}, {"AMEND": AnnualReport("Member", [_row()])})
        assert db_session.query(FinancialDisclosure).one().filing_id == "AMEND"
        garbled = {**amend, "filing_date": ""}
        with patch.object(holdings_pipeline, "HOUSE_PARSER_VERSION", 99):
            _, fetch = await _ingest_house(db_session, {2025: [orig, garbled]},
                                           {"AMEND": AnnualReport("Member", [_row(), _row()])})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, stored.parser_version, stored.filed_date) == ("AMEND", 99, "2026-06-01")


class TestRankRules:
    async def test_an_amendment_supersedes_its_original_even_without_a_filing_date(self, db_session, rep):
        orig = _house_filing("ORIG", filing_date="2026-05-01")
        amend = {**_house_filing("AMEND", filing_date=""), "filing_type": "A", "prefix": "Hon."}
        reports = {"ORIG": AnnualReport("Member", [_row()]), "AMEND": AnnualReport("Member", [_row(), _row()])}
        await _ingest_house(db_session, {2025: [orig]}, reports)
        await _ingest_house(db_session, {2025: [orig, amend]}, reports)
        assert db_session.query(FinancialDisclosure).one().filing_id == "AMEND"

    async def test_among_paper_filings_an_original_goes_before_a_later_amendment(self, db_session, senator):
        """An undated amendment can amend any earlier report."""
        original = _senate_filing("p1", title="Annual Report", filed="2025-05-10", office="Senator", paper=True)
        amendment = _senate_filing("p2", title="Annual Report (Amendment)", filed="2026-02-01", office="Senator",
                                   paper=True)
        await _ingest_senate(db_session, [original, amendment], {})
        stored = db_session.query(FinancialDisclosure).one()
        assert stored.filing_id == "p1"
        assert stored.later_filing_label == "annual report amendment filed 2026-02-01"

    async def test_a_missing_filing_date_is_filled_in_once_the_row_has_one(self, db_session, senator):
        undated = _senate_filing("e2025", filed="")
        await _ingest_senate(db_session, [undated], {"e2025": [_row()]})
        assert db_session.query(FinancialDisclosure).one().filed_date is None
        await _ingest_senate(db_session, [_senate_filing("e2025", filed="2026-05-11")], {"e2025": [_row()]})
        assert db_session.query(FinancialDisclosure).one().filed_date == "2026-05-11"

    def test_new_filer_dates_without_zero_padding(self):
        f = _senate_filing("n", title="New Filer Report for 3/4/2026")
        assert _year(holdings_pipeline._senate_as_of(f)) == 2026
        assert holdings_pipeline._senate_fields(f)["report_label"] == "new-filer report as of 2026-03-04"

    async def test_a_note_that_cant_be_written_doesnt_fail_the_phase(self, db_session, senator):
        with patch.object(holdings_pipeline, "_note_later_filing", side_effect=RuntimeError("locked")):
            assert await _ingest_senate(db_session, [_senate_filing("e2025")], {"e2025": [_row()]}) == 1


class TestStoredAndTonightMerge:
    async def test_a_title_that_parses_later_fills_in_the_stored_rank(self, db_session, senator):
        """Stored while its title didn't parse: once it does, the stored
        row is repaired — so a partial search returning only an older
        report can't outrank it."""
        garbled = _senate_filing("cy2025", title="Annual Report for CY", filed="2026-05-11")
        await _ingest_senate(db_session, [garbled], {"cy2025": [_row()]})
        assert db_session.query(FinancialDisclosure).one().as_of_date is None
        await _ingest_senate(db_session, [_senate_filing("cy2025", filed="2026-05-11")], {"cy2025": [_row()]})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.as_of_date, _year(stored.as_of_date), stored.report_label) == ("2025-12-31", 2025, "2025 annual report")
        older = _senate_filing("cy2024", title="Annual Report for CY 2024", filed="2025-05-11")
        await _ingest_senate(db_session, [older], {"cy2024": [_row()]})
        assert db_session.query(FinancialDisclosure).one().filing_id == "cy2025"

    async def test_tonights_date_is_kept_when_its_title_says_less(self, db_session, senator):
        await _ingest_senate(db_session, [_senate_filing("cy2025", filed="")], {"cy2025": [_row()]})
        await _ingest_senate(db_session, [_senate_filing("cy2025", title="Annual Report for CY", filed="2026-05-11")],
                             {"cy2025": [_row()]})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.as_of_date, stored.filed_date) == ("2025-12-31", "2026-05-11")

    async def test_a_note_failure_alerts(self, db_session, senator):
        with patch.object(holdings_pipeline, "_note_later_filing", side_effect=RuntimeError("locked")), \
             patch.object(holdings_pipeline, "_alert") as alert:
            await _ingest_senate(db_session, [_senate_filing("e2025")], {"e2025": [_row()]})
        alert.assert_called_once()


class TestRepairIsUsedAndKept:
    async def test_a_repaired_rank_guards_against_an_older_report(self, db_session, senator):
        """Reproduces the review case: garbled title on night one; on night
        two the title parses but the re-read fails, and the older CY2024
        report must not replace it."""
        await _ingest_senate(db_session, [_senate_filing("cy2025", title="Annual Report for CY", filed="2026-05-11")],
                             {"cy2025": [_row()]})
        filings = [_senate_filing("cy2025", filed="2026-05-11"),
                   _senate_filing("cy2024", title="Annual Report for CY 2024", filed="2025-05-11")]
        with patch.object(holdings_pipeline, "SENATE_PARSER_VERSION", 2):
            await _ingest_senate(db_session, filings, {"cy2024": [_row()]})  # cy2025's re-read fails
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, stored.as_of_date, _year(stored.as_of_date)) == ("cy2025", "2025-12-31", 2025)

    async def test_the_later_filing_note_survives_a_search_that_misses_it(self, db_session, senator):
        e2025 = _senate_filing("e2025", filed="2026-05-11")
        paper = _senate_filing("p", title="Annual Report (Amendment)", filed="2026-08-12", office="Senator", paper=True)
        await _ingest_senate(db_session, [e2025, paper], {"e2025": [_row()]})
        await _ingest_senate(db_session, [e2025], {"e2025": [_row()]})  # the paper row's page failed to load
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.later_filing_url, stored.later_filing_filed) == (paper["report_url"], "2026-08-12")


class TestLaterFilingNote:
    async def test_the_note_survives_a_re_read_of_the_stored_report(self, db_session, senator):
        e2025 = _senate_filing("e2025", filed="2026-05-11")
        paper = _senate_filing("p", title="Annual Report (Amendment)", filed="2026-08-12", office="Senator", paper=True)
        await _ingest_senate(db_session, [e2025, paper], {"e2025": [_row()]})
        with patch.object(holdings_pipeline, "SENATE_PARSER_VERSION", 2):
            await _ingest_senate(db_session, [e2025], {"e2025": [_row(), _row()]})  # re-read; paper row missed
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.parser_version, stored.later_filing_url) == (2, paper["report_url"])

    async def test_an_electronic_filing_without_a_stated_year_is_named_too(self, db_session, senator):
        e2025 = _senate_filing("e2025", filed="2026-05-11")
        undated = _senate_filing("u", title="Annual Report for CY", filed="2026-08-12")
        await _ingest_senate(db_session, [e2025, undated], {"e2025": [_row()], "u": [_row()]})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, stored.later_filing_label) == ("e2025", "annual report filed 2026-08-12")


class TestSameDayAmendments:
    @pytest.mark.parametrize("order", [("A1", "A2"), ("A2", "A1")])
    async def test_the_later_document_wins_whatever_the_index_order(self, db_session, rep, order):
        filings = {
            "A1": {**_house_filing("1002", filing_date="2026-06-01"), "filing_type": "A", "prefix": "Hon."},
            "A2": {**_house_filing("1003", filing_date="2026-06-01"), "filing_type": "A", "prefix": "Hon."},
        }
        reports = {"1002": AnnualReport("Member", [_row()]), "1003": AnnualReport("Member", [_row(), _row()])}
        await _ingest_house(db_session, {2025: [filings[k] for k in order]}, reports)
        assert db_session.query(FinancialDisclosure).one().filing_id == "1003"

    async def test_senate_amendment_numbers_break_a_same_day_tie(self, db_session, senator):
        first = _senate_filing("a1", title="Annual Report for CY 2025 (Amendment 1)", filed="2026-07-01")
        second = _senate_filing("a2", title="Annual Report for CY 2025 (Amendment 2)", filed="2026-07-01")
        await _ingest_senate(db_session, [second, first], {"a1": [_row()], "a2": [_row()]})
        assert db_session.query(FinancialDisclosure).one().filing_id == "a2"


class TestAmendmentOrderSignals:
    async def test_the_house_document_id_outranks_an_inherited_filing_date(self, db_session, rep):
        """The index can give an amendment its original's filing date, or
        none that parses; the later document id still wins."""
        a1 = {**_house_filing("10078299", filing_date="2026-06-01"), "filing_type": "A", "prefix": "Hon."}
        a2 = {**_house_filing("10078402", filing_date=""), "filing_type": "A", "prefix": "Hon."}
        reports = {"10078299": AnnualReport("Member", [_row()]), "10078402": AnnualReport("Member", [_row()])}
        await _ingest_house(db_session, {2025: [a1, a2]}, reports)
        assert db_session.query(FinancialDisclosure).one().filing_id == "10078402"

    async def test_an_unresolvable_tie_resolves_the_same_way_every_run(self, db_session, senator):
        x = _senate_filing("x", title="Annual Report (Amendment)", filed="2026-07-01", office="Senator", paper=True)
        y = _senate_filing("y", title="Annual Report (Amendment)", filed="2026-07-01", office="Senator", paper=True)
        await _ingest_senate(db_session, [x, y], {})
        first = db_session.query(FinancialDisclosure).one().filing_id
        await _ingest_senate(db_session, [y, x], {})
        assert db_session.query(FinancialDisclosure).one().filing_id == first


class TestSenateAmendmentDates:
    async def test_a_later_unnumbered_amendment_beats_an_earlier_numbered_one(self, db_session, senator):
        numbered = _senate_filing("a1", title="Annual Report for CY 2025 (Amendment 1)", filed="2026-06-01")
        unnumbered = _senate_filing("a", title="Annual Report for CY 2025 (Amendment)", filed="2026-08-01")
        await _ingest_senate(db_session, [numbered, unnumbered], {"a1": [_row()], "a": [_row()]})
        assert db_session.query(FinancialDisclosure).one().filing_id == "a"


class TestPhaseTable:
    async def test_each_step_runs_its_own_ingest_whatever_the_order(self):
        from unittest.mock import MagicMock

        calls = []

        async def house(_db, _client):
            calls.append("house")
            return 1

        async def senate(_db, _client):
            calls.append("senate")
            return 2

        steps = list(reversed(holdings_pipeline.HOLDINGS_STEPS))
        with patch.object(holdings_pipeline, "HOLDINGS_STEPS", steps), \
             patch.object(holdings_pipeline, "ingest_house_holdings", house), \
             patch.object(holdings_pipeline, "ingest_senate_holdings", senate):
            counts, errors = await holdings_pipeline.run_holdings_phases(MagicMock(), None, MagicMock())
        assert (calls, counts, errors) == (["senate", "house"], {"senate_holdings": 2, "house_holdings": 1}, [])


class TestNoteAndPhaseEdges:
    async def test_same_day_undated_filings_give_a_stable_note(self, db_session, senator):
        e2025 = _senate_filing("e2025", filed="2026-05-11")
        p1 = _senate_filing("p1", title="Annual Report", filed="2026-08-12", office="Senator", paper=True)
        p2 = _senate_filing("p2", title="Annual Report (Amendment)", filed="2026-08-12", office="Senator", paper=True)
        await _ingest_senate(db_session, [e2025, p1, p2], {"e2025": [_row()]})
        first = db_session.query(FinancialDisclosure).one().later_filing_url
        await _ingest_senate(db_session, [e2025, p2, p1], {"e2025": [_row()]})
        assert db_session.query(FinancialDisclosure).one().later_filing_url == first

    async def test_a_step_without_an_ingest_fails_as_one_phase(self):
        from unittest.mock import MagicMock

        async def house(_db, _client):
            return 1

        steps = [*holdings_pipeline.HOLDINGS_STEPS, ("president_holdings", "fetch", "x")]
        with patch.object(holdings_pipeline, "HOLDINGS_STEPS", steps), \
             patch.object(holdings_pipeline, "ingest_house_holdings", house), \
             patch.object(holdings_pipeline, "ingest_senate_holdings", house), \
             patch.object(holdings_pipeline, "_alert"):
            counts, errors = await holdings_pipeline.run_holdings_phases(MagicMock(), None, MagicMock())
        assert counts["house_holdings"] == 1 and len(errors) == 1 and "president_holdings" in errors[0]


class TestIndexAndMatchEdges:
    async def test_last_years_index_failing_to_load_fails_the_phase(self, db_session, rep):
        async def index(_client, _db, year):
            return None if year == 2025 else [_house_filing("OLD", year=2024)]

        with patch.object(holdings_pipeline, "fetch_annual_filing_index", side_effect=index), \
             patch.object(holdings_pipeline, "utcnow") as now:
            now.return_value.year = 2026
            with pytest.raises(RuntimeError, match="2025 could not be loaded"):
                await holdings_pipeline.ingest_house_holdings(db_session, None)
        assert db_session.query(FinancialDisclosure).count() == 0

    async def test_a_formal_first_name_still_matches_the_member(self, db_session):
        """The index often uses a formal or nickname variant ("Rohit" for Ro
        Khanna); the district and surname decide."""
        db_session.add(Representative(id="R1", name="Ro Khanna", state="CA", district=17, party="D", is_current=True))
        db_session.commit()
        index = {2025: [_house_filing("K", first="Rohit", last="Khanna", district="CA17")]}
        count, _ = await _ingest_house(db_session, index, {"K": AnnualReport("Member", [_row()])})
        assert count == 1

    async def test_an_undated_filing_the_same_day_is_named(self, db_session, senator):
        e2025 = _senate_filing("e2025", filed="2026-05-15")
        paper = _senate_filing("p", title="Annual Report (Amendment)", filed="2026-05-15", office="Senator", paper=True)
        await _ingest_senate(db_session, [e2025, paper], {"e2025": [_row()]})
        assert db_session.query(FinancialDisclosure).one().later_filing_url == paper["report_url"]


class TestSameDayNotesKeep:
    async def test_a_same_day_note_survives_a_search_that_misses_it_and_a_re_read(self, db_session, senator):
        e2025 = _senate_filing("e2025", filed="2026-05-15")
        paper = _senate_filing("p", title="Annual Report (Amendment)", filed="2026-05-15", office="Senator", paper=True)
        await _ingest_senate(db_session, [e2025, paper], {"e2025": [_row()]})
        await _ingest_senate(db_session, [e2025], {"e2025": [_row()]})
        with patch.object(holdings_pipeline, "SENATE_PARSER_VERSION", 2):
            await _ingest_senate(db_session, [e2025], {"e2025": [_row(), _row()]})
        assert db_session.query(FinancialDisclosure).one().later_filing_url == paper["report_url"]

    async def test_the_older_years_index_failing_fails_the_phase_too(self, db_session, rep):
        """Until May it holds nearly every member's current report."""
        async def index(_client, _db, year):
            return [_house_filing("NEW")] if year == 2025 else None

        with patch.object(holdings_pipeline, "fetch_annual_filing_index", side_effect=index), \
             patch.object(holdings_pipeline, "utcnow") as now:
            now.return_value.year = 2026
            with pytest.raises(RuntimeError, match="2024 could not be loaded"):
                await holdings_pipeline.ingest_house_holdings(db_session, None)

    def test_only_a_new_filer_title_is_dated_by_its_date(self):
        assert holdings_pipeline._senate_as_of({"title": "Annual Report (Amendment) 06/01/2026"}) is None
        assert holdings_pipeline._senate_as_of({"title": "New Filer Report for 06/01/2026"}) == "2026-06-01"


class TestRound40:
    async def test_the_senate_search_starts_in_the_earliest_report_year(self, db_session, senator):
        search = AsyncMock(return_value=[_senate_filing("e2025")])
        with patch.object(holdings_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value="tok"), \
             patch.object(holdings_pipeline, "search_annual_filings", search), \
             patch.object(holdings_pipeline, "fetch_senate_annual",
                          AsyncMock(return_value=AnnualReport(None, [_row()], None))), \
             patch.object(holdings_pipeline, "utcnow") as now:
            now.return_value.year = 2026
            await holdings_pipeline.ingest_senate_holdings(db_session, None)
        search.assert_awaited_once_with("2024-01-01")

    async def test_a_stored_as_of_date_survives_a_bump_whose_row_has_none(self, db_session, senator):
        """Giving it up would drop the report below the ones it superseded."""
        await _ingest_senate(db_session, [_senate_filing("cy2025", filed="2026-05-11")], {"cy2025": [_row()]})
        garbled = _senate_filing("cy2025", title="Annual Report for CY", filed="2026-05-11")
        older = _senate_filing("cy2024", title="Annual Report for CY 2024", filed="2025-05-11")
        with patch.object(holdings_pipeline, "SENATE_PARSER_VERSION", 2):
            await _ingest_senate(db_session, [garbled, older], {"cy2025": [_row()], "cy2024": [_row()]})
        stored = db_session.query(FinancialDisclosure).one()
        assert (stored.filing_id, stored.as_of_date) == ("cy2025", "2025-12-31")


class TestDuplicateRows:
    def test_a_row_repeated_across_pages_is_counted_once(self):
        from app.pipeline.fetch import senate_ptr

        by_id: dict = {}
        unparsed: set = set()
        page = [{"url": "a"}, {"url": "b"}, {"url": None, "n": 1}]
        with patch.object(senate_ptr, "_parse_search_row",
                          side_effect=lambda r: {"report_url": f"https://e/view/annual/{r['url']}/"} if r["url"] else None):
            senate_ptr._collect_rows(by_id, unparsed, page)
            senate_ptr._collect_rows(by_id, unparsed, [{"url": "b"}, {"url": "c"}, {"url": None, "n": 1}])
        assert (sorted(by_id), len(unparsed)) == (["a", "b", "c"], 1)

    async def test_a_house_document_listed_twice_is_fetched_once(self, db_session, rep):
        filing = _house_filing("10078188")
        _, fetch = await _ingest_house(db_session, {2025: [filing, dict(filing)]}, {})
        assert fetch.call_count == 1

class TestReportVersion:
    async def test_a_repair_moves_the_report_version_forward(self, db_session, senator):
        await _ingest_senate(db_session, [_senate_filing("cy2025", title="Annual Report for CY", filed="2026-05-11")],
                             {"cy2025": [_row()]})
        before = get_senator_holdings(db_session, "S1").report_version
        await _ingest_senate(db_session, [_senate_filing("cy2025", filed="2026-05-11")], {"cy2025": [_row()]})
        after = get_senator_holdings(db_session, "S1")
        assert after.report_label == "2025 annual report" and after.report_version > before
