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


async def _fetch(db_session, status_code, text=None):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    with patch(
        "app.pipeline.fetch.congress_legislators.fetch_with_retry",
        new=AsyncMock(return_value=response),
    ):
        return await fetch_bioguide_to_fec_ids(AsyncMock(), db_session)


class TestFetchBioguideToFecIds:
    @pytest.mark.asyncio
    async def test_parses_bioguide_to_fec_mapping(self, db_session):
        # Exact equality also confirms a legislator with a bioguide but no
        # `fec` key, and one with no `id` block at all, don't raise or
        # pollute the mapping.
        assert await _fetch(db_session, 200, _SAMPLE_YAML) == {"C001075": ["H8LA00017", "S4LA00107"]}

    @pytest.mark.parametrize("status_code, text", [
        pytest.param(500, None, id="fetch_failure"),
        pytest.param(200, "not: valid: yaml: [unclosed", id="unparseable_yaml"),
    ])
    @pytest.mark.asyncio
    async def test_failure_returns_empty_dict_not_none(self, db_session, status_code, text):
        assert await _fetch(db_session, status_code, text) == {}


class TestSelectFecIdForOffice:
    @pytest.mark.parametrize("fec_ids, office, expected", [
        pytest.param(["H8LA00017", "S4LA00107"], "S", "S4LA00107", id="senate_id_when_member_has_both"),
        pytest.param(["H8LA00017", "S4LA00107"], "H", "H8LA00017", id="house_id_when_member_has_both"),
        pytest.param(["S4LA00107"], "s", "S4LA00107", id="case_insensitive_office_match"),
        pytest.param(["H8LA00017"], "S", None, id="no_matching_office"),
        pytest.param([], "S", None, id="empty_list"),
        # The simple single-match helper stays first-match-in-crosswalk-order
        # for callers that can't verify (see TestSelectAllFecIdsForOffice).
        pytest.param(["H4NY04158", "H2NY04244"], "H", "H4NY04158", id="two_same_office_ids_returns_first"),
    ])
    def test_select(self, fec_ids, office, expected):
        assert select_fec_id_for_office(fec_ids, office) == expected


class TestSelectAllFecIdsForOffice:
    """2026-08-26 audit: three sitting members showed $0 raised because a
    crosswalk entry carried two ids for the same office — one stale/
    invalid, one real — and select_fec_id_for_office's first-match
    behavior isn't enough on its own to recover from that; a caller that
    can verify needs every candidate, not just the first."""

    @pytest.mark.parametrize("fec_ids, office, expected", [
        pytest.param(["H4NY04158", "S4LA00107", "H2NY04244"], "H", ["H4NY04158", "H2NY04244"],
                     id="every_match_in_crosswalk_order"),
        pytest.param(["H8LA00017", "S4LA00107"], "S", ["S4LA00107"], id="single_match_one_item_list"),
        pytest.param(["H8LA00017"], "S", [], id="no_matching_office_empty_list"),
    ])
    def test_select_all(self, fec_ids, office, expected):
        assert select_all_fec_ids_for_office(fec_ids, office) == expected
