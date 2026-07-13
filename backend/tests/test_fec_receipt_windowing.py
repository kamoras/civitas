"""Tests for FEC receipt-detail cycle windowing.

fetch_committee_receipts/fetch_pac_receipts/fetch_aggregated_contributors
previously had no time window at all — top-donor and industry-breakdown
detail was drawn from a committee's entire career while the receipt
totals it's compared against are windowed to the 2 most recent elections
(select_recent_elections). 2026-07 audit finding; see fetch/fec.py.
"""

from app.pipeline.fetch.fec import _cycle_query, _cycle_tag


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
