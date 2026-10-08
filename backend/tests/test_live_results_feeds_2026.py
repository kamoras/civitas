"""The 2026 general's live-results feeds as they actually stand, three weeks
out (audit of 2026-10-08), and the gates their real shapes broke.

Fixtures (tests/fixtures/live_results/, each with its own _source):

  co_primary_2026_electionsettings.json  Colorado's Clarity settings for
      its certified 2026 primary: showtestdatawatermark is the STRING "0".
  va_general_2026_index_and_copies.json  Virginia's Enhanced Voting index,
      which lists the 2026 general twice under one name, and both payloads.
  wa_general_2026_staged.json  Washington's staged 2026 general, whose
      House labels put the district first.
"""

import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.live_results import sync as er
from app.models import LiveResultRead, Race, RaceResult
from app.pipeline.fetch import (
    election_results,
    http_utils,
    state_candidates_clarity as clarity,
    state_candidates_enhanced_voting as ev,
    state_candidates_tally_enr as tally,
)
from app.pipeline.fetch.election_results import ContestCount, StateCount
from app.pipeline.fetch.state_candidate_sources import source_for_state

FIXTURES = Path(__file__).parent / "fixtures" / "live_results"
DAY = date(2026, 11, 3)


def _json(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _fresh_clarity_caches():
    clarity._general_eids.clear()
    clarity._general_eid_misses.clear()
    yield
    clarity._general_eids.clear()
    clarity._general_eid_misses.clear()


class TestClarityFlags:
    """Every 2026 Clarity election (CO, IA, SC, WV primaries) carries
    showtestdatawatermark "0", a string; read as truthy, all four states'
    counts were refused as test data all night."""

    SETTINGS = _json("co_primary_2026_electionsettings.json")

    def _serve(self, monkeypatch, details):
        summary = {"Contests": [{"C": "U.S. Senator", "CH": ["Pat Doe", "Lee Roe"], "P": ["DEM", "REP"],
                                 "V": [10, 9], "PR": 1, "TP": 64, "T": 19}]}

        async def fake_get(client, url, label):
            if url.endswith("elections.json"):
                return SimpleNamespace(json=lambda: [{"EID": "126592", "ElectionName": "2026 Primary",
                                                      "Date": "6/30/2026 12:00:00 AM"}])
            if url.endswith("current_ver.txt"):
                return SimpleNamespace(text="377440")
            if url.endswith("sum.json"):
                return SimpleNamespace(json=lambda: summary, headers={})
            return SimpleNamespace(json=lambda: {"settings": {"electiondetails": details}})

        monkeypatch.setattr(clarity, "_get", fake_get)

    async def test_the_real_2026_settings_are_not_a_test(self, monkeypatch):
        details = self.SETTINGS["settings"]["electiondetails"]
        assert details["showtestdatawatermark"] == "0"  # the real shape
        self._serve(monkeypatch, details)
        got = await clarity.fetch_general_results(None, date(2026, 6, 30), "CO", {})
        assert got is not None and got.contests[0].candidates[0] == ("Pat Doe", "D", 10)

    @pytest.mark.parametrize("value", ["1", 1, True, "true", "yes"])
    async def test_a_watermark_that_is_on_is_refused(self, monkeypatch, value):
        self._serve(monkeypatch, {**self.SETTINGS["settings"]["electiondetails"], "showtestdatawatermark": value})
        with pytest.raises(election_results.UntrustedCount):
            await clarity.fetch_general_results(None, date(2026, 6, 30), "CO", {})

    @pytest.mark.parametrize("value,on", [
        (None, False), (False, False), (0, False), ("0", False), ("", False), ("False", False),
        (True, True), ("1", True), ("True", True), ("unexpected", True),
    ])
    def test_flag_reading(self, value, on):
        assert clarity._flag_set(value) is on


class TestVirginiaListsItsGeneralTwice:
    FIX = _json("va_general_2026_index_and_copies.json")
    VA = source_for_state("VA")

    def _serve(self, monkeypatch, payloads, urls):
        async def fake(client, rl, method, url, **kw):
            urls.append(url)
            if "/jurisdictions/" in url:
                return SimpleNamespace(json=lambda: self.FIX["index"])
            eid = url.rsplit("/", 2)[-2]
            return SimpleNamespace(json=lambda: payloads[eid])

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)

    async def test_copies_of_one_election_read_the_one_updated_last(self, monkeypatch):
        urls = []
        self._serve(monkeypatch, copy.deepcopy(self.FIX["payloads"]), urls)
        got = await ev.fetch_general_results(None, DAY, "VA", self.VA)
        assert got.page_url.endswith("/elections/2026-November-General")
        assert got.source_updated.isoformat().startswith("2026-10-07T17:54:22")
        assert {(c.office, c.district) for c in got.contests} == {("S", None), ("H", 1), ("H", 2)}
        # Each copy read once; the chosen payload is not fetched again.
        assert sum(u.endswith("/data") for u in urls) == 2

    async def test_the_newer_copy_wins_whichever_id_it_has(self, monkeypatch):
        payloads = copy.deepcopy(self.FIX["payloads"])
        payloads["_20261001151803"]["election"]["lastUpdated"] = "2026-11-04T03:00:00Z"
        self._serve(monkeypatch, payloads, [])
        got = await ev.fetch_general_results(None, DAY, "VA", self.VA)
        assert got.page_url.endswith("/elections/_20261001151803")

    async def test_two_different_elections_stay_refused(self, monkeypatch):
        payloads = copy.deepcopy(self.FIX["payloads"])
        payloads["_20261001151803"]["election"]["id"] = "another-election"
        self._serve(monkeypatch, payloads, [])
        with pytest.raises(election_results.UntrustedCount):
            await ev.fetch_general_results(None, DAY, "VA", self.VA)

    async def test_a_copy_that_cannot_be_read_stays_refused(self, monkeypatch):
        payloads = copy.deepcopy(self.FIX["payloads"])
        payloads["_20261001151803"] = None
        self._serve(monkeypatch, payloads, [])
        with pytest.raises(election_results.UntrustedCount):
            await ev.fetch_general_results(None, DAY, "VA", self.VA)

    async def test_the_chosen_copy_still_passes_the_production_gate(self, monkeypatch):
        payloads = copy.deepcopy(self.FIX["payloads"])
        payloads["2026-November-General"]["election"]["isProduction"] = False
        self._serve(monkeypatch, payloads, [])
        with pytest.raises(election_results.UntrustedCount, match="not production"):
            await ev.fetch_general_results(None, DAY, "VA", self.VA)


class TestWashingtonGeneralLabels:
    PAYLOAD = _json("wa_general_2026_staged.json")["payload"]

    def test_every_district_resolves_through_the_states_entry(self):
        contests = ev.general_contests(self.PAYLOAD, ev._house_patterns(source_for_state("WA")))
        assert {(c.office, c.district) for c in contests} == {("H", d) for d in range(1, 11)}

    def test_without_it_every_seat_read_as_at_large(self):
        """The defect: all ten collided on one district-less seat and the
        whole state was dropped (_without_unnumbered_house_collisions)."""
        contests = ev.general_contests(self.PAYLOAD)
        assert all(c.district is None for c in contests if c.office == "H")
        assert election_results._without_unnumbered_house_collisions("WA", contests) == []

    def test_the_state_legislative_decoy_stays_refused(self):
        names = [ev._text(i["name"]) for i in self.PAYLOAD["ballotItems"]]
        assert any(n.lower().startswith("legislative district") for n in names)
        contests = ev.general_contests(self.PAYLOAD, ev._house_patterns(source_for_state("WA")))
        assert len(contests) == 10


class TestTallyPreviewIds:
    async def test_a_preview_id_is_never_the_count(self, monkeypatch):
        elections = [
            {"electionID": "abc_Preview", "electionName": "2026 General Election", "electionDate": "2026-11-03T00:00:00"},
        ]

        async def fake_json(client, rl, url, label, **kw):
            return elections if "GetElectionList" in url else None

        monkeypatch.setattr(tally, "fetch_json_with_retry", fake_json)
        source = {"base_url": "https://api.example", "cid": "arkansas"}
        assert await tally.fetch_general_results(None, DAY, "AR", source) is None

    def test_the_real_election_beside_its_preview_is_picked(self):
        assert not tally._is_preview_id("7f77a178-af02-40ec-92db-c5cc50882c68")
        assert tally._is_preview_id("346_Preview") and tally._is_preview_id("346_preview")


class TestNoCountAlert:
    """A covered state with nothing stored an hour after its polls closed
    pages someone, whatever its reads said: North Dakota's whole results
    site answered a Cloudflare 'Blocked' page on 2026-10-08 (read as
    'unavailable', not a finding of no results), Montana's and New Mexico's
    results hosts redirected to their Secretaries' landing pages, and
    Washington's read was 'ok' with every contest dropped."""

    def _race(self, db):
        db.add(Race(id="2026-HOUSE-GA-2", cycle_year=2026, office="H", state="GA", district=2))
        db.flush()

    def _check(self, db, now):
        with patch.object(er, "utcnow", return_value=now), \
                patch.object(er, "live_results_states", return_value={"GA"}), \
                patch.object(er, "send_ops_alert") as alert:
            return er.check_missing_counts(db, DAY), alert

    def test_nothing_stored_an_hour_after_close_alerts_with_the_last_read(self, db_session):
        self._race(db_session)
        db_session.add(LiveResultRead(state="GA", election_date=DAY.isoformat(), status="unavailable",
                                      checked_at=datetime(2026, 11, 4, 0, 55)))
        db_session.flush()
        missing, alert = self._check(db_session, datetime(2026, 11, 4, 1, 1))  # GA closes 00:00Z
        assert missing == ["GA"]
        assert "'unavailable'" in alert.call_args.args[1]
        assert alert.call_args.kwargs["dedupe_key"] == "results-missing-GA-2026-11-03"

    def test_not_before_the_hour_is_up(self, db_session):
        self._race(db_session)
        missing, alert = self._check(db_session, datetime(2026, 11, 4, 0, 59))
        assert missing == [] and not alert.called

    def test_a_stored_count_is_not_missing(self, db_session):
        self._race(db_session)
        db_session.add(RaceResult(race_id="2026-HOUSE-GA-2", election_date=DAY.isoformat(), source_name="GA",
                                  tallies="[]", votes_counted=0))
        db_session.flush()
        missing, alert = self._check(db_session, datetime(2026, 11, 4, 3, 0))
        assert missing == [] and not alert.called

    def test_states_closing_later_are_not_checked_early(self, db_session):
        with patch.object(er, "utcnow", return_value=datetime(2026, 11, 4, 1, 30)), \
                patch.object(er, "live_results_states", return_value={"WA"}), \
                patch.object(er, "send_ops_alert") as alert:
            assert er.check_missing_counts(db_session, DAY) == []  # WA closes 04:00Z
        assert not alert.called


class TestFlipBarMatrix:
    """AGENTS.md's flip bar, rule by rule, on bare figures."""

    NOW = datetime(2026, 11, 4, 12, 0)

    def test_precincts_need_half_in(self):
        assert er.count_is_mostly_in(50, 100, "precincts", False, None, self.NOW)
        assert not er.count_is_mostly_in(49, 100, "precincts", False, None, self.NOW)

    def test_beside_a_precinct_count_official_does_not_suffice(self):
        assert not er.count_is_mostly_in(10, 100, "precincts", True, None, self.NOW)

    def test_with_no_reporting_figure_official_suffices(self):
        assert er.count_is_mostly_in(None, 100, "precincts", True, None, self.NOW)
        assert er.count_is_mostly_in(None, None, "precincts", True, None, self.NOW)
        assert not er.count_is_mostly_in(None, None, "precincts", False, None, self.NOW)

    @pytest.mark.parametrize("label", ["counties", "counties and cities", "cities and towns"])
    def test_places_need_every_place_and_the_settle_time(self, label):
        settled = self.NOW - er.COUNTY_FLIP_SETTLE
        early = self.NOW - er.COUNTY_FLIP_SETTLE + timedelta(minutes=1)
        assert er.count_is_mostly_in(64, 64, label, False, settled, self.NOW)
        assert not er.count_is_mostly_in(64, 64, label, False, early, self.NOW)
        assert not er.count_is_mostly_in(63, 64, label, False, settled, self.NOW)
        assert not er.count_is_mostly_in(64, 64, label, False, None, self.NOW)

    def test_for_places_official_suffices(self):
        assert er.count_is_mostly_in(2, 64, "counties", True, None, self.NOW)
