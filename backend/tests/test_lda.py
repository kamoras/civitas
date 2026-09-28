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


def _mention(bill_id, after, year=2025, url="https://lda.gov/filings/public/filing/x/print/", before="", posted="2025-04-20"):
    return {"billId": bill_id, "before": before, "after": after, "filingYear": year,
            "filings": [{"url": url, "registrant": "ACME LLC", "posted": posted}]}


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
def _fresh_verdicts():
    lda._verdicts.clear()
    yield


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
    async def test_a_failed_current_year_counts_toward_the_outage_alert(self, db_session):
        # The finished year can come from the 30-day cache while every live
        # request fails; that must still look like an outage.
        matches = [{"lobbyistOrg": "Pfizer", "description": ""}]
        mock = AsyncMock(side_effect=lambda c, d, org, year: _activity(9.0) if year == 2025 else None)
        with patch.object(lda, "fetch_lobbying_activity", new=mock):
            stats = await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)
        assert stats == {"lookups": 1, "failed": 1}
        assert matches[0]["lobbyingChecked"] is True and matches[0]["lobbyingSpend"] == 9

    @pytest.mark.asyncio
    async def test_a_capped_total_is_described_as_a_floor(self, db_session):
        matches = [{"lobbyistOrg": "Pfizer", "description": ""}]
        capped = LobbyingActivity(total=5_000_000.0, complete=False)
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(return_value=capped)):
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)
        assert "at least $5,000,000" in matches[0]["description"]

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
    async def test_one_match_counts_once_however_it_fails(self, db_session):
        # Found in review: a failed year plus a later exception counted one
        # match twice, so one bad match could fire the all-failed alert.
        matches = [{"lobbyistOrg": "A", "description": ""}, {"lobbyistOrg": "B", "description": ""}]
        mock = AsyncMock(side_effect=lambda c, d, org, year: None if (org == "A" and year == 2026) else _activity(1.0))
        with patch.object(lda, "fetch_lobbying_activity", new=mock), \
                patch.object(lda, "lobbied_bills_for", new=AsyncMock(side_effect=[RuntimeError("x"), []])):
            stats = await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)
        assert stats == {"lookups": 2, "failed": 1}

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
        assert [b["billId"] for b in m["lobbiedBills"]] == ["HR.1492"]
        assert m["lobbiedBills"][0]["label"] == "H.R. 1492"
        assert m["lobbiedBills"][0]["vote"] == "Yea"
        assert m["lobbiedBills"][0]["filingUrl"].startswith("https://lda.gov/")
        assert m["lobbiedBills"][0]["filingCount"] == 1
        # The topical list keeps only bills no filing names.
        assert m["billsInfluenced"] == ["S.2296"]

    @pytest.mark.asyncio
    async def test_a_named_bill_leaves_the_topical_list_whatever_its_spelling(self, db_session):
        matches = [{"lobbyistOrg": "Pfizer", "description": "", "billsInfluenced": ["H.R. 1492", "S.2296"]}]
        mention = _mention("HR.1492", ", to equalize the negotiation period between small-molecule and biologic candidates")
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(return_value=_activity(5.0, [mention]))):
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025, votes=VOTES, congress=119)
        assert matches[0]["billsInfluenced"] == ["S.2296"]

    @pytest.mark.asyncio
    async def test_a_house_recent_roll_call_matches_on_its_measure(self, db_session):
        # Found in review: the topical list holds the roll call's synthetic
        # id, which no spelling rule maps to "HR.1492".
        votes = [{"billId": "HouseRC-2025-309", "measureId": "HR.1492", "billName": "", "vote": "Nay", "date": "2025-06-01"}]
        matches = [{"lobbyistOrg": "Pfizer", "description": "", "billsInfluenced": ["HouseRC-2025-309"]}]
        mention = _mention("HR.1492", ", to equalize the negotiation period between small-molecule and biologic candidates")
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(return_value=_activity(5.0, [mention]))):
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025, votes=votes, congress=119)
        assert [b["billId"] for b in matches[0]["lobbiedBills"]] == ["HR.1492"]
        assert matches[0]["billsInfluenced"] == []

    @pytest.mark.asyncio
    async def test_links_the_latest_filing_and_counts_every_one(self, db_session):
        text = ", to equalize the negotiation period between small-molecule and biologic candidates"
        mention = _mention("HR.1492", text, url="https://lda.gov/q1/", posted="2025-04-20")
        mention["filings"] += [
            {"url": "https://lda.gov/q3/", "registrant": "FIRM B", "posted": "2025-10-20"},
            {"url": "https://lda.gov/q2/", "registrant": "FIRM A", "posted": "2025-07-20"},
        ]
        m = await self._run(db_session, [mention])
        bill = m["lobbiedBills"][0]
        assert bill["filingUrl"] == "https://lda.gov/q3/" and bill["registrant"] == "FIRM B"
        assert bill["filingCount"] == 3

    @pytest.mark.asyncio
    async def test_a_failed_previous_congress_lookup_claims_nothing(self, db_session):
        async def _prev_fails(client, db, congress, bill_key):
            return None if congress == 118 else list(TITLES.get(bill_key, []))

        with patch.object(lda, "_bill_titles", new=_prev_fails):
            m = await self._run(db_session, [_mention(
                "HR.1492", ", to equalize the negotiation period between small-molecule and biologic candidates",
            )])
        assert m["lobbiedBills"] == []

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
            "client": {"name": "PFIZER INC."},
            "filing_type": "Q1", "income": "20000.00", "filing_year": 2025,
            "filing_document_url": "https://lda.gov/f/1/print/", "registrant": {"name": "ALTRIUS GROUP, LLC"},
            "lobbying_activities": [{"description": "S.1040, Drug Competition Enhancement Act"}],
        }]}
        page2 = {"next": None, "results": [
            {"client": {"name": "PFIZER INC."}, "filing_type": "RR", "income": "999"},
            # The registry's loose name search returns other clients too.
            {"client": {"name": "PFIZERVILLE ISD"}, "filing_type": "Q1", "income": "70000",
             "lobbying_activities": [{"description": "H.R. 5, Parents Bill of Rights Act"}]},
        ]}
        client = MagicMock()
        client.get = AsyncMock(side_effect=[self._response(200, page1), self._response(200, page2)])
        with patch.object(lda._rate_limiter, "acquire", new=AsyncMock()):
            act = await fetch_lobbying_activity(client, db_session, "Pfizer", 2025)
            again = await fetch_lobbying_activity(client, db_session, "Pfizer", 2025)
        assert act.total == 20_000.0  # RR registrations carry no amounts
        assert [m["billId"] for m in act.mentions] == ["S.1040"]
        assert act.mentions[0]["filings"][0]["url"] == "https://lda.gov/f/1/print/"
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


class TestVotedBills:
    def test_passage_wins_over_a_later_procedural_vote(self):
        votes = [
            {"billId": "HouseRC-2025-10", "measureId": "HR.1", "vote": "Yea", "date": "2025-05-22", "motionType": "passage"},
            {"billId": "HouseRC-2025-11", "measureId": "HR.1", "vote": "Nay", "date": "2025-07-03", "motionType": "procedural"},
        ]
        assert lda._voted_bills(votes)["HR.1"]["vote"] == "Yea"

    def test_senate_text_dates_order_by_date_not_alphabet(self):
        # "September ..." sorts after "October ..." as a string.
        votes = [
            {"billId": "H.R. 4", "vote": "Nay", "date": "September 18, 2025, 05:34 PM", "motionType": "cloture"},
            {"billId": "H.R. 4", "vote": "Yea", "date": "October 2, 2025, 11:00 AM", "motionType": "cloture"},
        ]
        assert lda._voted_bills(votes)["HR.4"]["vote"] == "Yea"

    def test_the_shown_vote_says_what_it_decided(self):
        votes = [{"billId": "S. 1040", "vote": "Nay", "date": "2025-07-01", "motionType": "cloture"}]
        assert lda._voted_bills(votes)["S.1040"]["motionType"] == "cloture"


class TestCacheWindow:
    def test_a_finished_year_is_open_until_q4_reports_are_due(self):
        from datetime import datetime

        with patch.object(lda, "utcnow", return_value=datetime(2027, 1, 10)):
            assert not lda._year_is_closed(2026)
            assert lda._year_is_closed(2025)
        with patch.object(lda, "utcnow", return_value=datetime(2027, 2, 1)):
            assert lda._year_is_closed(2026)


@pytest.mark.asyncio
async def test_an_empty_client_name_is_unknown_not_zero(db_session):
    assert await fetch_lobbying_activity(MagicMock(), db_session, " ", 2025) is None


class TestClientMatching:
    def test_the_search_name_drops_a_legal_form(self):
        assert lda.search_name("JPMorgan Chase & Co.") == "JPMORGAN CHASE"
        assert lda.search_name("The Boeing Company") == "BOEING"
        assert lda.search_name("Inc") == "INC"

    @pytest.mark.parametrize("searched,client,same", [
        ("APPLE", "APPLE INC.", True),
        ("APPLE", "APPLETON INTERNATIONAL AIRPORT", False),
        ("APPLE", "US APPLE ASSOCIATION", False),
        ("APPLE", "WILMERHALE ON BEHALF OF APPLE INC.", True),
        ("KOCH", "KOCH GOVERNMENT AFFAIRS, LLC", True),
        ("KOCH", "DR. KOCHO ANGJUSHEV", False),
        ("VERIZON", "THE LIVINGSTON GROUP, LLC (VERIZON COMMUNICATIONS, INC.)", True),
        ("CVS", "OAK STREET HEALTH D/B/A CVS HEALTH", True),
        ("AMERICAN MEDICAL ASSOCIATION", "AMERICAN VETERINARY MEDICAL ASSOCIATION", False),
        ("NATIONAL EDUCATION ASSOCIATION", "NATIONAL ASSOCIATION FOR MUSIC EDUCATION", False),
        ("JPMORGAN CHASE", "JPMORGAN CHASE HOLDINGS LLC", True),
    ])
    def test_real_client_names_from_2025_filings(self, searched, client, same):
        assert lda.is_same_client(searched, client) is same


@pytest.mark.asyncio
async def test_the_spend_year_failing_keeps_the_other_years_bills(db_session):
    text = ", to equalize the negotiation period between small-molecule and biologic candidates"
    matches = [{"lobbyistOrg": "Pfizer", "description": "", "billsInfluenced": []}]
    mock = AsyncMock(side_effect=lambda c, d, org, year: None if year == 2025 else _activity(1.0, [_mention("HR.1492", text, year=2026)]))
    with patch.object(lda, "fetch_lobbying_activity", new=mock):
        stats = await enrich_lobbying_matches_with_lda(matches, db_session, 2025, votes=VOTES, congress=119)
    assert matches[0]["lobbyingChecked"] is False and "lobbyingSpend" not in matches[0]
    assert [b["billId"] for b in matches[0]["lobbiedBills"]] == ["HR.1492"]
    assert stats["failed"] == 1


def test_the_motion_reaches_the_api():
    from app.schemas import LobbyingMatchSchema

    dumped = LobbyingMatchSchema(
        lobbyist_org="x", industry="PHARMA", lobbying_spend=0, donation_to_senator=0,
        bills_influenced=[], description="",
        lobbied_bills=[{"billId": "HR.1", "motionType": "cloture", "vote": "Nay"}],
    ).model_dump(by_alias=True)
    assert dumped["lobbiedBills"][0]["motionType"] == "cloture"


class TestOwnFilings:
    def test_an_own_name_client_excludes_other_companies_with_the_prefix(self):
        # Found in review, live: "COCA COLA" also returned an independent
        # bottler, whose spend was added to Coca-Cola's.
        filings = [{"client": {"name": "THE COCA-COLA COMPANY"}},
                   {"client": {"name": "COCA-COLA BOTTLING COMPANY UNITED, INC."}}]
        assert lda._own_filings("COCA COLA", filings) == filings[:1]

    def test_without_one_the_named_entities_count(self):
        filings = [{"client": {"name": "JPMORGAN CHASE HOLDINGS LLC"}},
                   {"client": {"name": "JPMORGAN CHASE HOLDINGS, LLC"}}]
        assert lda._own_filings(lda.search_name("JPMORGAN CHASE & CO."), filings) == filings

    def test_a_registrant_filing_for_the_exact_name_counts_as_own(self):
        filings = [{"client": {"name": "ELI LILLY AND COMPANY"}},
                   {"client": {"name": "TIBER CREEK HEALTH STRATEGIES, INC. ON BEHALF OF ELI LILLY AND COMPANY"}},
                   {"client": {"name": "ELI LILLY BIO"}}]
        assert lda._own_filings(lda.search_name("Eli Lilly & Company"), filings) == filings[:2]

    @pytest.mark.asyncio
    async def test_the_description_names_the_clients_counted(self, db_session):
        matches = [{"lobbyistOrg": "Coca-Cola", "description": ""}]
        act = LobbyingActivity(total=10.0, clients=["THE COCA-COLA COMPANY"])
        with patch.object(lda, "fetch_lobbying_activity", new=AsyncMock(return_value=act)):
            await enrich_lobbying_matches_with_lda(matches, db_session, 2025, congress=119)
        assert "filed as THE COCA-COLA COMPANY." in matches[0]["description"]
