"""Tests for find_candidate's district-mismatch fallback.

FEC's `district` field on a candidate record can lag a member's current
Congress.gov district after redistricting — a district-constrained search
then finds nothing even though the candidate exists (2026-07 audit: 16
sitting representatives had $0 recorded funding because of exactly this;
Cal Green (TX-9 on Congress.gov) is on file with FEC under district 18).
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.cache import api_cache_get
from app.pipeline.fetch.fec import _candidate_latest_election, find_candidate


def _candidate(name: str, candidate_id: str, district: str) -> dict:
    return {"name": name, "candidate_id": candidate_id, "district": district}


@pytest.mark.asyncio
async def test_district_match_found_directly(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {
            "results": [_candidate("SMITH, JANE", "H2CA01001", "01")]
        }
        result = await find_candidate(
            None, db_session, "Jane Smith", "CA", office="H", district="01"
        )
        assert result["candidate_id"] == "H2CA01001"
        mock_fetch.assert_called_once()  # no fallback needed

    # search resolved with the district in the URL
    assert "district=01" in mock_fetch.call_args.args[1]


@pytest.mark.asyncio
async def test_district_mismatch_falls_back_to_name_only(db_session):
    async def fake_fetch(client, url, retries=None):
        if "district=" in url:
            return {"results": []}  # district-constrained search: nothing
        return {"results": [_candidate("GREEN, CALEXANDER", "H4TX09095", "18")]}

    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = fake_fetch
        result = await find_candidate(
            None, db_session, "Cal Green", "TX", office="H", district="09"
        )
        assert result is not None
        assert result["candidate_id"] == "H4TX09095"
        assert mock_fetch.call_count == 2  # district attempt, then fallback


@pytest.mark.asyncio
async def test_fallback_requires_genuine_name_match(db_session):
    """Without a district to disambiguate, an unrelated same-surname
    candidate must NOT be accepted as a fallback match."""
    async def fake_fetch(client, url, retries=None):
        if "district=" in url:
            return {"results": []}
        return {"results": [_candidate("GREENE, MARK", "H0TX12147", "12")]}

    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = fake_fetch
        result = await find_candidate(
            None, db_session, "Cal Green", "TX", office="H", district="09"
        )
        assert result is None


@pytest.mark.asyncio
async def test_no_district_provided_no_fallback_attempted(db_session):
    """Senate searches never pass a district — nothing to fall back from."""
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {"results": []}
        result = await find_candidate(
            None, db_session, "Nobody Here", "ZZ", office="S"
        )
        assert result is None
        mock_fetch.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("name, state, fec_name, fec_id", [
    pytest.param("Delia Whitfield", "SC", "WHITFIELD, LOWELL O", "S0SC00149", id="fec_name_with_middle_initial"),
    pytest.param("Chuck Grantham", "IA", "GRANTHAM, BARBARA", "S0IA00099", id="fec_name_without_middle"),
])
async def test_primary_search_requires_genuine_name_match(db_session, name, state, fec_name, fec_id):
    """A same-surname/state/office candidate from the primary (non-district)
    search must NOT be accepted as a fallback match either — e.g. a newly
    appointed senator sharing a surname with a long-tenured incumbent must
    not get that incumbent's committee attributed to them. The middle-
    initial fallback (TestMiddleInitialFallback) must still reject a
    genuinely different first name."""
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {"results": [_candidate(fec_name, fec_id, "")]}
        result = await find_candidate(None, db_session, name, state, office="S")
        assert result is None


class TestBioguideCrosswalkTakesPriority:
    """2026-07: the congress-legislators bioguide->FEC crosswalk
    (congress_legislators.py) is tried FIRST when a bioguide_id is given —
    an authoritative ID match with no name-matching guesswork at all, and
    immune to the next nickname/legal-name mismatch nobody's added to the
    fallback table below yet (the concrete concern that prompted this:
    a hand-maintained nickname table can only ever cover patterns already
    seen)."""

    @pytest.mark.asyncio
    async def test_crosswalk_match_skips_name_search_entirely(self, db_session):
        # 2026-08-26: a verified crosswalk match still costs one cheap
        # existence lookup, but never the full name-search flow (which
        # would be several calls: a name query, possibly a no-district
        # retry).
        with patch(
            "app.pipeline.fetch.fec.fetch_bioguide_to_fec_ids",
            new=AsyncMock(return_value={"C001075": ["H8LA00017", "S4LA00107"]}),
        ), patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock,
            return_value={"results": [{"candidate_id": "S4LA00107"}]},
        ) as mock_fetch:
            result = await find_candidate(
                None, db_session, "Bill Cassell", "LA", office="S", bioguide_id="C001075",
            )
        assert result == {"candidate_id": "S4LA00107"}
        mock_fetch.assert_called_once()
        assert "S4LA00107" in mock_fetch.call_args.args[1]

    @pytest.mark.asyncio
    async def test_stale_crosswalk_id_falls_through_to_the_next_valid_one(self, db_session):
        # 2026-08-26 audit: three sitting members (Gillis/NY, Self/TX,
        # Ivey/MD) showed $0 raised because the crosswalk carried TWO
        # ids for the same office — one stale/invalid, one real — and
        # the unverified first match happened to be the invalid one in
        # all three cases. The first id here 404s on FEC; the second is
        # real and must be the one returned.
        async def fake_fetch(client, url, *a, **kw):
            if "H4NY04158" in url:
                return {"results": []}  # doesn't resolve — the stale one
            if "H2NY04244" in url:
                return {"results": [{"candidate_id": "H2NY04244"}]}  # the real one
            raise AssertionError(f"unexpected FEC lookup: {url}")

        with patch(
            "app.pipeline.fetch.fec.fetch_bioguide_to_fec_ids",
            new=AsyncMock(return_value={"G000598": ["H4NY04158", "H2NY04244"]}),
        ), patch(
            "app.pipeline.fetch.fec._fetch_with_retry", side_effect=fake_fetch,
        ):
            result = await find_candidate(
                None, db_session, "Laura Gillis", "NY", office="H", bioguide_id="G000598",
            )
        assert result == {"candidate_id": "H2NY04244"}

    @pytest.mark.asyncio
    async def test_all_crosswalk_ids_invalid_falls_back_to_name_search(self, db_session):
        with patch(
            "app.pipeline.fetch.fec.fetch_bioguide_to_fec_ids",
            new=AsyncMock(return_value={"C001075": ["S4LA00107"]}),
        ), patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = [
                {"results": []},  # the one crosswalk id doesn't resolve
                {"results": [_candidate("CASSELL, BILL", "S6LA00201", "")]},  # name search
            ]
            result = await find_candidate(
                None, db_session, "Bill Cassell", "LA", office="S", bioguide_id="C001075",
            )
        assert result["candidate_id"] == "S6LA00201"
        assert mock_fetch.call_count == 2

    @pytest.mark.asyncio
    async def test_member_missing_from_crosswalk_falls_back_to_name_search(self, db_session):
        with patch(
            "app.pipeline.fetch.fec.fetch_bioguide_to_fec_ids",
            new=AsyncMock(return_value={}),  # crosswalk fetched fine, just has no entry for this member
        ), patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = {"results": [_candidate("RASCH, JAMES E MR.", "S8ID00092", "")]}
            result = await find_candidate(
                None, db_session, "James E. Rasch", "ID", office="S", bioguide_id="Z999999",
            )
        assert result is not None
        assert result["candidate_id"] == "S8ID00092"
        mock_fetch.assert_called_once()  # crosswalk missed — fell back to name search

    @pytest.mark.asyncio
    async def test_crosswalk_entry_without_this_office_falls_back_to_name_search(self, db_session):
        # In the crosswalk, but only has a House id — looking them up for
        # a Senate seat must still fall through to name search rather
        # than returning the wrong-chamber id.
        with patch(
            "app.pipeline.fetch.fec.fetch_bioguide_to_fec_ids",
            new=AsyncMock(return_value={"R000584": ["H8ID00017"]}),
        ), patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = {"results": [_candidate("RASCH, JAMES E MR.", "S8ID00092", "")]}
            result = await find_candidate(
                None, db_session, "James E. Rasch", "ID", office="S", bioguide_id="R000584",
            )
        assert result["candidate_id"] == "S8ID00092"
        mock_fetch.assert_called_once()

    # No bioguide_id at all skips the crosswalk entirely: asserted in
    # TestMiddleInitialFallback.test_middle_initial_mismatch_does_not_block_match.


class TestMiddleInitialFallback:
    """2026-07 audit: 28 of 100 sitting senators had zero FEC donor data
    because FEC files under the legal name/format (nickname, no/spelled-
    out middle initial), which never satisfies the strict all-parts
    match above. The fallback resolves the middle-initial/punctuation
    class only ("James E. Rasch" vs FEC's "RASCH, JAMES E") — nickname
    resolution ("Bill" -> "CASSELL, WILLIAM M.") is deliberately NOT
    name-matched; it's the bioguide crosswalk's job (see
    TestBioguideCrosswalkTakesPriority above), since any hand-maintained
    alias table silently misses the next new nickname. The fallback must
    still reject a genuinely different first name sharing a surname
    (Delia vs. Lowell Whitfield — covered by
    test_primary_search_requires_genuine_name_match above)."""

    @pytest.mark.asyncio
    async def test_nickname_without_crosswalk_is_not_guessed(self, db_session):
        # "Bill" vs FEC's "WILLIAM" with no bioguide crosswalk entry:
        # correctly returns nothing rather than guessing the alias —
        # every sitting member resolves via the crosswalk instead.
        with patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = {
                "results": [_candidate("CASSELL, WILLIAM M.", "S4LA00107", "")]
            }
            result = await find_candidate(None, db_session, "Bill Cassell", "LA", office="S")
        assert result is None

    @pytest.mark.asyncio
    async def test_middle_initial_mismatch_does_not_block_match(self, db_session):
        with patch(
            "app.pipeline.fetch.fec.fetch_bioguide_to_fec_ids", new_callable=AsyncMock
        ) as mock_crosswalk, patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = {
                "results": [_candidate("RASCH, JAMES E MR.", "S8ID00092", "")]
            }
            result = await find_candidate(None, db_session, "James E. Rasch", "ID", office="S")
        assert result is not None
        assert result["candidate_id"] == "S8ID00092"
        mock_crosswalk.assert_not_called()  # no bioguide_id: the crosswalk is skipped entirely

    @pytest.mark.asyncio
    async def test_disambiguates_among_multiple_same_surname_results(self, db_session):
        # Two real, different Warners on file for VA Senate across eras —
        # must pick the one whose first name actually matches, not just
        # the first result.
        with patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = {
                "results": [
                    _candidate("WARDELL, JOHN WILLIAM", "S8VA00107", ""),
                    _candidate("WARDELL, MARK ROBERT", "S6VA00093", ""),
                ]
            }
            result = await find_candidate(None, db_session, "Mark R. Wardell", "VA", office="S")
        assert result is not None
        assert result["candidate_id"] == "S6VA00093"


@pytest.mark.asyncio
async def test_both_searches_empty_returns_none(db_session):
    with patch(
        "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {"results": []}
        result = await find_candidate(
            None, db_session, "Ghost Person", "TX", office="H", district="09"
        )
        assert result is None
        assert mock_fetch.call_count == 2  # district attempt, then fallback


class TestCandidateProfileCaching:
    """Only a resolved id is cached — a miss can't be told apart from
    _fetch_with_retry exhausting its own retries on a transient FEC outage,
    and caching that would blacklist a genuinely valid id over a one-time
    network blip."""

    @pytest.mark.asyncio
    async def test_a_real_candidate_is_cached_with_its_latest_election(self, db_session):
        with patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock,
            return_value={"results": [{"candidate_id": "S4LA00107", "election_years": [2014, 2020, 2026]}]},
        ) as mock_fetch:
            assert await _candidate_latest_election(None, db_session, "S4LA00107") == 2026
            assert await _candidate_latest_election(None, db_session, "S4LA00107") == 2026
        mock_fetch.assert_called_once()  # second call served from cache
        assert api_cache_get(db_session, "fec", "candidate-profile-S4LA00107") == {"latest_election": 2026}

    @pytest.mark.asyncio
    async def test_a_nonexistent_candidate_is_not_cached(self, db_session):
        with patch(
            "app.pipeline.fetch.fec._fetch_with_retry", new_callable=AsyncMock,
            return_value={"results": []},
        ) as mock_fetch:
            assert await _candidate_latest_election(None, db_session, "H4NY04158") is None
            assert await _candidate_latest_election(None, db_session, "H4NY04158") is None
        assert mock_fetch.call_count == 2  # re-checked both times, nothing cached
        assert api_cache_get(db_session, "fec", "candidate-profile-H4NY04158") is None


@pytest.mark.asyncio
async def test_the_current_campaigns_id_wins_over_an_older_valid_one(db_session):
    """John McGuinn (VA-5): the crosswalk lists his 2022 VA-7 id first; both
    resolve on FEC. The 2024/2026 id is the seat he holds."""
    async def fake_fetch(client, url, *a, **kw):
        if "H2VA07196" in url:
            return {"results": [{"candidate_id": "H2VA07196", "election_years": [2022]}]}
        if "H0VA07133" in url:
            return {"results": [{"candidate_id": "H0VA07133", "election_years": [2024, 2026]}]}
        raise AssertionError(f"unexpected FEC lookup: {url}")

    with patch(
        "app.pipeline.fetch.fec.fetch_bioguide_to_fec_ids",
        new=AsyncMock(return_value={"M001239": ["H2VA07196", "H0VA07133"]}),
    ), patch("app.pipeline.fetch.fec._fetch_with_retry", side_effect=fake_fetch):
        result = await find_candidate(
            None, db_session, "John McGuinn", "VA", office="H", district="05", bioguide_id="M001239",
        )
    assert result == {"candidate_id": "H0VA07133"}
