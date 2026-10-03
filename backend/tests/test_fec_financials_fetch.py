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
from app.pipeline.fetch.fec import fetch_candidate_financials, select_recent_elections
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
