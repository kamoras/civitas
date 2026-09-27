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


async def _ok(*_args, **_kwargs):
    """A source that fetches fine and simply has nothing to report."""
    return []


def _candidate(db, cand_id, race_id, name, party="REP", **overrides):
    c = Candidate(id=cand_id, race_id=race_id, name=name, party=party, **overrides)
    db.add(c)
    return c


class TestCrawlAdoption:
    """The one path that can add a state with nobody reading it first, so
    the bar is positive proof: the nominees a discovered source names must
    be candidates on file for those races."""

    @staticmethod
    def _patch(monkeypatch, records, found=None):
        saved = {}

        async def fake_discover(client, state, cycle, rules=None):
            return found if found is not None else {
                "strategy": "tabular", "_evidence": "a results file somewhere",
            }

        async def fake_fetch(client, cycle, state, source):
            return records

        async def no_filings(client, state, cycle):
            return None

        async def no_calendar(client, cycle):
            return {}

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "discover_source", fake_discover)
        monkeypatch.setattr(sc, "discover_filings", no_filings)
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"ZZ": ["example.gov"]})
        monkeypatch.setattr(sc, "STRATEGIES", {"tabular": fake_fetch})
        monkeypatch.setattr(sc, "save_discovered", lambda st, src: saved.update({st: src}))
        return saved

    @pytest.mark.asyncio
    async def test_adopts_a_source_whose_nominees_are_real_candidates(
        self, db_session, monkeypatch,
    ):
        _race(db_session, "2026-HOUSE-ZZ-3", "ZZ", "H", 3)
        _candidate(db_session, "c1", "2026-HOUSE-ZZ-3", "FLOOD, MIKE", party="REP")
        db_session.commit()
        saved = self._patch(
            monkeypatch,
            [{"office": "H", "district": 3, "party": "R", "last_name": "Flood"}],
        )
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"].startswith("adopted")
        assert "ZZ" in saved

    @pytest.mark.asyncio
    async def test_rejects_a_source_naming_people_who_are_not_in_the_race(
        self, db_session, monkeypatch,
    ):
        """What a wrong column looks like: a file that parses beautifully
        into names no filer in that race shares."""
        _race(db_session, "2026-HOUSE-ZZ-3", "ZZ", "H", 3)
        _candidate(db_session, "c1", "2026-HOUSE-ZZ-3", "FLOOD, MIKE", party="REP")
        db_session.commit()
        saved = self._patch(
            monkeypatch,
            [{"office": "H", "district": 3, "party": "R", "last_name": "Nobody"}],
        )
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"] == "rejected"
        assert saved == {}

    @pytest.mark.asyncio
    async def test_a_source_claiming_nothing_yet_is_not_adopted_on_faith(
        self, db_session, monkeypatch,
    ):
        """A state still counting claims no nominees, so nothing can be
        proved — and a first version adopted exactly such files (a
        candidate filing list, a headerless export) because nothing could
        contradict them. It waits for the next crawl instead."""
        saved = self._patch(monkeypatch, [])
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"] == "unproven"
        assert saved == {}

    @pytest.mark.asyncio
    async def test_a_working_hand_verified_states_source_is_left_alone(
        self, db_session, monkeypatch,
    ):
        """Its results source is never replaced while it works — though the
        state is still checked for a candidate filing list, which answers a
        different question."""
        saved = self._patch(monkeypatch, [])
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"TX": ["sos.texas.gov"]})
        monkeypatch.setattr(sc, "STRATEGIES", {"tx_civix": _ok})
        monkeypatch.setattr(sc, "_refresh_dates", _ok)
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes.get("TX", "none") == "none"
        assert saved == {}

    @pytest.mark.asyncio
    async def test_a_working_google_civic_hand_verified_state_still_gets_crawled(
        self, db_session, monkeypatch,
    ):
        """google_civic is a national fallback, not a real per-district
        vendor — unlike every other hand-verified strategy, it must
        never shadow discover_source the way a real working source
        rightly does, or these states' only path to a genuine vendor
        being found is permanently blocked for the rest of the cycle.
        Uses MI's real state_candidate_sources.json entry (strategy:
        "google_civic") — no synthetic hand-verified entry needed."""
        _race(db_session, "2026-HOUSE-MI-3", "MI", "H", 3)
        _candidate(db_session, "c1", "2026-HOUSE-MI-3", "FLOOD, MIKE", party="REP")
        db_session.commit()
        saved = self._patch(
            monkeypatch,
            [{"office": "H", "district": 3, "party": "R", "last_name": "Flood"}],
        )
        sc.STRATEGIES["google_civic"] = _ok
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"MI": ["michigan.gov"]})
        monkeypatch.setattr(sc, "_refresh_dates", _ok)
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["MI"].startswith("adopted")
        assert "MI" in saved

    @pytest.mark.asyncio
    async def test_a_BROKEN_hand_verified_state_is_crawled_for_a_replacement(
        self, db_session, monkeypatch,
    ):
        """A state that moves hosts between cycles is the whole reason
        locations aren't trusted to stay put — so when a hand-written one
        stops fetching, a replacement is looked for instead of the state
        going dark until someone edits a URL. Its LAW still comes from the
        hand-written entry."""
        seen_rules = {}

        async def fake_discover(client, state, cycle, rules=None):
            seen_rules.update(rules or {})
            return None

        async def broken(client, cycle, state, source):
            return None

        async def no_filings(client, state, cycle):
            return None

        async def no_calendar(client, cycle):
            return {}

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        async def no_calendar(client, cycle):
            return {}

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "discover_source", fake_discover)
        monkeypatch.setattr(sc, "discover_filings", no_filings)
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"GA": ["sos.ga.gov"]})
        monkeypatch.setattr(sc, "STRATEGIES", {"tabular": broken})
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert "GA" in outcomes
        # Georgia nominates on a majority — that rule is law, and must be
        # carried into whatever replacement gets found.
        assert seen_rules["runoff_threshold_pct"] == 50.0


class TestForgetsBrokenDiscoveries:
    """The other half of self-healing: finding a state's new location only
    helps if the dead one goes away."""

    @pytest.mark.asyncio
    async def test_a_discovered_source_that_stopped_fetching_is_forgotten(
        self, db_session, monkeypatch,
    ):
        saved = {"ZZ": {"strategy": "tabular"}}

        async def nothing_found(client, state, cycle, rules=None):
            return None

        async def broken(client, cycle, state, source):
            return None

        async def no_filings(client, state, cycle):
            return None

        async def no_calendar(client, cycle):
            return {}

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "discover_source", nothing_found)
        monkeypatch.setattr(sc, "discover_filings", no_filings)
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"ZZ": ["example.gov"]})
        monkeypatch.setattr(sc, "STRATEGIES", {"tabular": broken})
        monkeypatch.setattr(sc, "discovered_states", lambda: {"ZZ"})
        monkeypatch.setattr(sc, "source_for_state", lambda st: saved.get(st))
        monkeypatch.setattr(sc, "save_discovered", lambda st, src: saved.pop(st))
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"] == "forgotten"
        assert saved == {}

    @pytest.mark.asyncio
    async def test_one_that_still_fetches_survives_a_crawl_that_missed_it(
        self, db_session, monkeypatch,
    ):
        """A page can be down for an hour; that is not a reason to drop a
        working source."""
        saved = {"ZZ": {"strategy": "tabular"}}

        async def nothing_found(client, state, cycle, rules=None):
            return None

        async def working(client, cycle, state, source):
            return []

        async def no_filings(client, state, cycle):
            return None

        async def no_calendar(client, cycle):
            return {}

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "discover_source", nothing_found)
        monkeypatch.setattr(sc, "discover_filings", no_filings)
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"ZZ": ["example.gov"]})
        monkeypatch.setattr(sc, "STRATEGIES", {"tabular": working})
        monkeypatch.setattr(sc, "discovered_states", lambda: {"ZZ"})
        monkeypatch.setattr(sc, "source_for_state", lambda st: saved.get(st))
        monkeypatch.setattr(sc, "save_discovered", lambda st, src: saved.pop(st))
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"] == "kept"
        assert "ZZ" in saved


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


class TestSameSurnameSameParty:
    """Alaska's real 2026 Senate top-four advances TWO Sullivans --
    "Sullivan, Dan S." (the sitting senator, 68,726 votes) and
    "Sullivan, Daniel J. Jr." (4,107) -- against FEC's own "SULLIVAN,
    DAN" and "SULLIVAN, DANIEL J". Surname plus party cannot separate
    them, so both went unmatched and the state's marquee race published
    two Democrats with its Republican incumbent absent.

    The given name is a LAST resort, reached only where the answer would
    otherwise be None. It can turn a refusal into a match; it can never
    change one the surname already resolved, and it still refuses when
    the given name is ambiguous too."""

    AK = [
        Candidate(id="1", race_id="r", name="SULLIVAN, DAN", party="REP"),
        Candidate(id="2", race_id="r", name="SULLIVAN, DANIEL J", party="REP"),
    ]

    def test_the_sitting_senator_is_matched_by_given_name(self):
        match = sc._match_candidate(self.AK, "Sullivan", "R", "Sullivan, Dan S.")
        assert match.id == "1"

    def test_the_other_sullivan_is_matched_too(self):
        """His own Party_Code is blank in Alaska's file, so party can't
        even narrow the pool -- the given name has to carry it alone."""
        match = sc._match_candidate(self.AK, "Sullivan", None, "Sullivan, Daniel J. Jr.")
        assert match.id == "2"

    def test_an_adapter_that_sends_no_display_name_is_unchanged(self):
        """Every non-tabular adapter still omits it, and must behave
        exactly as before."""
        assert sc._match_candidate(self.AK, "Sullivan", "R") is None

    def test_an_unrecognised_given_name_still_refuses(self):
        assert sc._match_candidate(self.AK, "Sullivan", "R", "Sullivan, Mortimer Q.") is None

    def test_a_unique_surname_is_never_overridden_by_a_mismatched_given_name(self):
        """The rule is additive: a surname that already resolves uniquely
        is returned without the given name being consulted at all, so a
        state's nickname ("Chuck" for CHARLES) can't undo a good match."""
        candidates = [Candidate(id="1", race_id="r", name="KOPP, CHARLES M.", party="REP")]
        match = sc._match_candidate(candidates, "Kopp", "R", 'Kopp, Chuck')
        assert match.id == "1"


class TestSyncConfirmedCandidates:
    @pytest.fixture(autouse=True)
    def _no_calendar(self, monkeypatch):
        """The nightly sync refreshes the national calendar first; these
        tests are about matching, not about the FEC."""
        async def none(client, cycle):
            return {}

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", none)

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
        assert results["TX"] == {"confirmed": 1, "unmatched": 0, "statewide": 0, "stateLeg": 0,
                                 "judicial": 0, "status": "ok",
                                 "ballotOnly": 0, "unconfirmed": 0}

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
        assert results["TX"] == {"confirmed": 0, "unmatched": 1, "statewide": 0, "stateLeg": 0,
                                 "judicial": 0, "status": "ok",
                                 "ballotOnly": 0, "unconfirmed": 0}

    @pytest.mark.asyncio
    async def test_unmatched_when_no_race_exists_for_the_record(self, db_session, monkeypatch):
        mock_fetch = AsyncMock(return_value=[
            {"office": "H", "district": 99, "party": "R", "last_name": "NOBODY"},
        ])
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", mock_fetch)
        results = await sc.sync_confirmed_candidates(db_session, None, 2026)

        assert results["TX"] == {"confirmed": 0, "unmatched": 1, "statewide": 0, "stateLeg": 0,
                                 "judicial": 0, "status": "ok",
                                 "ballotOnly": 0, "unconfirmed": 0}

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
