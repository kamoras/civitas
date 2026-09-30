"""Tests for president_ptr.py and stock_pipeline._ingest_president.

Network is mocked (no live requests); the 278-T transaction table parse
itself is ptr_common.py's, covered by test_ptr_common.py. What's tested
here is everything specific to the presidential path: picking this
president's periodic transaction reports out of an API response that also
lists hundreds of other appointees' filings, refusing to follow a scraped
link off-host, and the ingest's dedupe/classification wiring.

The row shape these fixtures imitate was confirmed live on 2026-08-26 via a
Playwright network trace of OGE's public disclosure-search page (see the
module docstring's LIVE-VERIFIED note) — real field names, real anchor
markup inside `type`, real "Request this Document" rows for filings with no
direct PDF.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models import President, PresidentTrade
from app.pipeline.fetch.president_ptr import (
    _filing_id_for,
    _names_this_president,
    _parse_index,
    fetch_and_parse_ptr,
    fetch_ptr_filing_index,
)
from app.pipeline.fetch.ptr_common import TradeRow


def _row(name, title, type_html, doc_date="2026-08-12T04:20:00"):
    return {"name": name, "title": title, "type": type_html, "docDate": doc_date, "agency": "", "level": ""}


_SAMPLE_ROWS = [
    _row(
        "Trump, Donald J.", "President",
        "<a href='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/AAA/$FILE/trump-278t-111425.pdf'>"
        "278 Transaction</a>",
        doc_date="2025-11-14T04:20:00",
    ),
    _row(
        "Trump, Donald J.", "President",
        "<a href='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/BBB/$FILE/trump-2026-278annual.pdf'>"
        "Annual (2026)</a>",
        doc_date="2026-06-30T04:20:00",
    ),
    _row(
        "Zinberg, Joel", "Special Government Employee",
        "<a href='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/CCC/$FILE/zinberg-278t-090325.pdf'>"
        "278 Transaction</a>",
    ),
    _row(
        "Trump, Eric F.", "Advisor",
        "<a href='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/DDD/$FILE/etrump-278t-070125.pdf'>"
        "278 Transaction</a>",
    ),
    _row(
        "Vance, James D.", "Vice President",
        "<a href='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/EEE/$FILE/vance-278t-081225.pdf'>"
        "278 Transaction</a>",
    ),
    _row(
        "Weaver, Douglas", "Commissioner",
        "278 Transaction (<a href='https://extapps2.oge.gov/201/Presiden.nsf/201%20Request?OpenForm"
        "&Filer=Weaver'>Request this Document</a>)",
    ),
]


class TestIndexParsing:
    def test_keeps_only_this_presidents_periodic_and_annual_reports(self):
        filings = _parse_index(_SAMPLE_ROWS, "Donald Trump")

        # Exactly the president's own two rows. Every other official's
        # filing is absent: another appointee (zinberg), a relative sharing
        # the surname (etrump — relatives hold appointed positions and file
        # their own 278-Ts; attributing one to the president would be a
        # factual claim about who traded what, not a near miss), the vice
        # president (vance), and a row with no direct PDF (weaver).
        assert [(f["kind"], f["pdf_url"]) for f in filings] == [
            ("periodic", "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/AAA/$FILE/trump-278t-111425.pdf"),
            # The annual report is read by president_fd (its Part 7), never
            # as a 278-T: its holdings tables would read as transactions.
            ("annual", "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/BBB/$FILE/trump-2026-278annual.pdf"),
        ]
        assert filings[0]["doc_id"].startswith("trump-278t-111425-")
        assert filings[0]["filing_date"] == "2025-11-14"

    def test_a_different_president_matches_nothing_here(self):
        assert _parse_index(_SAMPLE_ROWS, "Joseph Biden") == []

    @pytest.mark.parametrize("type_html", [
        # A filing that isn't directly downloadable renders its `type` cell
        # as a link to a "Request this Document" form instead of a PDF —
        # the same shape as a row genuinely missing a link would have.
        pytest.param(
            "278 Transaction (<a href='https://extapps2.oge.gov/201/Presiden.nsf/201%20Request?OpenForm"
            "&Filer=Trump'>Request this Document</a>)",
            id="no_direct_pdf_link",
        ),
        pytest.param("<a href='https://evil.example.com/ptr.pdf'>278 Transaction</a>", id="link_off_allowed_hosts"),
        pytest.param("", id="no_type_markup"),
    ])
    def test_a_row_without_a_usable_pdf_link_is_not_counted_as_a_filing(self, type_html):
        assert _parse_index([_row("Trump, Donald J.", "President", type_html)], "Donald Trump") == []

    def test_the_vice_presidents_filing_is_not_the_presidents(self):
        filings = _parse_index(_SAMPLE_ROWS, "James Vance")
        # Surname matches and the position cell contains the word
        # "President" — but "Vice President" is not the office, so only the
        # given-name match can qualify this row, and here it does. (It is
        # never picked up for the president himself: see the first test.)
        assert len(filings) == 1
        assert "vance" in filings[0]["pdf_url"]


class TestFilerMatching:
    @pytest.mark.parametrize("cells, president, expected", [
        pytest.param(["Trump, Donald J.", "President"], "Donald Trump", True, id="lastname_first_format"),
        pytest.param(["Trumbull, Lyman", "Senator"], "Donald Trump", False, id="near_miss_surname"),
        # The roster's display name and the index's formal name disagree
        # for several presidents ("Jimmy" vs "James E."). The office cell is
        # what keeps that from rejecting a genuine presidential filing.
        pytest.param(["Carter, James E.", "President"], "Jimmy Carter", True, id="formal_first_name_via_office_cell"),
        pytest.param(["Carter, James E.", "Vice President"], "Jimmy Carter", False,
                     id="office_fallback_rejects_vice_president"),
        pytest.param(["Carter, James E.", "Assistant to the President"], "Jimmy Carter", False,
                     id="office_fallback_rejects_staff_title"),
        pytest.param(["Bush, George", "President"], "George H. W. Bush", True, id="initials_and_suffixes_in_roster_name"),
        pytest.param(["Trump, Donald J.", "President"], "", False, id="empty_president_name"),
    ])
    def test_names_this_president(self, cells, president, expected):
        assert _names_this_president(cells, president) is expected


class TestFilingIds:
    def test_generic_filenames_do_not_collide(self):
        """A Domino attachment link ends in whatever the filer named the
        file. Keying on the bare filename collapsed every "download.pdf"
        into one id and silently dropped whole filings."""
        a = _filing_id_for("https://extapps2.oge.gov/201/Presiden.nsf/a1b2/$FILE/download.pdf")
        b = _filing_id_for("https://extapps2.oge.gov/201/Presiden.nsf/c3d4/$FILE/download.pdf")
        assert a != b

    def test_the_same_filing_keeps_one_id_across_host_and_query_changes(self):
        base = _filing_id_for("https://extapps2.oge.gov/201/x/$FILE/ptr.pdf")
        assert _filing_id_for("https://www.oge.gov/201/x/$FILE/ptr.pdf?open=1") == base


class TestFetchIndex:
    @pytest.mark.asyncio
    async def test_zero_parsed_filings_is_not_cached_as_a_real_result(self, db_session):
        """A structural break must not be frozen in as 'no filings' for a
        day — the next run has to try the live API again."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": []}
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests",
            new_callable=AsyncMock, return_value=resp,
        ), patch("app.pipeline.fetch.president_ptr.api_cache_set") as mock_cache_set:
            filings = await fetch_ptr_filing_index(db_session, "Donald Trump")

        assert filings == []
        mock_cache_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_empty(self, db_session):
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests",
            new_callable=AsyncMock, return_value=None,
        ):
            assert await fetch_ptr_filing_index(db_session, "Donald Trump") == []

    @pytest.mark.asyncio
    async def test_a_successful_fetch_is_parsed_and_cached(self, db_session):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": _SAMPLE_ROWS}
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests",
            new_callable=AsyncMock, return_value=resp,
        ) as mock_fetch, patch("app.pipeline.fetch.president_ptr.api_cache_set") as mock_cache_set:
            filings = await fetch_ptr_filing_index(db_session, "Donald Trump")

        assert [f["kind"] for f in filings] == ["periodic", "annual"]
        assert "trump-278t-111425" in filings[0]["pdf_url"]
        mock_cache_set.assert_called_once()
        # Server-side filtered on surname so the ~16k-row full index is
        # never pulled down for one president.
        assert mock_fetch.call_args.kwargs["params"]["columns[3][search][value]"] == "trump"


class TestFetchAndParseFiling:
    @pytest.mark.asyncio
    async def test_tags_rows_with_source_and_filing_id(self, db_session):
        filing = {
            "doc_id": "trump-278t-111425",
            "pdf_url": "https://extapps2.oge.gov/201/Presiden.nsf/files/trump-278t-111425.pdf",
        }
        parsed = [TradeRow(
            ticker=None, asset_name="Bitcoin", owner="self",
            transaction_type="purchase", transaction_date="2025-10-30",
            disclosure_date="2025-11-14", amount_low=1000001.0, amount_high=5000000.0,
        )]
        resp = MagicMock(status_code=200, content=b"%PDF-fake")
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests",
            new_callable=AsyncMock, return_value=resp,
        ), patch(
            "app.pipeline.fetch.president_ptr.parse_pdf_bytes", return_value=(parsed, "text"),
        ):
            rows = await fetch_and_parse_ptr(db_session, filing)

        assert len(rows) == 1
        assert rows[0].source_url == filing["pdf_url"]
        assert rows[0].filing_id == "trump-278t-111425"
        assert rows[0].parse_confidence == "text"

    @pytest.mark.asyncio
    async def test_refuses_a_link_pointing_off_the_allowed_hosts(self, db_session):
        """pdf_url comes from a scraped page, so it's untrusted input."""
        filing = {"doc_id": "evil", "pdf_url": "https://evil.example.com/x.pdf"}
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests", new_callable=AsyncMock,
        ) as mock_fetch:
            rows = await fetch_and_parse_ptr(db_session, filing)

        assert rows == []
        mock_fetch.assert_not_called()


class TestIngestPresident:
    """stock_pipeline._ingest_president — storage, dedupe, and the
    untickered-asset classification that crypto lines depend on."""

    @staticmethod
    def _seed_president(db_session) -> President:
        president = President(
            id="trump-47", name="Donald Trump", party="R", number=47,
            term_start="2025-01-20", is_current=True,
        )
        db_session.add(president)
        db_session.commit()
        return president

    @staticmethod
    def _rows() -> list[TradeRow]:
        return [
            TradeRow(
                ticker=None, asset_name="Bitcoin", owner="self",
                transaction_type="purchase", transaction_date="2025-10-30",
                disclosure_date="2025-11-14", amount_low=1000001.0, amount_high=5000000.0,
                filing_id="f1", source_url="https://www.whitehouse.gov/x.pdf",
            ),
            TradeRow(
                ticker="AAPL", asset_name="Apple Inc. (AAPL)", owner="spouse",
                transaction_type="sale_full", transaction_date="2025-10-31",
                disclosure_date="2025-11-14", amount_low=15001.0, amount_high=50000.0,
                filing_id="f1", source_url="https://www.whitehouse.gov/x.pdf",
            ),
        ]

    async def _ingest(self, db_session, rows, filings=None):
        from app.pipeline.stock_pipeline import _ingest_president

        with (
            patch(
                "app.pipeline.stock_pipeline.fetch_president_ptr_index",
                new_callable=AsyncMock,
                return_value=filings if filings is not None else [
                    {"doc_id": "f1", "filing_date": "2025-11-14", "pdf_url": "https://www.whitehouse.gov/x.pdf",
                     "kind": "periodic"},
                ],
            ),
            patch(
                "app.pipeline.stock_pipeline.fetch_president_ptr",
                new_callable=AsyncMock, return_value=rows,
            ),
            patch(
                "app.pipeline.stock_pipeline.resolve_tickers",
                new_callable=AsyncMock, return_value={"AAPL": "Apple Inc"},
            ),
            patch(
                "app.pipeline.stock_pipeline.classify_batch_with_learning",
                return_value=({"Apple Inc": "TECH", "Bitcoin": "CRYPTO"}, []),
            ),
        ):
            return await _ingest_president(db_session, AsyncMock())

    async def test_stores_disclosed_transactions_against_the_current_president(self, db_session):
        self._seed_president(db_session)

        inserted = await self._ingest(db_session, self._rows())

        assert inserted == 2
        stored = db_session.query(PresidentTrade).order_by(PresidentTrade.transaction_date).all()
        assert [t.president_id for t in stored] == ["trump-47", "trump-47"]
        assert [t.transaction_type for t in stored] == ["purchase", "sale_full"]
        assert [t.owner for t in stored] == ["self", "spouse"]
        # Ranges as filed — the form reports no single figure and none is invented.
        assert (stored[0].amount_low, stored[0].amount_high) == (1000001.0, 5000000.0)

    async def test_untickered_crypto_is_classified_not_left_unclassified(self, db_session):
        """A ticker-only classification pass leaves every crypto line
        UNCLASSIFIED — virtual currency has no SEC ticker to resolve."""
        self._seed_president(db_session)

        await self._ingest(db_session, self._rows())

        by_asset = {t.asset_name: t.industry for t in db_session.query(PresidentTrade).all()}
        assert by_asset["Bitcoin"] == "CRYPTO"
        assert by_asset["Apple Inc. (AAPL)"] == "TECH"

    async def test_disclosure_timeliness_is_computed_from_the_filed_dates(self, db_session):
        self._seed_president(db_session)

        await self._ingest(db_session, self._rows())

        trade = db_session.query(PresidentTrade).filter_by(asset_name="Bitcoin").one()
        assert trade.days_to_disclose == 15  # 2025-10-30 -> 2025-11-14

    async def test_an_already_ingested_filing_is_not_stored_twice(self, db_session):
        self._seed_president(db_session)

        assert await self._ingest(db_session, self._rows()) == 2
        assert await self._ingest(db_session, self._rows()) == 0
        assert db_session.query(PresidentTrade).count() == 2

    async def test_two_current_rows_resolve_to_the_later_presidency(self, db_session):
        """A roster mid-transition can briefly carry two is_current rows;
        an unordered pick would file the trades under whichever one the
        query happened to return first."""
        db_session.add(President(
            id="biden-46", name="Joseph Biden", party="D", number=46,
            term_start="2021-01-20", is_current=True,
        ))
        self._seed_president(db_session)

        await self._ingest(db_session, self._rows())

        assert {t.president_id for t in db_session.query(PresidentTrade).all()} == {"trump-47"}

    async def test_no_current_president_row_is_a_no_op(self, db_session):
        assert await self._ingest(db_session, self._rows()) == 0
        assert db_session.query(PresidentTrade).count() == 0

    async def test_a_filing_that_parses_to_nothing_stores_nothing(self, db_session):
        self._seed_president(db_session)

        assert await self._ingest(db_session, []) == 0
        assert db_session.query(PresidentTrade).count() == 0


class TestTradesEndpoint:
    """/api/presidents/{id}/stock-trades — called directly with db_session,
    the same convention as test_elections_api.py (no TestClient harness
    exists in this suite)."""

    @staticmethod
    def _seed(db_session, *, trades: int = 0) -> President:
        president = President(
            id="trump-47", name="Donald Trump", party="R", number=47,
            term_start="2025-01-20", is_current=True,
        )
        db_session.add(president)
        for i in range(trades):
            db_session.add(PresidentTrade(
                president_id="trump-47", ticker=None, asset_name=f"Bitcoin {i}",
                owner="self", transaction_type="purchase",
                transaction_date=f"2025-11-{i + 1:02d}", disclosure_date="2025-12-30",
                days_to_disclose=60 - i, amount_low=1000001.0, amount_high=5000000.0,
                industry="CRYPTO", source_url="https://www.whitehouse.gov/x.pdf",
                filing_id="f1",
            ))
        db_session.commit()
        return president

    def test_unknown_president_is_a_404_not_an_empty_list(self, db_session):
        from app.api import presidents

        with pytest.raises(HTTPException) as exc:
            presidents.get_trades("nobody-99", 1, 15, db_session)
        assert exc.value.status_code == 404

    def test_returns_disclosed_ranges_and_flags_late_filings(self, db_session):
        from app.api import presidents

        self._seed(db_session, trades=2)
        body = json.loads(presidents.get_trades("trump-47", 1, 15, db_session).body)

        assert body["total"] == 2
        # 60 and 59 days out — both past the 45-day statutory deadline.
        assert body["lateCount"] == 2
        assert all(t["late"] for t in body["trades"])
        assert body["trades"][0]["amountLow"] == 1000001.0
        assert body["trades"][0]["amountHigh"] == 5000000.0
        # Nothing resembling a profit/gain is exposed — the form has none.
        assert not any(
            key in body["trades"][0] for key in ("profit", "gain", "return", "pnl")
        )

    def test_an_open_ended_bracket_is_flagged_and_never_given_a_ceiling(self, db_session):
        """The form's top bracket discloses a floor and no maximum. The
        stored high figure is a placeholder, and the API has to say so or a
        client will render an invented upper bound as a disclosed one."""
        from app.api import presidents

        self._seed(db_session)
        db_session.add(PresidentTrade(
            president_id="trump-47", ticker=None, asset_name="Bitcoin",
            owner="self", transaction_type="purchase",
            transaction_date="2025-10-30", disclosure_date="2025-11-14",
            days_to_disclose=15, amount_low=50000000.0, amount_high=50000000.0,
            industry="CRYPTO", source_url="https://www.whitehouse.gov/x.pdf",
            filing_id="f-open",
        ))
        db_session.commit()

        body = json.loads(presidents.get_trades("trump-47", 1, 15, db_session).body)
        assert body["trades"][0]["amountOpenEnded"] is True

    def test_an_ordinary_bracket_is_not_flagged_open_ended(self, db_session):
        from app.api import presidents

        self._seed(db_session, trades=1)
        body = json.loads(presidents.get_trades("trump-47", 1, 15, db_session).body)
        assert body["trades"][0]["amountOpenEnded"] is False

    def test_a_president_with_no_filings_returns_an_empty_page_not_a_404(self, db_session):
        from app.api import presidents

        self._seed(db_session)
        body = json.loads(presidents.get_trades("trump-47", 1, 15, db_session).body)

        assert body["total"] == 0
        assert body["trades"] == []
