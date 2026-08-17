"""Tests for the generic confirmed-candidate sync/match orchestration
(state_candidates.py) — fetch-per-strategy dispatch and the matching-
safety rules (never guess, never hide, never fabricate).
"""

from unittest.mock import AsyncMock

import pytest

from app.models import Candidate, Race
from app.pipeline.fetch import state_candidates as sc
from app.pipeline.fetch.state_candidate_sources import configured_states


def _race(db, race_id, state, office="S", district=None, cycle_year=2026):
    r = Race(id=race_id, cycle_year=cycle_year, office=office, state=state, district=district)
    db.add(r)
    return r


def _candidate(db, cand_id, race_id, name, party="REP", **overrides):
    c = Candidate(id=cand_id, race_id=race_id, name=name, party=party, **overrides)
    db.add(c)
    return c


class TestIsConfigured:
    def test_true_for_a_registered_state_with_a_real_strategy(self):
        assert sc.is_configured("TX") is True

    def test_true_for_every_registered_state(self):
        """Each entry must name a strategy that actually exists — a typo'd
        key is a config bug that would silently drop that state."""
        for state in configured_states():
            assert sc.is_configured(state) is True, state

    def test_false_for_an_unregistered_state(self):
        assert sc.is_configured("ZZ") is False


class TestMultiWordSurname:
    """A state publishes a display name, so all that can be taken from
    "Debbie Wasserman Schultz" without guessing is the trailing token —
    while FEC keeps the whole surname before the comma. Every state is
    affected; Florida's real 2024 file is just where it surfaced."""

    def test_matches_a_two_word_fec_surname(self):
        candidates = [
            Candidate(id="1", race_id="r", name="WASSERMAN SCHULTZ, DEBBIE", party="DEM"),
        ]
        assert sc._match_candidate(candidates, "Schultz", "D").id == "1"

    def test_still_prefers_an_exact_surname_over_the_fallback(self):
        """An exact match must win outright — the fallback exists for the
        candidate the exact pass cannot see, and must never pull a
        different person in ahead of a real match."""
        candidates = [
            Candidate(id="1", race_id="r", name="SCHULTZ, BOB", party="REP"),
            Candidate(id="2", race_id="r", name="WASSERMAN SCHULTZ, DEBBIE", party="DEM"),
        ]
        assert sc._match_candidate(candidates, "Schultz", "R").id == "1"

    def test_two_candidates_ending_in_the_same_token_stay_ambiguous(self):
        """The never-guess rule still governs: two same-party candidates
        the fallback can't tell apart yield nobody."""
        candidates = [
            Candidate(id="1", race_id="r", name="WASSERMAN SCHULTZ, DEBBIE", party="DEM"),
            Candidate(id="2", race_id="r", name="VAN SCHULTZ, ANA", party="DEM"),
        ]
        assert sc._match_candidate(candidates, "Schultz", "D") is None


class TestMatchCandidate:
    def test_matches_a_unique_surname(self):
        candidates = [Candidate(id="1", race_id="r", name="PAXTON, KEN", party="REP")]
        match = sc._match_candidate(candidates, "PAXTON", "R")
        assert match.id == "1"

    def test_returns_none_when_no_candidate_shares_the_surname(self):
        candidates = [Candidate(id="1", race_id="r", name="TALARICO, JAMES", party="DEM")]
        assert sc._match_candidate(candidates, "PAXTON", "R") is None

    def test_disambiguates_same_surname_by_party(self):
        candidates = [
            Candidate(id="1", race_id="r", name="SMITH, JANE", party="DEM"),
            Candidate(id="2", race_id="r", name="SMITH, BOB", party="REP"),
        ]
        match = sc._match_candidate(candidates, "SMITH", "R")
        assert match.id == "2"

    def test_returns_none_when_same_surname_and_party_both_ambiguous(self):
        """Never guesses between two same-surname, same-party candidates —
        an FEC record this can't safely tell apart stays unconfirmed
        rather than risk flagging the wrong one."""
        candidates = [
            Candidate(id="1", race_id="r", name="SMITH, JANE", party="REP"),
            Candidate(id="2", race_id="r", name="SMITH, BOB", party="REP"),
        ]
        assert sc._match_candidate(candidates, "SMITH", "R") is None


class TestSyncConfirmedCandidates:
    @pytest.fixture(autouse=True)
    def _only_texas(self, monkeypatch):
        """Scope the sync loop to the one state each test mocks. Without
        this, every other registered state runs its real strategy against
        the live Secretary-of-State endpoint — turning this file into a
        slow, flaky, network-dependent suite the moment a state is added."""
        monkeypatch.setattr(sc, "configured_states", lambda: {"TX"})

    @pytest.mark.asyncio
    async def test_flags_a_matched_candidate(self, db_session, monkeypatch):
        _race(db_session, "2026-SEN-TX", "TX", office="S")
        _candidate(db_session, "C1", "2026-SEN-TX", "PAXTON, KEN", party="REP")
        db_session.commit()

        mock_fetch = AsyncMock(return_value=[
            {"office": "S", "district": None, "party": "R", "last_name": "PAXTON"},
        ])
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", mock_fetch)
        results = await sc.sync_confirmed_candidates(db_session, None, 2026)

        cand = db_session.query(Candidate).filter(Candidate.id == "C1").first()
        assert cand.confirmed_general is True
        assert results["TX"] == {"confirmed": 1, "unmatched": 0, "status": "ok"}

    @pytest.mark.asyncio
    async def test_never_hides_a_candidate_that_fails_to_match(self, db_session, monkeypatch):
        """An unmatched record just doesn't confirm anyone — the existing
        FEC candidate list for that race is untouched, not filtered down
        to zero."""
        _race(db_session, "2026-SEN-TX", "TX", office="S")
        _candidate(db_session, "C1", "2026-SEN-TX", "SOMEONE, ELSE", party="REP")
        db_session.commit()

        mock_fetch = AsyncMock(return_value=[
            {"office": "S", "district": None, "party": "R", "last_name": "PAXTON"},
        ])
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", mock_fetch)
        results = await sc.sync_confirmed_candidates(db_session, None, 2026)

        cand = db_session.query(Candidate).filter(Candidate.id == "C1").first()
        assert cand.confirmed_general is False
        assert results["TX"] == {"confirmed": 0, "unmatched": 1, "status": "ok"}

    @pytest.mark.asyncio
    async def test_unmatched_when_no_race_exists_for_the_record(self, db_session, monkeypatch):
        mock_fetch = AsyncMock(return_value=[
            {"office": "H", "district": 99, "party": "R", "last_name": "NOBODY"},
        ])
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", mock_fetch)
        results = await sc.sync_confirmed_candidates(db_session, None, 2026)

        assert results["TX"] == {"confirmed": 0, "unmatched": 1, "status": "ok"}

    @pytest.mark.asyncio
    async def test_fetch_failure_reports_status_without_raising(self, db_session, monkeypatch):
        mock_fetch = AsyncMock(return_value=None)
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", mock_fetch)
        results = await sc.sync_confirmed_candidates(db_session, None, 2026)

        assert results["TX"]["status"] == "fetch_failed"

    @pytest.mark.asyncio
    async def test_fetch_exception_reports_failed_status_not_raise(self, db_session, monkeypatch):
        mock_fetch = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", mock_fetch)
        results = await sc.sync_confirmed_candidates(db_session, None, 2026)

        assert results["TX"]["status"] == "fetch_failed"
