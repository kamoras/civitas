"""Tests for the @unitedstates/congress-legislators bioguide<->FEC-
candidate-ID crosswalk (congress_legislators.py) — the authoritative,
no-name-matching-required alternative to fec.py's nickname-table
fallback. Real shape verified live (2026-07): Bill Cassidy's entry has
id.bioguide=C001075, id.fec=[H8LA00017, S4LA00107]."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.fetch.congress_legislators import (
    fetch_bioguide_to_fec_ids,
    select_all_fec_ids_for_office,
    select_fec_id_for_office,
)

_SAMPLE_YAML = """
- id:
    bioguide: C001075
    fec:
    - H8LA00017
    - S4LA00107
  name:
    official_full: Bill Cassidy
- id:
    bioguide: G000359
  name:
    official_full: No FEC Ids At All
- name:
    official_full: No Id Block At All
"""


class TestFetchBioguideToFecIds:
    @pytest.mark.asyncio
    async def test_parses_bioguide_to_fec_mapping(self, db_session):
        response = MagicMock()
        response.status_code = 200
        response.text = _SAMPLE_YAML
        with patch(
            "app.pipeline.fetch.congress_legislators.fetch_with_retry",
            new=AsyncMock(return_value=response),
        ):
            result = await fetch_bioguide_to_fec_ids(AsyncMock(), db_session)
        assert result == {"C001075": ["H8LA00017", "S4LA00107"]}

    @pytest.mark.asyncio
    async def test_entries_without_fec_ids_are_skipped_not_crashed_on(self, db_session):
        # Confirms a legislator with a bioguide but no `fec` key, and one
        # with no `id` block at all, don't raise or pollute the mapping.
        response = MagicMock()
        response.status_code = 200
        response.text = _SAMPLE_YAML
        with patch(
            "app.pipeline.fetch.congress_legislators.fetch_with_retry",
            new=AsyncMock(return_value=response),
        ):
            result = await fetch_bioguide_to_fec_ids(AsyncMock(), db_session)
        assert "G000359" not in result
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_empty_dict_not_none(self, db_session):
        response = MagicMock()
        response.status_code = 500
        with patch(
            "app.pipeline.fetch.congress_legislators.fetch_with_retry",
            new=AsyncMock(return_value=response),
        ):
            result = await fetch_bioguide_to_fec_ids(AsyncMock(), db_session)
        assert result == {}

    @pytest.mark.asyncio
    async def test_unparseable_yaml_returns_empty_dict(self, db_session):
        response = MagicMock()
        response.status_code = 200
        response.text = "not: valid: yaml: [unclosed"
        with patch(
            "app.pipeline.fetch.congress_legislators.fetch_with_retry",
            new=AsyncMock(return_value=response),
        ):
            result = await fetch_bioguide_to_fec_ids(AsyncMock(), db_session)
        assert result == {}


class TestSelectFecIdForOffice:
    def test_picks_the_senate_id_when_member_has_both(self):
        assert select_fec_id_for_office(["H8LA00017", "S4LA00107"], "S") == "S4LA00107"

    def test_picks_the_house_id_when_member_has_both(self):
        assert select_fec_id_for_office(["H8LA00017", "S4LA00107"], "H") == "H8LA00017"

    def test_case_insensitive_office_match(self):
        assert select_fec_id_for_office(["S4LA00107"], "s") == "S4LA00107"

    def test_no_matching_office_returns_none(self):
        assert select_fec_id_for_office(["H8LA00017"], "S") is None

    def test_empty_list_returns_none(self):
        assert select_fec_id_for_office([], "S") is None


class TestSelectAllFecIdsForOffice:
    """2026-08-26 audit: three sitting members showed $0 raised because a
    crosswalk entry carried two ids for the same office — one stale/
    invalid, one real — and select_fec_id_for_office's first-match
    behavior isn't enough on its own to recover from that; a caller that
    can verify needs every candidate, not just the first."""

    def test_returns_every_match_in_crosswalk_order(self):
        assert select_all_fec_ids_for_office(
            ["H4NY04158", "S4LA00107", "H2NY04244"], "H",
        ) == ["H4NY04158", "H2NY04244"]

    def test_single_match_returns_a_one_item_list(self):
        assert select_all_fec_ids_for_office(["H8LA00017", "S4LA00107"], "S") == ["S4LA00107"]

    def test_no_matching_office_returns_empty_list(self):
        assert select_all_fec_ids_for_office(["H8LA00017"], "S") == []

    def test_select_fec_id_for_office_still_returns_only_the_first(self):
        # The simple single-match helper is unchanged for callers that
        # can't verify — first-match-in-crosswalk-order behavior.
        assert select_fec_id_for_office(["H4NY04158", "H2NY04244"], "H") == "H4NY04158"
