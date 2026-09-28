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
            return {}, False

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
    async def test_a_google_civic_states_filing_list_is_looked_for_once(self, db_session, monkeypatch):
        """Its crawl goes on to discovery; finding nothing there must not
        look for the same filing list a second time."""
        async def nothing_found(client, state, cycle, rules=None):
            return None

        looked = []

        async def filings(client, state, cycle):
            looked.append(state)
            return None

        self._patch(monkeypatch, [])
        sc.STRATEGIES["google_civic"] = _ok
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"MI": ["michigan.gov"]})
        monkeypatch.setattr(sc, "_refresh_dates", _ok)
        monkeypatch.setattr(sc, "discover_source", nothing_found)
        monkeypatch.setattr(sc, "discover_filings", filings)
        await sc.crawl_for_new_sources(db_session, None, 2026)
        assert looked == ["MI"]

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
            return {}, False

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        async def no_calendar(client, cycle):
            return {}, False

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
    helps if the dead one goes away — but a failed fetch is as likely an
    outage as a move."""

    @staticmethod
    def _patch(monkeypatch, saved, fetch):
        async def nothing_found(client, state, cycle, rules=None):
            return None

        async def no_filings(client, state, cycle):
            return None

        forgotten = []
        monkeypatch.setattr(sc, "discover_source", nothing_found)
        monkeypatch.setattr(sc, "discover_filings", no_filings)
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"ZZ": ["example.gov"]})
        monkeypatch.setattr(sc, "STRATEGIES", {"tabular": fetch})
        monkeypatch.setattr(sc, "discovered_states", lambda: {"ZZ"})
        monkeypatch.setattr(sc, "_discovered_source", lambda st: saved.get(st))
        monkeypatch.setattr(sc, "forget_results_source", lambda st: forgotten.append(st))
        return forgotten

    @staticmethod
    async def _crawl_at(db, monkeypatch, when):
        from datetime import datetime

        monkeypatch.setattr(sc, "utcnow", lambda: datetime.fromisoformat(when))
        return await sc.crawl_for_new_sources(db, None, 2026)

    @pytest.mark.asyncio
    async def test_a_source_is_forgotten_only_after_failing_for_two_weeks(
        self, db_session, monkeypatch,
    ):
        """It used to go on the first failed fetch — an hour's outage on
        crawl night left the state dark until a later crawl re-proved it."""
        saved = {"ZZ": {"strategy": "tabular", "filings": {"url": "x"}}}

        async def broken(client, cycle, state, source):
            return None

        forgotten = self._patch(monkeypatch, saved, broken)
        first = await self._crawl_at(db_session, monkeypatch, "2026-09-01T03:00:00")
        assert first["ZZ"] == "failing since 2026-09-01" and forgotten == []
        assert await self._crawl_at(db_session, monkeypatch, "2026-09-05T03:00:00") == {}  # not due
        second = await self._crawl_at(db_session, monkeypatch, "2026-09-08T03:00:00")
        assert second["ZZ"] == "failing since 2026-09-01" and forgotten == []
        third = await self._crawl_at(db_session, monkeypatch, "2026-09-15T03:00:00")
        assert third["ZZ"] == "forgotten" and forgotten == ["ZZ"]

    @pytest.mark.asyncio
    async def test_a_source_that_fetches_again_starts_its_clock_over(self, db_session, monkeypatch):
        saved = {"ZZ": {"strategy": "tabular"}}
        works = {"now": False}

        async def flaky(client, cycle, state, source):
            return [] if works["now"] else None

        forgotten = self._patch(monkeypatch, saved, flaky)
        await self._crawl_at(db_session, monkeypatch, "2026-09-01T03:00:00")
        works["now"] = True
        assert (await self._crawl_at(db_session, monkeypatch, "2026-09-08T03:00:00"))["ZZ"] == "kept"
        works["now"] = False
        later = await self._crawl_at(db_session, monkeypatch, "2026-09-15T03:00:00")
        assert later["ZZ"] == "failing since 2026-09-15" and forgotten == []

    @pytest.mark.asyncio
    async def test_one_that_still_fetches_survives_a_crawl_that_missed_it(
        self, db_session, monkeypatch,
    ):
        """A page can be down for an hour; that is not a reason to drop a
        working source."""
        saved = {"ZZ": {"strategy": "tabular"}}

        async def working(client, cycle, state, source):
            return []

        forgotten = self._patch(monkeypatch, saved, working)
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"] == "kept" and forgotten == []

    @pytest.mark.asyncio
    async def test_a_filing_list_alone_is_not_a_broken_results_source(self, db_session, monkeypatch):
        """An entry with a filing list and no results source was forgotten
        on every crawl, because it had nothing to fetch. It is kept — and
        its filing list is still looked for each week, so one that moves
        is followed."""
        saved = {"ZZ": {"filings": {"url": "x"}, "source_name": "filings"}}

        async def never_called(client, cycle, state, source):
            raise AssertionError("no results source to test")

        forgotten = self._patch(monkeypatch, saved, never_called)
        looked = []

        async def filings(client, state, cycle):
            looked.append(state)
            return None

        monkeypatch.setattr(sc, "discover_filings", filings)
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"] == "filings only" and forgotten == [] and looked == ["ZZ"]


class TestForgettingKeepsTheFilingList:
    def test_only_the_results_source_goes(self, tmp_path, monkeypatch):
        import json

        from app.pipeline.fetch import state_candidate_sources as sources

        path = tmp_path / "discovered.json"
        path.write_text(json.dumps({
            "ZZ": {"strategy": "tabular", "source_name": "a file", "filings": {"url": "x"}},
            "YY": {"strategy": "tabular"},
        }))
        monkeypatch.setattr(sources, "_DISCOVERED_PATH", str(path))
        monkeypatch.setattr(sources, "_discovered_cache", None)
        sources.forget_results_source("ZZ")
        sources.forget_results_source("YY")
        assert json.loads(path.read_text()) == {"ZZ": {"filings": {"url": "x"}}}


class TestCrawlFailuresAreContained:
    """One state's crawl raising used to end the whole sweep — and, since
    it ran unguarded inside the confirmed-candidate phase, that night's
    sync too. And being weekly with no memory, the states after it were
    starved every week."""

    @staticmethod
    def _patch(monkeypatch, discover):
        async def no_filings(client, state, cycle):
            return None

        monkeypatch.setattr(sc, "discover_source", discover)
        monkeypatch.setattr(sc, "discover_filings", no_filings)
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"AA": ["a.gov"], "BB": ["b.gov"]})
        monkeypatch.setattr(sc, "discovered_states", lambda: set())
        alerts = []
        monkeypatch.setattr(sc, "report_file_problems",
                            lambda subject, lead, problems, key: alerts.extend(problems))
        return alerts

    @pytest.mark.asyncio
    async def test_a_state_whose_crawl_raises_is_alone_in_failing_and_is_retried_next_night(
        self, db_session, monkeypatch,
    ):
        from datetime import datetime

        calls = []

        async def discover(client, state, cycle, rules=None):
            return None

        real = sc._crawl_results_source

        async def results(db, client, cycle, state, *args):
            calls.append(state)
            if state == "AA":
                raise RuntimeError("a bug past every guard")
            return await real(db, client, cycle, state, *args)

        alerts = self._patch(monkeypatch, discover)
        monkeypatch.setattr(sc, "_crawl_results_source", results)
        monkeypatch.setattr(sc, "utcnow", lambda: datetime(2026, 9, 1, 3))
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes == {"AA": "error", "BB": "none"}
        assert len(alerts) == 1 and alerts[0].startswith("AA")
        monkeypatch.setattr(sc, "utcnow", lambda: datetime(2026, 9, 2, 3))
        calls.clear()
        await sc.crawl_for_new_sources(db_session, None, 2026)
        assert calls == ["AA"]  # BB completed and isn't due for a week; AA is retried

    @pytest.mark.asyncio
    async def test_a_raise_inside_a_step_is_contained_and_still_alerted(self, db_session, monkeypatch):
        """Discovery raising (a portal's JSON in a shape nobody checked) is
        treated as finding nothing, so the state still gets its forget check
        and filing-list search — but it is reported, since a bug in an
        adapter otherwise looks exactly like an outage."""
        async def discover(client, state, cycle, rules=None):
            if state == "AA":
                raise AttributeError("'list' object has no attribute 'get'")
            return None

        looked = []

        async def filings(client, state, cycle):
            looked.append(state)
            return None

        alerts = self._patch(monkeypatch, discover)
        monkeypatch.setattr(sc, "discover_filings", filings)
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes == {"AA": "none", "BB": "none"} and looked == ["AA", "BB"]
        assert alerts == ["AA: Source discovery raised AttributeError: 'list' object has no attribute 'get'"]

    @pytest.mark.asyncio
    async def test_a_find_that_cannot_be_saved_is_not_reported_as_adopted(
        self, db_session, monkeypatch,
    ):
        from app.atomic_write import NotSaved

        _race(db_session, "2026-HOUSE-AA-3", "AA", "H", 3)
        _candidate(db_session, "c1", "2026-HOUSE-AA-3", "FLOOD, MIKE", party="REP")
        db_session.commit()

        async def discover(client, state, cycle, rules=None):
            return {"strategy": "tabular", "_evidence": "a file"} if state == "AA" else None

        async def fetch(client, cycle, state, source):
            return [{"office": "H", "district": 3, "party": "R", "last_name": "Flood"}]

        def cannot_save(state, source):
            raise NotSaved("disk full")

        alerts = self._patch(monkeypatch, discover)
        monkeypatch.setattr(sc, "STRATEGIES", {"tabular": fetch})
        monkeypatch.setattr(sc, "save_discovered", cannot_save)
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["AA"] == "save failed" and alerts == ["AA: disk full"]


class TestARaisingSourceIsNotFetching:
    """A source that breaks by raising, rather than returning nothing, got
    none of the handling a non-fetching one gets — and in the sync ended
    the pass over every state after it."""

    @pytest.mark.asyncio
    async def test_a_spare_that_raises_does_not_end_the_sync(self, db_session, monkeypatch, tmp_path):
        import json
        from unittest.mock import AsyncMock

        from app.pipeline.fetch import state_candidate_sources as sources

        async def no_calendar(client, cycle):
            return {}, False

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "configured_states", lambda: {"TX", "WY"})
        path = tmp_path / "d.json"
        path.write_text(json.dumps({"TX": {"strategy": "tabular", "source_name": "x"}}))
        monkeypatch.setattr(sources, "_DISCOVERED_PATH", str(path))
        monkeypatch.setattr(sources, "_discovered_cache", None)
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", AsyncMock(return_value=None))
        monkeypatch.setitem(sc.STRATEGIES, "tabular", AsyncMock(side_effect=ValueError("not a spreadsheet")))
        wy = sources.source_for_state("WY")
        monkeypatch.setitem(sc.STRATEGIES, wy["strategy"], AsyncMock(return_value=[]))
        if wy.get("general_list"):
            monkeypatch.setitem(sc.STRATEGIES, wy["general_list"]["strategy"], AsyncMock(return_value=[]))
        results = await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert results["TX"]["status"] == "fetch_failed" and results["WY"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_in_the_crawl_it_takes_the_not_fetching_paths(self, db_session, monkeypatch):
        """A discovered source that raises enters the failing/forget clock,
        and the state is still searched for a filing list."""
        async def raises(client, cycle, state, source):
            raise ValueError("html where a spreadsheet was")

        saved = {"ZZ": {"strategy": "tabular"}}
        forgotten = TestForgetsBrokenDiscoveries._patch(monkeypatch, saved, raises)
        looked = []

        async def filings(client, state, cycle):
            looked.append(state)
            return None

        monkeypatch.setattr(sc, "discover_filings", filings)
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"].startswith("failing since") and looked == ["ZZ"] and forgotten == []

    @pytest.mark.asyncio
    async def test_a_hand_verified_source_that_raises_is_crawled_for_a_replacement(
        self, db_session, monkeypatch,
    ):
        searched = []

        async def discover(client, state, cycle, rules=None):
            searched.append(state)
            return None

        async def raises(client, cycle, state, source):
            raise ValueError("html where a spreadsheet was")

        async def no_filings(client, state, cycle):
            return None

        monkeypatch.setattr(sc, "discover_source", discover)
        monkeypatch.setattr(sc, "discover_filings", no_filings)
        monkeypatch.setattr(sc, "ELECTION_DOMAINS", {"TX": ["sos.texas.gov"]})
        monkeypatch.setattr(sc, "STRATEGIES", {"tx_civix": raises})
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert searched == ["TX"] and outcomes["TX"] != "error"


class TestAdoptingAFilingListThatWontParse:
    @pytest.mark.asyncio
    async def test_a_read_that_raises_adopts_nothing_and_is_reported(self, db_session, monkeypatch):
        import csv

        async def filings(client, state, cycle):
            return {"url": "x", "_evidence": "a list"}

        async def unreadable(client, year, state, source):
            raise csv.Error("field larger than field limit")

        raised = []
        monkeypatch.setattr(sc, "discover_filings", filings)
        monkeypatch.setattr(sc, "fetch_ballot_candidates", unreadable)
        token = sc._RAISED.set(raised)
        try:
            assert await sc._adopt_filings(db_session, None, 2026, "ZZ", {}) == "none"
        finally:
            sc._RAISED.reset(token)
        assert raised == ["ZZ: Filing-list read raised Error: field larger than field limit"]

    @pytest.mark.asyncio
    async def test_the_list_already_on_file_is_left_to_the_syncs_report(self, db_session, monkeypatch):
        """The nightly sync reads the same list and reports it; the crawl
        reporting it too was two alerts for one fault."""
        async def filings(client, state, cycle):
            return {"url": "x", "_evidence": "a list"}

        async def unreadable(client, year, state, source):
            raise ValueError("unreadable")

        raised = []
        monkeypatch.setattr(sc, "discover_filings", filings)
        monkeypatch.setattr(sc, "fetch_ballot_candidates", unreadable)
        monkeypatch.setattr(sc, "filings_for_state", lambda st: {"url": "x"})
        token = sc._RAISED.set(raised)
        try:
            assert await sc._adopt_filings(db_session, None, 2026, "ZZ", {}) == "none"
        finally:
            sc._RAISED.reset(token)
        assert raised == []


class TestAWeakerSourceNeverPrunesTheCertifiedBallot:
    """A state's certified ballot lists third-party and independent
    candidates no FEC row covers (ballot-only rows). A primary-results file
    answering while it is down cannot list them — its silence must not
    delete them."""

    @staticmethod
    def _setup(monkeypatch, tmp_path, state, discovered=None):
        import json

        from app.pipeline.fetch import state_candidate_sources as sources

        async def no_calendar(client, cycle):
            return {}, True

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "report_file_problems", lambda *a, **k: None)
        monkeypatch.setattr(sc, "configured_states", lambda: {state})
        path = tmp_path / "d.json"
        path.write_text(json.dumps(discovered or {}))
        monkeypatch.setattr(sources, "_DISCOVERED_PATH", str(path))
        monkeypatch.setattr(sources, "_discovered_cache", None)
        return sources.source_for_state(state)

    @staticmethod
    def _ids(db):
        return sorted(c.id for c in db.query(Candidate).all())

    @pytest.mark.asyncio
    async def test_the_crawlers_spare_answering_for_tx(self, db_session, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        self._setup(monkeypatch, tmp_path, "TX", {"TX": {
            "strategy": "tabular", "source_name": "a results file",
            "description": "Found automatically on 2026-10-01: x"}})
        _race(db_session, "2026-SEN-TX", "TX", office="S")
        _candidate(db_session, "A", "2026-SEN-TX", "PAXTON, KEN", party="REP", confirmed_general=True)
        db_session.commit()
        paxton = {"office": "S", "district": None, "party": "R", "last_name": "PAXTON", "display_name": "Ken Paxton"}
        green = {"office": "S", "district": None, "party": "G", "last_name": "GREENE", "display_name": "Gina Greene"}
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", AsyncMock(return_value=[paxton, green]))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        night1 = self._ids(db_session)
        assert any(i.startswith("ballot:") for i in night1)
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", AsyncMock(return_value=None))
        monkeypatch.setitem(sc.STRATEGIES, "tabular", AsyncMock(return_value=[paxton]))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert self._ids(db_session) == night1

    @pytest.mark.asyncio
    async def test_primary_results_beside_a_general_list_that_is_down(self, db_session, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        src = self._setup(monkeypatch, tmp_path, "CO")
        _race(db_session, "2026-SEN-CO", "CO", office="S")
        _candidate(db_session, "A", "2026-SEN-CO", "HICK, JOHN", party="DEM")
        db_session.commit()
        rec = [{"office": "S", "district": None, "party": "D", "last_name": "HICK", "display_name": "John Hick"}]
        green = {"office": "S", "district": None, "party": "G", "last_name": "GREENE", "display_name": "Gina Greene"}
        monkeypatch.setitem(sc.STRATEGIES, src["strategy"], AsyncMock(return_value=rec))
        monkeypatch.setitem(sc.STRATEGIES, src["general_list"]["strategy"], AsyncMock(return_value=rec + [green]))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        night1 = self._ids(db_session)
        assert len(night1) == 2
        monkeypatch.setitem(sc.STRATEGIES, src["general_list"]["strategy"], AsyncMock(return_value=None))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert self._ids(db_session) == night1

    @pytest.mark.asyncio
    async def test_a_certified_list_that_answers_empty_or_partial(self, db_session, monkeypatch, tmp_path):
        """A general list returning [] (Google dropping the election) or
        missing a race leaves those races to primary results, which must
        not prune the certified rows there."""
        from unittest.mock import AsyncMock

        src = self._setup(monkeypatch, tmp_path, "CO")
        _race(db_session, "2026-SEN-CO", "CO", office="S")
        _candidate(db_session, "A", "2026-SEN-CO", "HICK, JOHN", party="DEM")
        db_session.commit()
        rec = [{"office": "S", "district": None, "party": "D", "last_name": "HICK", "display_name": "John Hick"}]
        green = {"office": "S", "district": None, "party": "G", "last_name": "GREENE", "display_name": "Gina Greene"}
        monkeypatch.setitem(sc.STRATEGIES, src["strategy"], AsyncMock(return_value=rec))
        monkeypatch.setitem(sc.STRATEGIES, src["general_list"]["strategy"], AsyncMock(return_value=rec + [green]))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        night1 = self._ids(db_session)
        monkeypatch.setitem(sc.STRATEGIES, src["general_list"]["strategy"], AsyncMock(return_value=[]))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert self._ids(db_session) == night1

    @pytest.mark.asyncio
    async def test_a_placeholder_an_fec_row_replaced_goes_whoever_answers(
        self, db_session, monkeypatch, tmp_path,
    ):
        """Not pruning must not show one person twice: a certified
        candidate who has since filed with the FEC is matched by the weaker
        source, and their placeholder goes — a same-surname candidate of
        another party stays."""
        from unittest.mock import AsyncMock

        self._setup(monkeypatch, tmp_path, "TX", {"TX": {
            "strategy": "tabular", "source_name": "a results file",
            "description": "Found automatically on 2026-10-01: x"}})
        _race(db_session, "2026-SEN-TX", "TX", office="S")
        db_session.commit()
        allred = {"office": "S", "district": None, "party": "D", "last_name": "ALLRED", "display_name": "Colin Allred"}
        green = {"office": "S", "district": None, "party": "G", "last_name": "ALLRED", "display_name": "Gail Allred"}
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", AsyncMock(return_value=[allred, green]))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert len([i for i in self._ids(db_session) if i.startswith("ballot:")]) == 2
        _candidate(db_session, "S0TX", "2026-SEN-TX", "ALLRED, COLIN", party="DEM")
        db_session.commit()
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", AsyncMock(return_value=None))
        monkeypatch.setitem(sc.STRATEGIES, "tabular", AsyncMock(return_value=[allred]))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert self._ids(db_session) == ["S0TX", "ballot:2026-SEN-TX:gail-allred"]

    @pytest.mark.parametrize("order", ["mary_first", "john_first"])
    def test_a_same_surname_same_party_candidate_is_not_taken_for_another(self, db_session, order):
        """A top-four race can list Mary Smith (no FEC row) beside John
        Smith (FEC-filed): John's match must never drop Mary's row."""
        _race(db_session, "2026-SEN-AK", "AK", office="S")
        _candidate(db_session, "S1", "2026-SEN-AK", "SMITH, JOHN", party="REP")
        _candidate(db_session, "S2", "2026-SEN-AK", "SMITH, ROBERT", party="REP")
        db_session.commit()
        mary = {"office": "S", "district": None, "party": "R", "last_name": "SMITH", "display_name": "Mary Smith"}
        john = {"office": "S", "district": None, "party": "R", "last_name": "SMITH", "display_name": "John Smith"}
        records = [mary, john] if order == "mary_first" else [john, mary]
        sc._apply_ballot(db_session, 2026, "AK", records, keep_unlisted=True, authoritative=True)
        assert "ballot:2026-SEN-AK:mary-smith" in self._ids(db_session)
        sc._apply_ballot(db_session, 2026, "AK", [john], keep_unlisted=True, authoritative=False, prune=False)
        assert "ballot:2026-SEN-AK:mary-smith" in self._ids(db_session)

    @pytest.mark.parametrize("certified, tonight, fec", [
        ("T.J. Cox", "T.J. Cox", "COX, TERRANCE JOHN"),  # initials only
        ("Daniel Cox", "Dan Cox", "COX, DANIEL"),  # another source's short form
    ])
    def test_an_fec_match_replaces_its_placeholder_however_it_was_spelled(
        self, db_session, certified, tonight, fec,
    ):
        _race(db_session, "2026-HOUSE-CA-21", "CA", office="H", district=21)
        db_session.commit()
        rec = {"office": "H", "district": 21, "party": "D", "last_name": "COX"}
        sc._apply_ballot(db_session, 2026, "CA", [rec | {"display_name": certified}],
                         keep_unlisted=True, authoritative=True)
        assert any(i.startswith("ballot:") for i in self._ids(db_session))
        _candidate(db_session, "H1", "2026-HOUSE-CA-21", fec, party="DEM")
        db_session.commit()
        sc._apply_ballot(db_session, 2026, "CA", [rec | {"display_name": tonight}],
                         keep_unlisted=True, authoritative=False, prune=False)
        assert self._ids(db_session) == ["H1"]

    def test_two_people_on_one_list_never_share_a_row(self, db_session):
        """Chris and Christine Smith, both certified and neither FEC-filed:
        "chris" is a short form of "christine", but a row this pass kept
        for one is never reused for the other."""
        _race(db_session, "2026-HOUSE-CA-3", "CA", office="H", district=3)
        db_session.commit()
        rec = {"office": "H", "district": 3, "party": "D", "last_name": "SMITH"}
        sc._apply_ballot(db_session, 2026, "CA", [rec | {"display_name": "Chris Smith"},
                                                   rec | {"display_name": "Christine Smith"}],
                         keep_unlisted=True, authoritative=True)
        assert len([i for i in self._ids(db_session) if i.startswith("ballot:")]) == 2

    def test_an_ambiguous_placeholder_is_left_alone(self, db_session):
        """John's FEC row filed as "SMITH, J" fits Jane's row by initial as
        well as John's: with two candidates, neither is guessed — John's is
        taken by his exact given name, Jane's stays."""
        _race(db_session, "2026-HOUSE-CA-4", "CA", office="H", district=4)
        db_session.commit()
        rec = {"office": "H", "district": 4, "party": "D", "last_name": "SMITH"}
        jane, john = rec | {"display_name": "Jane Smith"}, rec | {"display_name": "John Smith"}
        sc._apply_ballot(db_session, 2026, "CA", [jane, john], keep_unlisted=True, authoritative=True)
        _candidate(db_session, "H1", "2026-HOUSE-CA-4", "SMITH, J", party="DEM")
        db_session.commit()
        sc._apply_ballot(db_session, 2026, "CA", [john], keep_unlisted=True, authoritative=False, prune=False)
        assert "ballot:2026-HOUSE-CA-4:jane-smith" in self._ids(db_session)

    def test_a_different_partys_different_person_is_not_matched_to_the_one_fec_row(self, db_session):
        """Mary Smith (Libertarian, never FEC-filed) beside John Smith (DEM,
        filed): the lone same-surname FEC row is not hers."""
        _race(db_session, "2026-HOUSE-CA-5", "CA", office="H", district=5)
        _candidate(db_session, "H1", "2026-HOUSE-CA-5", "SMITH, JOHN", party="DEM")
        db_session.commit()
        mary = {"office": "H", "district": 5, "party": "L", "last_name": "SMITH", "display_name": "Mary Smith"}
        sc._apply_ballot(db_session, 2026, "CA", [mary], keep_unlisted=True, authoritative=False)
        assert "ballot:2026-HOUSE-CA-5:mary-smith" in self._ids(db_session)
        assert db_session.get(Candidate, "H1").confirmed_general is not True

    def test_a_nickname_or_a_recoded_party_alone_still_matches(self, db_session):
        _race(db_session, "2026-HOUSE-CA-6", "CA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-CA-6", "JONES, JAMES", party="DEM")
        db_session.commit()
        jim = {"office": "H", "district": 6, "party": "D", "last_name": "JONES", "display_name": "Jim Jones"}
        sc._apply_ballot(db_session, 2026, "CA", [jim], keep_unlisted=True, authoritative=False)
        assert db_session.get(Candidate, "H1").confirmed_general is True

    @pytest.mark.parametrize("fec_party, record_party", [("DFL", "D"), ("DNL", "D"), ("NPA", "I"), ("UN", "I")])
    def test_fecs_state_party_codes_are_the_same_party(self, db_session, fec_party, record_party):
        """FEC files some Minnesota and North Dakota Democrats under the
        state party's code (Ilhan Omar as DFL) and independents as NPA/UN:
        a nominee listed by nickname must still be matched."""
        _race(db_session, "2026-HOUSE-MN-2", "MN", office="H", district=2)
        _candidate(db_session, "H1", "2026-HOUSE-MN-2", "SMITH, WILLIAM", party=fec_party)
        db_session.commit()
        bill = {"office": "H", "district": 2, "party": record_party, "last_name": "SMITH",
                "display_name": "Bill Smith"}
        sc._apply_ballot(db_session, 2026, "MN", [bill], keep_unlisted=True, authoritative=True)
        assert self._ids(db_session) == ["H1"]
        assert db_session.get(Candidate, "H1").confirmed_general is True

    def test_a_nickname_placeholder_goes_when_its_fec_row_matches(self, db_session):
        _race(db_session, "2026-HOUSE-CA-7", "CA", office="H", district=7)
        db_session.commit()
        bill = {"office": "H", "district": 7, "party": "D", "last_name": "SMITH", "display_name": "Bill Smith"}
        sc._apply_ballot(db_session, 2026, "CA", [bill], keep_unlisted=True, authoritative=True)
        _candidate(db_session, "H1", "2026-HOUSE-CA-7", "SMITH, WILLIAM", party="DEM")
        db_session.commit()
        sc._apply_ballot(db_session, 2026, "CA", [bill], keep_unlisted=True, authoritative=False, prune=False)
        assert self._ids(db_session) == ["H1"]

    def test_names_without_a_given_half_match_nothing(self):
        assert sc._same_given_name("SMITH", "SMITH") is False
        assert sc._given_initial("SMITH, MR. J") == "j"
        assert sc._same_given_name("SMITH, DR. JOHN", "SMITH, J") is True
        assert sc._same_given_name("SMITH, MR. J", "SMITH, MARY") is False

    def test_one_person_spelled_two_ways_keeps_one_row(self, db_session):
        _race(db_session, "2026-HOUSE-CO-1", "CO", office="H", district=1)
        db_session.commit()
        for name in ("Jane Q. Doe", "Jane Doe", "Jane Q. Doe"):
            sc._apply_ballot(db_session, 2026, "CO", [
                {"office": "H", "district": 1, "party": "G", "last_name": "DOE", "display_name": name},
            ], keep_unlisted=True, authoritative=False, prune=False)
        assert len([i for i in self._ids(db_session) if i.startswith("ballot:")]) == 1

    def test_a_state_with_no_certified_source_still_prunes(self):
        assert sc._may_prune({"strategy": "clarity"}, {"strategy": "clarity"}) is True
        assert sc._may_prune({"general_ballot_complete": True}, {"strategy": "tabular"}) is False
        assert sc._may_prune({"general_ballot_complete": True}, {"general_ballot_complete": True}) is True


class TestAlertsAndCadence:
    def test_a_new_failure_later_in_the_day_is_not_silenced(self, monkeypatch):
        keys = []
        monkeypatch.setattr("app.ops_alerts.send_ops_alert",
                            lambda subject, body, dedupe_key=None: keys.append(dedupe_key))
        sc.report_file_problems("s", "l", ["WV: raised X"], "k")
        sc.report_file_problems("s", "l", ["WV: raised X again"], "k")
        sc.report_file_problems("s", "l", ["WV: raised X", "TX: raised Y"], "k")
        assert keys[0] == keys[1] and keys[2] != keys[0]

    def test_a_weekly_crawl_does_not_slip_to_the_eighth_night(self):
        from datetime import datetime

        record = {"lastOk": datetime(2026, 9, 1, 5).isoformat()}
        assert sc._crawl_due(record, datetime(2026, 9, 8, 2)) is True  # the run started earlier tonight
        assert sc._crawl_due(record, datetime(2026, 9, 7, 5)) is False


class TestTheCrawlAlwaysReports:
    @pytest.mark.asyncio
    async def test_what_was_found_before_the_loop_raised_is_still_alerted(self, db_session, monkeypatch):
        async def loop(db, client, cycle, hand_verified, outcomes, problems):
            problems.append("AA: disk full")
            try:
                raise ValueError("inner")
            except ValueError:
                sc._note_raise("BB", "Source discovery")
            raise RuntimeError("database is locked")

        alerts = []
        monkeypatch.setattr(sc, "_crawl_due_states", loop)
        monkeypatch.setattr(sc, "report_file_problems",
                            lambda subject, lead, problems, key: alerts.extend(problems))
        with pytest.raises(RuntimeError):
            await sc.crawl_for_new_sources(db_session, None, 2026)
        assert alerts == ["AA: disk full", "BB: Source discovery raised ValueError: inner"]


class TestSyncRaisesAreReported:
    @pytest.mark.asyncio
    async def test_a_raising_source_in_the_sync_is_alerted(self, db_session, monkeypatch):
        from unittest.mock import AsyncMock

        async def no_calendar(client, cycle):
            return {}, True

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "configured_states", lambda: {"TX"})
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", AsyncMock(side_effect=TypeError("refactor slip")))
        monkeypatch.setattr(sc, "_discovered_source", lambda st: None)
        alerts = []
        monkeypatch.setattr(sc, "report_file_problems",
                            lambda subject, lead, problems, key: alerts.extend(problems))
        results = await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert results["TX"]["status"] == "fetch_failed"
        assert alerts == ["TX: Confirmed-candidate fetch raised TypeError: refactor slip"]


class TestAPrimarySeasonListSaysTheBallotIsNotYetWhole:
    @pytest.mark.asyncio
    async def test_a_failed_read_before_any_basis_is_not_taken_as_complete(self, db_session, monkeypatch):
        """A fresh deploy or a new cycle, and NC's list fails to fetch: with
        no basis recorded the page fell back to the entry's
        general_ballot_complete. A basis already recorded is left alone."""
        recorded = []

        async def fails(client, year, state, source):
            return None

        monkeypatch.setattr(sc, "states_with_filings", lambda: {"NC"})
        monkeypatch.setattr(sc, "fetch_ballot_candidates", fails)
        real = sc._record_ballot_basis

        def record(db, c, st, src, **k):
            recorded.append(src.get("general_ballot_complete"))
            real(db, c, st, src, **k)

        monkeypatch.setattr(sc, "_record_ballot_basis", record)
        await sc.sync_ballot_filings(db_session, None, 2026)
        await sc.sync_ballot_filings(db_session, None, 2026)
        assert recorded == [False]  # once: the second night finds a basis on record

    @pytest.mark.asyncio
    async def test_nc_records_an_incomplete_basis_until_its_list_names_november(
        self, db_session, monkeypatch,
    ):
        """NC's list speaks for its November ballot, so nothing else records
        its basis — and without one the page fell back to the entry's
        general_ballot_complete and called primary nominees the whole ballot."""
        recorded = []

        async def primary_only(client, year, state, source):
            return {"primary": [], "general": [], "primary_date": None}

        monkeypatch.setattr(sc, "states_with_filings", lambda: {"NC"})
        monkeypatch.setattr(sc, "fetch_ballot_candidates", primary_only)
        monkeypatch.setattr(sc, "_record_ballot_basis",
                            lambda db, c, st, src, **k: recorded.append((st, src.get("general_ballot_complete"))))
        await sc.sync_ballot_filings(db_session, None, 2026)
        assert recorded == [("NC", False)]


class TestEveryCrawlLooksForAFilingList:
    @pytest.mark.asyncio
    async def test_a_state_whose_results_source_was_found_is_searched_too(self, db_session, monkeypatch):
        """A filing list's general rows are the only way to see a third-party
        candidate; a state the crawler found results for was never searched
        for one."""
        _race(db_session, "2026-HOUSE-ZZ-3", "ZZ", "H", 3)
        _candidate(db_session, "c1", "2026-HOUSE-ZZ-3", "FLOOD, MIKE", party="REP")
        db_session.commit()
        TestCrawlAdoption._patch(
            monkeypatch, [{"office": "H", "district": 3, "party": "R", "last_name": "Flood"}],
        )
        looked = []

        async def filings(client, state, cycle):
            looked.append(state)
            return None

        monkeypatch.setattr(sc, "discover_filings", filings)
        outcomes = await sc.crawl_for_new_sources(db_session, None, 2026)
        assert outcomes["ZZ"].startswith("adopted") and looked == ["ZZ"]


class TestFilingsForHandVerifiedStates:
    def test_a_crawler_found_list_never_takes_a_verified_states_november_authority(
        self, tmp_path, monkeypatch,
    ):
        """For a hand-verified state only a list in its own entry speaks for
        November (NC's); one the crawler found is unverified and must not
        strip a certified ballot's authority. Elsewhere a found list does."""
        import json

        from app.pipeline.fetch import state_candidate_sources as sources

        path = tmp_path / "discovered.json"
        path.write_text(json.dumps({"TX": {"filings": {"url": "x"}}, "ZZ": {"filings": {"url": "y"}}}))
        monkeypatch.setattr(sources, "_DISCOVERED_PATH", str(path))
        monkeypatch.setattr(sources, "_discovered_cache", None)
        assert sc._has_general_filings("TX") is False
        assert sc._has_general_filings("ZZ") is True
        assert sc._has_general_filings("NC") is True  # its own list

    @pytest.mark.asyncio
    async def test_a_verified_ballot_keeps_unconfirming_with_a_found_list_on_file(
        self, db_session, monkeypatch, tmp_path,
    ):
        """TX's certified ballot drops a withdrawn nominee and records its
        basis whether or not the crawler has found TX a (primary-only)
        filing list; that list's general rows are not applied."""
        import json
        from unittest.mock import AsyncMock

        from app.pipeline.fetch import state_candidate_sources as sources

        async def no_calendar(client, cycle):
            return {}, False

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "configured_states", lambda: {"TX"})
        path = tmp_path / "d.json"
        path.write_text(json.dumps({"TX": {"filings": {"url": "x"}}}))
        monkeypatch.setattr(sources, "_DISCOVERED_PATH", str(path))
        monkeypatch.setattr(sources, "_discovered_cache", None)
        _race(db_session, "2026-SEN-TX", "TX", office="S")
        _candidate(db_session, "A", "2026-SEN-TX", "PAXTON, KEN", party="REP", confirmed_general=True)
        _candidate(db_session, "B", "2026-SEN-TX", "WITHDRAWN, BOB", party="REP", confirmed_general=True)
        db_session.commit()
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", AsyncMock(return_value=[
            {"office": "S", "district": None, "party": "R", "last_name": "PAXTON"}]))
        recorded = []
        monkeypatch.setattr(sc, "_record_ballot_basis", lambda db, c, st, src, **k: recorded.append(st))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert db_session.get(Candidate, "B").confirmed_general is False
        assert recorded == ["TX"]

        async def a_list_naming_bob(client, year, state, source):
            return {"primary": [], "primary_date": None, "general": [
                {"office": "S", "district": None, "party": "R", "last_name": "WITHDRAWN"}]}

        monkeypatch.setattr(sc, "fetch_ballot_candidates", a_list_naming_bob)
        await sc.sync_ballot_filings(db_session, None, 2026)
        assert db_session.get(Candidate, "B").confirmed_general is False  # its general rows not applied

    @pytest.mark.asyncio
    async def test_a_spare_source_answers_with_its_own_authority(self, db_session, monkeypatch, tmp_path):
        """When TX's certified ballot is down and a discovered results file
        answers, that file isn't the certified ballot: it must not un-confirm
        a third-party nominee the ballot confirmed, nor be recorded as it."""
        import json
        from unittest.mock import AsyncMock

        from app.pipeline.fetch import state_candidate_sources as sources

        async def no_calendar(client, cycle):
            return {}, False

        monkeypatch.setattr(sc.election_dates, "fetch_fec_calendar", no_calendar)
        monkeypatch.setattr(sc, "configured_states", lambda: {"TX"})
        path = tmp_path / "d.json"
        path.write_text(json.dumps({"TX": {"strategy": "tabular", "source_name": "a results file",
                                           "description": "Found automatically on 2026-10-01: x"}}))
        monkeypatch.setattr(sources, "_DISCOVERED_PATH", str(path))
        monkeypatch.setattr(sources, "_discovered_cache", None)
        _race(db_session, "2026-SEN-TX", "TX", office="S")
        _candidate(db_session, "A", "2026-SEN-TX", "PAXTON, KEN", party="REP", confirmed_general=True)
        _candidate(db_session, "L", "2026-SEN-TX", "LIBBY, LARRY", party="LIB", confirmed_general=True)
        db_session.commit()
        monkeypatch.setitem(sc.STRATEGIES, "tx_civix", AsyncMock(return_value=None))
        monkeypatch.setitem(sc.STRATEGIES, "tabular", AsyncMock(return_value=[
            {"office": "S", "district": None, "party": "R", "last_name": "PAXTON"}]))
        recorded = []
        monkeypatch.setattr(sc, "_record_ballot_basis",
                            lambda db, c, st, src, **k: recorded.append(src.get("source_name")))
        await sc.sync_confirmed_candidates(db_session, None, 2026)
        assert db_session.get(Candidate, "L").confirmed_general is True
        assert "Texas Secretary of State" not in " ".join(filter(None, recorded))

    def test_a_filing_list_found_for_a_hand_verified_state_is_read(self, tmp_path, monkeypatch):
        """The hand-verified entry won whole, so a filing list the crawler
        proved for such a state was stored and never used."""
        import json

        from app.pipeline.fetch import state_candidate_sources as sources

        path = tmp_path / "discovered.json"
        path.write_text(json.dumps({"TX": {"filings": {"url": "x"}}}))
        monkeypatch.setattr(sources, "_DISCOVERED_PATH", str(path))
        monkeypatch.setattr(sources, "_discovered_cache", None)
        assert (sources.source_for_state("TX") or {}).get("filings") is None  # hand entry wins whole
        assert sources.filings_for_state("TX") == {"url": "x"}
        assert "TX" in sources.states_with_filings()

    @pytest.mark.asyncio
    async def test_adopting_one_stores_only_the_filing_list(self, db_session, monkeypatch):
        """Not a copy of the hand-verified entry, which would shadow later
        edits to it and serve as a stale spare source."""
        _race(db_session, "2026-HOUSE-TX-3", "TX", "H", 3)
        _candidate(db_session, "c1", "2026-HOUSE-TX-3", "FLOOD, MIKE", party="REP")
        db_session.commit()
        hand = sc._sources_file()["states"]["TX"]
        saved = {}

        async def filings(client, state, cycle):
            return {"url": "x", "_evidence": "TX filings"}

        async def ballot(client, cycle, state, source):
            return {"primary": [{"office": "H", "district": 3, "party": "R", "last_name": "Flood"}],
                    "general": [], "primary_date": None}

        monkeypatch.setattr(sc, "discover_filings", filings)
        monkeypatch.setattr(sc, "fetch_ballot_candidates", ballot)
        monkeypatch.setattr(sc, "_discovered_source", lambda st: dict(hand) | {"filings": {"url": "old"}})
        monkeypatch.setattr(sc, "save_discovered", lambda st, src: saved.update({st: src}))
        outcome = await sc._adopt_filings(db_session, None, 2026, "TX", hand)
        assert outcome.startswith("filings adopted")
        assert saved["TX"] == {"filings": {"url": "x"}, "source_name": "TX filings"}

    @pytest.mark.asyncio
    async def test_a_replacement_the_crawler_found_is_kept_whole(self, db_session, monkeypatch):
        """A results source adopted for a broken hand-verified state carries
        that state's rules — it is not a copy to strip."""
        _race(db_session, "2026-HOUSE-TX-3", "TX", "H", 3)
        _candidate(db_session, "c1", "2026-HOUSE-TX-3", "FLOOD, MIKE", party="REP")
        db_session.commit()
        hand = sc._sources_file()["states"]["TX"]
        found = {"strategy": hand["strategy"], "runoff_threshold_pct": 50.0,
                 "source_name": "a new host", "description": "Found automatically on 2026-09-01: x"}
        saved = {}

        async def filings(client, state, cycle):
            return {"url": "x", "_evidence": "TX filings"}

        async def ballot(client, cycle, state, source):
            return {"primary": [{"office": "H", "district": 3, "party": "R", "last_name": "Flood"}],
                    "general": [], "primary_date": None}

        monkeypatch.setattr(sc, "discover_filings", filings)
        monkeypatch.setattr(sc, "fetch_ballot_candidates", ballot)
        monkeypatch.setattr(sc, "_discovered_source", lambda st: dict(found))
        monkeypatch.setattr(sc, "save_discovered", lambda st, src: saved.update({st: src}))
        await sc._adopt_filings(db_session, None, 2026, "TX", hand)
        assert saved["TX"] == found | {"filings": {"url": "x"}}


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
            return {}, False

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


class TestFecPartyCodes:
    """FEC's published party codes, translated only where they name the
    same party — never a different one."""

    def test_translations(self):
        from app.pipeline.fetch.state_candidates_common import fec_party

        assert fec_party("DFL") == fec_party("DNL") == "DEM"
        assert {fec_party(c) for c in ("NPA", "UN", "NNE", "NOP", "NON")} == {"IND"}
        assert fec_party("UST") == "CON"
        assert fec_party("CRV") == "CRV"  # the Conservative Party is not the Constitution Party
        assert fec_party("REP") == "REP" and fec_party(None) is None

    def test_a_conservative_party_filer_is_not_the_constitution_nominee(self, db_session):
        _race(db_session, "2026-HOUSE-NY-1", "NY", office="H", district=1)
        _candidate(db_session, "H1", "2026-HOUSE-NY-1", "JONES, ROBERT", party="CRV")
        _candidate(db_session, "H2", "2026-HOUSE-NY-1", "JONES, ALICE", party="REP")
        db_session.commit()
        mary = [c for c in db_session.get(Race, "2026-HOUSE-NY-1").candidates]
        assert sc._match_candidate(mary, "JONES", "C", "Mary Jones") is None

    def test_one_person_refiled_under_the_state_code_is_still_one_person(self, db_session):
        _race(db_session, "2026-SEN-MN", "MN", office="S")
        _candidate(db_session, "S1", "2026-SEN-MN", "KLOBUCHAR, AMY", party="DFL", has_raised_funds=True)
        _candidate(db_session, "S2", "2026-SEN-MN", "KLOBUCHAR, AMY J", party="DEM")
        db_session.commit()
        rows = db_session.get(Race, "2026-SEN-MN").candidates
        assert sc._match_candidate(rows, "KLOBUCHAR", "D", "Amy Klobuchar").id == "S1"

    def test_the_page_does_not_bring_back_a_dfl_nominees_primary_loser(self, db_session):
        from app.api.elections import _unopposed_nominees

        _race(db_session, "2026-HOUSE-MN-3", "MN", office="H", district=3)
        nominee = _candidate(db_session, "H1", "2026-HOUSE-MN-3", "DOE, JANE", party="DFL", confirmed_general=True)
        rep = _candidate(db_session, "H2", "2026-HOUSE-MN-3", "ROE, RICK", party="REP", confirmed_general=True)
        loser = _candidate(db_session, "H3", "2026-HOUSE-MN-3", "LOSS, LEE", party="DEM")
        db_session.commit()
        assert _unopposed_nominees([nominee, rep, loser], [nominee, rep], "MN", False) == []

    def test_the_api_names_each_candidates_party_group(self, db_session):
        from app.api.elections import _candidate_summary

        _race(db_session, "2026-HOUSE-MN-5", "MN", office="H", district=5)
        omar = _candidate(db_session, "H1", "2026-HOUSE-MN-5", "OMAR, ILHAN", party="DFL")
        db_session.commit()
        summary = _candidate_summary(omar)
        assert summary["party"] == "DFL" and summary["partyGroup"] == "DEM"
