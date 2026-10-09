"""fetch_candidate_financials reads every totals row a candidate has.

The API ignores sort=-cycle for election-full rows (cycle: null), so a short
page is an arbitrary subset of a candidate's elections. At per_page=4 the
most recent completed election was missing for long-serving members, who
were then scored on the wrong campaign: live on 2026-10-03, one Senate
leader showed $5.0M raised against the $68.1M FEC reports for 2019-20
alone, and a House member of 39 years $375K against $10.2M for 2023-24."""

import re
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.cache import api_cache_set
from app.pipeline.fetch.fec import (
    FecUnavailable,
    fetch_candidate_committees,
    fetch_candidate_financials,
    select_recent_elections,
    with_seat_election,
)
from app.pipeline.transform.normalize_finance import summarize_election_totals


def _election(year: int, receipts: float) -> dict:
    return {"cycle": None, "candidate_election_year": year, "receipts": receipts, "contributions": receipts}


# A senator first elected in 1984, re-elected every six years, with a
# committee for an election they are not running in. Order as the API
# returned it: no order at all.
SENATOR_ROWS = [
    _election(1990, 5_000_000), _election(2026, 7_900_000), _election(1984, 2_000_000),
    _election(2002, 9_000_000), _election(2008, 21_000_000), _election(2020, 73_950_000),
    _election(1996, 7_000_000), _election(2014, 30_000_000),
]


async def _fec(client, url, retries=None):
    """The API: one page of `per_page` rows, in its own (useless) order."""
    per_page = int(re.search(r"per_page=(\d+)", url).group(1))
    return {"results": SENATOR_ROWS[:per_page]}


@pytest.mark.asyncio
async def test_every_row_is_requested_and_the_last_completed_election_wins(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 3, 12, 0))
    with patch("app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock) as fetch:
        fetch.side_effect = _fec
        rows = await fetch_candidate_financials(None, db_session, "S2KY00012")

    assert len(rows) == len(SENATOR_ROWS)
    (window,) = select_recent_elections(rows, office="S")
    assert window["candidate_election_year"] == 2020
    assert summarize_election_totals([window])["total_raised"] == 73_950_000


@pytest.mark.asyncio
async def test_a_four_row_sample_cached_under_the_old_key_is_not_read(db_session, freeze_utcnow):
    """Rows cached before the fix were a 4-row sample. They sit under the
    old key, which nothing reads now, so the next run fetches every row."""
    freeze_utcnow(datetime(2026, 10, 3, 12, 0))
    api_cache_set(db_session, "fec", "candidate-financials-S2KY00012", SENATOR_ROWS[:4])
    with patch("app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock) as fetch:
        fetch.side_effect = _fec
        rows = await fetch_candidate_financials(None, db_session, "S2KY00012")
    fetch.assert_called_once()
    assert len(rows) == len(SENATOR_ROWS)


@pytest.mark.asyncio
async def test_an_unreachable_fec_raises_and_caches_nothing(db_session, freeze_utcnow):
    """A failed fetch is not a candidate who raised nothing: it raises, so
    the member is skipped and keeps the stored funding, and the next run
    asks again instead of reading a cached []."""
    freeze_utcnow(datetime(2026, 10, 3, 12, 0))
    with patch("app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock, return_value=None):
        with pytest.raises(FecUnavailable):
            await fetch_candidate_financials(None, db_session, "S2KY00012")
        with pytest.raises(FecUnavailable):
            await fetch_candidate_committees(None, db_session, "S2KY00012")
    with patch("app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock) as fetch:
        fetch.side_effect = _fec
        rows = await fetch_candidate_financials(None, db_session, "S2KY00012")
    assert len(rows) == len(SENATOR_ROWS)


# Candidate totals as FEC served them for a member seated by the 2024
# general: the 2026 campaign and a 2020 run, no 2024 election.
UNLINKED_ROWS = [_election(2026, 1_781_840), _election(2020, 763_934)]
COMMITTEES = [{"committee_id": "C1"}, {"committee_id": "C2"}]
COMMITTEE_TOTALS = {
    "C1": [{"cycle": 2020, "receipts": 763_934}],
    "C2": [
        {"cycle": 2026, "receipts": 1_740_534},
        {"cycle": 2024, "receipts": 1_841_418, "contributions": 1_800_000,
         "other_political_committee_contributions": 400_000,
         "individual_unitemized_contributions": 300_000,
         "individual_itemized_contributions": 1_100_000},
    ],
}


async def _committee_fec(client, url, retries=None):
    return {"results": COMMITTEE_TOTALS[re.search(r"/committee/(C\d)/", url).group(1)]}


@pytest.mark.asyncio
async def test_the_seat_winning_race_comes_from_committee_totals(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 8, 12, 0))
    assert summarize_election_totals(select_recent_elections(UNLINKED_ROWS, office="H"))["total_raised"] == 1_781_840
    with patch("app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock) as fetch:
        fetch.side_effect = _committee_fec
        rows = await with_seat_election(None, db_session, UNLINKED_ROWS, COMMITTEES, 2024)
    (window,) = select_recent_elections(rows, office="H")
    assert window["candidate_election_year"] == 2024
    totals = summarize_election_totals([window])
    assert (totals["total_raised"], totals["total_from_pacs"]) == (1_841_418, 400_000)


@pytest.mark.asyncio
async def test_a_linked_seat_race_is_left_alone(db_session):
    rows = [_election(2024, 900_000), *UNLINKED_ROWS]
    with patch("app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock) as fetch:
        assert await with_seat_election(None, db_session, rows, COMMITTEES, 2024) == rows
    fetch.assert_not_called()


def test_only_a_member_seated_at_convening_was_seated_by_the_general():
    from app.pipeline.house_pipeline import seated_at_convening

    assert seated_at_convening("2025-01-03", 119)
    assert not seated_at_convening("2025-04-02", 119)  # a special election
    assert not seated_at_convening(None, 119)
