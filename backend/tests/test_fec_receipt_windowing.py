"""Tests for FEC receipt-detail cycle windowing.

fetch_committee_receipts/fetch_pac_receipts/fetch_aggregated_contributors
previously had no time window at all — top-donor and industry-breakdown
detail was drawn from a committee's entire career while the receipt
totals it's compared against are windowed to the 2 most recent elections
(select_recent_elections). 2026-07 audit finding; see fetch/fec.py.
"""

from datetime import datetime
from unittest.mock import patch

from app.pipeline.fetch.fec import (
    _cycle_query,
    _cycle_tag,
    _sort_financials_recent_first,
    financials_election_year,
    select_recent_elections,
)


def test_cycle_query_formats_repeated_params():
    q = _cycle_query([2024, 2018])
    assert "two_year_transaction_period=2024" in q
    assert "two_year_transaction_period=2018" in q


def test_cycle_query_dedupes_and_sorts():
    q = _cycle_query([2024, 2022, 2024])
    # sorted ascending, each cycle appears exactly once
    assert q == "&two_year_transaction_period=2022&two_year_transaction_period=2024"


def test_cycle_query_empty_when_no_cycles():
    assert _cycle_query(None) == ""
    assert _cycle_query([]) == ""


def test_cycle_tag_used_for_cache_key_disambiguation():
    assert _cycle_tag([2024, 2022, 2024]) == "2022-2024"
    assert _cycle_tag(None) == "all"
    assert _cycle_tag([]) == "all"


def test_cycle_tag_differs_for_different_windows():
    # Different windows must not collide on the same ApiCache row —
    # this is what actually fixes the "career data cached under one
    # key regardless of window" bug.
    assert _cycle_tag([2024, 2022]) != _cycle_tag([2018, 2016])
    assert _cycle_tag([2024, 2022]) != _cycle_tag(None)


def test_select_recent_elections_defaults_to_most_recent_only():
    # Funding is windowed to the candidate's most recent election only
    # (their current mandate's campaign), not a 2-election lookback —
    # see select_recent_elections's docstring for the "current term"
    # rationale.
    financials = [
        {"candidate_election_year": 2024, "receipts": 100},
        {"candidate_election_year": 2018, "receipts": 200},
    ]
    result = select_recent_elections(financials)
    assert len(result) == 1
    assert result[0]["candidate_election_year"] == 2024


def _frozen_utcnow():
    return datetime(2026, 7, 15)


def test_financials_election_year_ignores_raw_cycle_fallback():
    # A row with no candidate_election_year (e.g. an off-cycle filing
    # period with no election) must not be mistaken for a real election
    # via its raw `cycle` filing-period label — the actual bug behind a
    # senator re-elected in 2024 showing ~$50K "total raised" instead of
    # their real $5.8M campaign (2026-07 audit, Angus King / issue found
    # via external review).
    assert financials_election_year({"cycle": 2026, "receipts": 48607}) is None
    assert financials_election_year({"candidate_election_year": 2024, "cycle": 2024}) == 2024


def test_select_recent_elections_skips_dormant_off_cycle_row_with_no_election_year():
    # Angus King scenario: real 2024 election ($5.8M) plus a dormant
    # 2025-2026 off-cycle filing-period row (near-zero receipts, no
    # confirmed election) that must not outrank the real election just
    # because its raw `cycle` number is numerically larger.
    financials = [
        {"candidate_election_year": 2024, "cycle": 2024, "receipts": 5_800_000},
        {"cycle": 2026, "receipts": 48_607},  # no candidate_election_year — dormant
    ]
    with patch("app.pipeline.fetch.fec.utcnow", _frozen_utcnow):
        result = select_recent_elections(financials)
    assert len(result) == 1
    assert result[0]["receipts"] == 5_800_000


def test_select_recent_elections_excludes_future_election_year():
    # Even if candidate_election_year IS populated, a year that hasn't
    # happened yet cannot be "the most recent election."
    financials = [
        {"candidate_election_year": 2024, "receipts": 5_800_000},
        {"candidate_election_year": 2030, "receipts": 12_000},  # not yet held
    ]
    with patch("app.pipeline.fetch.fec.utcnow", _frozen_utcnow):
        result = select_recent_elections(financials)
    assert len(result) == 1
    assert result[0]["candidate_election_year"] == 2024


def test_sort_financials_recent_first_puts_dormant_row_last():
    financials = [
        {"cycle": 2026, "receipts": 48_607},  # no candidate_election_year
        {"candidate_election_year": 2024, "receipts": 5_800_000},
    ]
    with patch("app.pipeline.fetch.fec.utcnow", _frozen_utcnow):
        result = _sort_financials_recent_first(financials)
    assert result[0]["receipts"] == 5_800_000
