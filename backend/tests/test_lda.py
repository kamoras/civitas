"""Tests for fetch/lda.py — registered lobbying spend and the bills an
organization's own LDA filings name, added to donor-vote lobbying matches.
Shared by senate_pipeline.py and house_pipeline.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.pipeline.analyze.lobbying_records import TitlePool
from app.pipeline.fetch import lda
from app.pipeline.fetch.lda import (
    LobbyingActivity,
    alert_if_lda_down,
    enrich_lobbying_matches_with_lda,
    fetch_lobbying_activity,
)

VOTES = [
    {"billId": "H.R. 1492", "billName": "To amend title XI of the Social Security Act to equalize the negotiation period", "vote": "Yea", "date": "2025-06-01"},
    {"billId": "S. 1040", "billName": "A bill to amend the Federal Trade Commission Act to prohibit product hopping", "vote": "Nay", "date": "2025-07-01"},
    {"billId": "PN999-1", "billName": "A nomination", "vote": "Yea", "date": "2025-07-02"},
    {"billId": "H.R. 8774", "billName": "Farm Workforce Modernization Act", "vote": "Not Voting", "date": "2025-07-03"},
    {"billId": "H.R. 82", "billName": "Defund National Endowment for the Humanities Act of 2025", "vote": "Nay", "date": "2025-09-02"},
    {"billId": "S. 2587", "billName": "Department of Education Appropriations Act, 2026", "vote": "Yea", "date": "2025-09-01"},
]


def _mention(bill_id, after, year=2025, url="https://lda.gov/filings/public/filing/x/print/", before=""):
    return {"billId": bill_id, "before": before, "after": after, "filingUrl": url, "filingYear": year, "registrant": "ACME LLC"}


def _activity(total=0.0, mentions=()):
    return LobbyingActivity(total=total, mentions=list(mentions))


TITLES = {
    "HR.1492": ["To amend title XI of the Social Security Act to equalize the negotiation period between small-molecule and biologic candidates"],
    "S.1040": ["Affordable Prescriptions for Patients Act of 2025"],
    "HR.82": ["Defund National Endowment for the Humanities Act of 2025"],
    # Siblings: every title word but one is shared.
    "HR.4016": ["Department of Defense Appropriations Act, 2026"],
    "S.2587": ["Department of Education Appropriations Act, 2026"],
}


PREVIOUS_TITLES = {"HR.82": ["Social Security Fairness Act of 2023"]}


async def _titles(client, db, congress, bill_key):
    return list((TITLES if congress == 119 else PREVIOUS_TITLES).get(bill_key, []))


@pytest.fixture(autouse=True)
def _no_real_client():
    """enrich_* opens its own client; no test here may reach the network."""
    with patch.object(lda, "make_async_client", return_value=MagicMock(
        __aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=None),
    )), patch.object(lda, "_bill_titles", new=_titles), patch.object(
        lda, "_title_pool", new=AsyncMock(return_value=TitlePool(TITLES)),
    ):
        yield


class TestEnrich:
    @pytest.mark.asyncio
    async def test_adds_spend_and_description_when_spend_found(self, db_session):
        matches = [{"lobbyistOrg": "Big Pharma Inc", "description": "base description"}]
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(return_value=_activity(250_000.0))):
            stats = await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)

        assert matches[0]["lobbyingSpend"] == 250_000
        assert matches[0]["lobbyingChecked"] is True
        assert "250,000" in matches[0]["description"]
        assert matches[0]["description"].startswith("base description")
        assert stats == {"lookups": 1, "failed": 0}

    @pytest.mark.asyncio
    async def test_zero_spend_does_not_alter_description(self, db_session):
        matches = [{"lobbyistOrg": "Small Org", "description": "base description"}]
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(return_value=_activity(0.0))):
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)

        assert matches[0]["lobbyingSpend"] == 0
        assert matches[0]["lobbyingChecked"] is True
        assert matches[0]["description"] == "base description"

    @pytest.mark.asyncio
    async def test_failed_lookup_is_unknown_not_zero(self, db_session):
        # The 2026 lda.senate.gov -> lda.gov move failed every lookup, and
        # each read as "$0 registered lobbying".
        matches = [{"lobbyistOrg": "Goldman Sachs", "description": "d", "lobbyingSpend": 0}]
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(return_value=None)):
            stats = await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)
        assert matches[0]["lobbyingChecked"] is False
        assert stats == {"lookups": 1, "failed": 1}

    @pytest.mark.asyncio
    async def test_empty_matches_list_is_a_noop(self, db_session):
        matches = []
        assert await enrich_lobbying_matches_with_lda(matches, db_session, 2025) == {"lookups": 0, "failed": 0}
        assert matches == []

    @pytest.mark.asyncio
    async def test_one_org_failing_does_not_block_the_others(self, db_session):
        matches = [
            {"lobbyistOrg": "Failing Org", "description": ""},
            {"lobbyistOrg": "Working Org", "description": ""},
        ]
        mock = AsyncMock(side_effect=[RuntimeError("LDA API down"), _activity(100_000.0), _activity(0.0)])
        with patch.object(lda, "fetch_lobbying_activity", new=mock):
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)

        assert matches[0]["lobbyingChecked"] is False
        assert "lobbyingSpend" not in matches[0]
        assert matches[1]["lobbyingSpend"] == 100_000

    @pytest.mark.asyncio
    async def test_a_lookup_that_raises_counts_toward_the_outage_alert(self, db_session):
        matches = [{"lobbyistOrg": "A", "description": ""}, {"lobbyistOrg": "B", "description": ""}]
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(side_effect=RuntimeError("db gone"))):
            stats = await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)
        assert stats == {"lookups": 2, "failed": 2}

    @pytest.mark.asyncio
    async def test_searches_the_pacs_sponsor_not_the_pac(self, db_session):
        matches = [{"lobbyistOrg": "Jp Morgan Chase Federal PAC", "lobbyingClient": "JPMORGAN CHASE & CO.", "description": ""}]
        mock = AsyncMock(return_value=_activity(1.0))
        with patch.object(lda, "fetch_lobbying_activity", new=mock):
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)
        assert {c.args[2] for c in mock.await_args_list} == {"JPMORGAN CHASE & CO."}

    @pytest.mark.asyncio
    async def test_industry_label_headline_is_never_searched(self, db_session):
        matches = [{"lobbyistOrg": "Finance industry", "lobbyingClient": None, "description": ""}]
        mock = AsyncMock()
        with patch.object(lda, "fetch_lobbying_activity", new=mock):
            stats = await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)
        mock.assert_not_awaited()
        assert stats["lookups"] == 0 and "lobbyingChecked" not in matches[0]


class TestLobbiedBills:
    async def _run(self, db_session, mentions, votes=VOTES):
        matches = [{"lobbyistOrg": "Pfizer", "description": "", "billsInfluenced": ["S.2296"]}]
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(return_value=_activity(5.0, mentions))):
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025, votes=votes, congress=119)
        return matches[0]

    @pytest.mark.asyncio
    async def test_named_bill_with_matching_title_is_linked(self, db_session):
        m = await self._run(db_session, [_mention(
            "HR.1492", "prescription drug value, including , to equalize the negotiation period between "
            "small-molecule and biologic candidates under the Drug Price Negotiation Program",
        )])
        assert [b["billId"] for b in m["lobbiedBills"]] == ["H.R. 1492"]
        assert m["lobbiedBills"][0]["vote"] == "Yea"
        assert m["lobbiedBills"][0]["filingUrl"].startswith("https://lda.gov/")
        assert m["billsInfluenced"] == ["S.2296", "H.R. 1492"]

    @pytest.mark.asyncio
    async def test_same_number_different_bill_is_not(self, db_session):
        # The number matches a bill the member voted on, but the filer's
        # words name a different bill: filings cite earlier congresses'.
        m = await self._run(db_session, [_mention("S.1040", "Medication Affordability and Patent Integrity Act")])
        assert m["lobbiedBills"] == []

    @pytest.mark.asyncio
    async def test_a_sibling_bills_title_is_not_this_bill(self, db_session):
        # Found in calibration: a filing about the Defense appropriations
        # act, citing the Education one's number, scored 0.72 against it.
        m = await self._run(db_session, [_mention("S.2587", "Department of Defense Appropriations Act, 2026")])
        assert m["lobbiedBills"] == []

    @pytest.mark.asyncio
    async def test_the_previous_congresss_bill_with_this_number_wins(self, db_session):
        # Found in calibration: "... Defund National Endowment for the
        # Humanities Act of 2025 H.R. 82 - Social Security Fairness Act".
        # The filer's own words for H.R. 82 name the 118th's bill.
        m = await self._run(db_session, [_mention(
            "HR.82", " - Social Security Fairness Act",
            before="Defund National Endowment for the Humanities Act of 2025 ",
        )])
        assert m["lobbiedBills"] == []

    @pytest.mark.asyncio
    async def test_nothing_is_claimed_without_the_congress_to_compare(self, db_session):
        with patch.object(lda, "_title_pool", new=AsyncMock(return_value=None)):
            m = await self._run(db_session, [_mention("S.1040", "Affordable Prescriptions for Patients Act")])
        assert m["lobbiedBills"] == []

    @pytest.mark.asyncio
    async def test_a_filing_from_another_congress_is_ignored(self, db_session):
        m = await self._run(db_session, [_mention("S.1040", "Affordable Prescriptions for Patients Act", year=2024)])
        assert m["lobbiedBills"] == []

    @pytest.mark.asyncio
    async def test_bill_the_member_did_not_vote_on_is_ignored(self, db_session):
        # H.R. 8774 is in the member's record only as "Not Voting".
        m = await self._run(db_session, [_mention("HR.8774", "Farm Workforce Modernization Act")])
        assert m["lobbiedBills"] == []


class TestFetch:
    def _response(self, status, payload=None):
        resp = MagicMock(status_code=status)
        resp.json.return_value = payload or {}
        if not 200 <= status < 300:  # httpx raises for redirects too
            resp.raise_for_status.side_effect = httpx.HTTPStatusError("x", request=MagicMock(), response=resp)
        return resp

    @pytest.mark.asyncio
    async def test_follows_pages_and_redirects_and_caches(self, db_session):
        page1 = {"next": "https://lda.gov/api/v1/filings/?page=2", "results": [{
            "filing_type": "Q1", "income": "20000.00", "filing_year": 2025,
            "filing_document_url": "https://lda.gov/f/1/print/", "registrant": {"name": "ALTRIUS GROUP, LLC"},
            "lobbying_activities": [{"description": "S.1040, Drug Competition Enhancement Act"}],
        }]}
        page2 = {"next": None, "results": [{"filing_type": "RR", "income": "999"}]}
        client = MagicMock()
        client.get = AsyncMock(side_effect=[self._response(200, page1), self._response(200, page2)])
        with patch.object(lda._rate_limiter, "acquire", new=AsyncMock()):
            act = await fetch_lobbying_activity(client, db_session, "Pfizer", 2025)
            again = await fetch_lobbying_activity(client, db_session, "Pfizer", 2025)
        assert act.total == 20_000.0  # RR registrations carry no amounts
        assert [m["billId"] for m in act.mentions] == ["S.1040"]
        assert client.get.await_args_list[0].kwargs["follow_redirects"] is True
        assert client.get.await_args_list[0].args[0].startswith("https://lda.gov/")
        assert again.total == act.total and client.get.await_count == 2  # cached

    @pytest.mark.asyncio
    async def test_failure_returns_none_and_is_not_cached(self, db_session):
        client = MagicMock()
        client.get = AsyncMock(side_effect=[self._response(301), self._response(200, {"next": None, "results": []})])
        with patch.object(lda._rate_limiter, "acquire", new=AsyncMock()):
            assert await fetch_lobbying_activity(client, db_session, "Goldman Sachs", 2025) is None
            retry = await fetch_lobbying_activity(client, db_session, "Goldman Sachs", 2025)
        assert retry is not None and retry.total == 0.0

    @pytest.mark.asyncio
    async def test_rate_limit_is_a_failure(self, db_session):
        client = MagicMock()
        client.get = AsyncMock(return_value=self._response(429))
        with patch.object(lda._rate_limiter, "acquire", new=AsyncMock()):
            assert await fetch_lobbying_activity(client, db_session, "Goldman Sachs", 2025) is None


class TestAlert:
    def test_alerts_only_when_every_lookup_failed(self):
        with patch("app.ops_alerts.send_ops_alert") as send:
            alert_if_lda_down({"lookups": 3, "failed": 2}, "senate")
            alert_if_lda_down({"lookups": 0, "failed": 0}, "senate")
            send.assert_not_called()
            alert_if_lda_down({"lookups": 3, "failed": 3}, "senate")
            send.assert_called_once()
