"""Live general-election readers (fetch/election_results.py and each
vendor module's fetch_general_results).

Every fixture is a REAL general-election response, trimmed, fetched
2026-09-28 from the vendor each state uses — the only general elections
these feeds hold before November are past ones, so the tests pin the
2022/2024 shapes the 2026 count will arrive in:

  fixtures_co_general_2022_summary.json  Colorado's Clarity sum.json for
      the 2022 general (EID 115903): the Senate race, CO-1 and CO-8, plus
      Governor and two "Congressional District" state offices (a Regent
      and a Board of Education seat) that must NOT be read as House races.
  fixtures_ga_general_index.json / fixtures_ga_general_2024_data.json
      Georgia's Enhanced Voting index (four entries, one a 2026 special
      runoff and one a presidential primary) and its certified 2024
      general data: two House districts and a State Senate seat.
  fixtures_ar_general_{elections,search,results,info}.json
      Arkansas's Tally ENR 2024 general: election list, contest names,
      federal results (precinct `locations` stripped) and the party table.
  fixtures_mt_general_2024.html
      Montana's TotalVote 2024 general page, first four contest blocks.
"""

import json
from datetime import date

import pytest
from pathlib import Path
from types import SimpleNamespace

from app.pipeline.fetch import (
    election_results,
    http_utils,
    state_candidates_clarity as clarity,
    state_candidates_enhanced_voting as ev,
    state_candidates_tally_enr as tally,
    state_candidates_totalvote as totalvote,
)

FIXTURES = Path(__file__).parent


def _json(name):
    return json.loads((FIXTURES / name).read_text())


def _by_seat(contests):
    return {(c.office, c.district): c for c in contests}


class TestPickGeneral:
    def test_the_only_election_that_day(self):
        assert election_results.pick_general([("November 5 Election", "a")]) == "a"

    def test_the_one_named_general_among_several(self):
        assert election_results.pick_general([("Local Special", "a"), ("2024 General", "b")]) == "b"

    def test_refuses_to_guess(self):
        assert election_results.pick_general([("Special A", "a"), ("Special B", "b")]) is None
        assert election_results.pick_general([]) is None


class TestClarity:
    def test_federal_contests_keep_every_candidates_votes(self):
        contests = _by_seat(clarity.general_contests(_json("fixtures_co_general_2022_summary.json")))
        assert set(contests) == {("S", None), ("H", 1), ("H", 8)}
        senate = contests[("S", None)]
        assert senate.candidates[0] == ("Michael Bennet", "D", 1397170)
        assert senate.candidates[1] == ("Joe O'Dea", "R", 1031693)
        # A party the shared vocabulary doesn't know keeps its votes.
        assert ("T.J. Cole", None, 16379) in senate.candidates
        assert (senate.reporting_units, senate.total_units) == (64, 64)

    def test_state_offices_named_by_congressional_district_are_not_house_races(self):
        contests = clarity.general_contests(_json("fixtures_co_general_2022_summary.json"))
        assert len(contests) == 3

    def test_mismatched_arrays_pair_nothing(self):
        summary = {"Contests": [{"C": "United States Senator", "CH": ["A", "B"], "V": [1], "P": ["DEM", "REP"]}]}
        assert clarity.general_contests(summary) == []

    @staticmethod
    def _serve(monkeypatch, settings, seen=None):
        summary = _json("fixtures_co_general_2022_summary.json")
        elections = [
            {"EID": "115903", "ElectionName": "2022 General", "Date": "11/8/2022 12:00:00 AM"},
            {"EID": "113964", "ElectionName": "2022 Primary", "Date": "6/28/2022 12:00:00 AM"},
        ]

        async def fake_get(client, url, label):
            if seen is not None:
                seen.append(url)
            if url.endswith("elections.json"):
                return SimpleNamespace(json=lambda: elections)
            if url.endswith("current_ver.txt"):
                return SimpleNamespace(text="316199")
            if url.endswith("sum.json"):
                return SimpleNamespace(json=lambda: summary, headers={"Last-Modified": "Tue, 20 Dec 2022 17:00:00 GMT"})
            return SimpleNamespace(json=lambda: {"settings": {"electiondetails": settings}})

        monkeypatch.setattr(clarity, "_get", fake_get)

    REAL_SETTINGS = {"istestmode": False, "showtestdatawatermark": None, "electiondate": "11/8/2022",
                     "participatingcounties": ["Adams|115906|314687|11/30/2022 1:12:13 PM MST|16"]}

    async def test_test_mode_is_refused(self, monkeypatch):
        self._serve(monkeypatch, {**self.REAL_SETTINGS, "istestmode": True})
        with pytest.raises(election_results.UntrustedCount):
            await clarity.fetch_general_results(None, date(2022, 11, 8), "CO", {})

    async def test_settings_for_another_day_are_refused(self, monkeypatch):
        self._serve(monkeypatch, {**self.REAL_SETTINGS, "electiondate": "6/28/2022"})
        with pytest.raises(election_results.UntrustedCount):
            await clarity.fetch_general_results(None, date(2022, 11, 8), "CO", {})

    async def test_finds_the_election_by_date_and_labels_counties(self, monkeypatch):
        seen = []
        self._serve(monkeypatch, self.REAL_SETTINGS, seen)
        got = await clarity.fetch_general_results(None, date(2022, 11, 8), "CO", {"source_name": "CO SOS"})
        assert "/CO/115903/316199/json/sum.json" in "".join(seen)
        assert got.page_url.endswith("/CO/115903/")
        assert got.unit_label == "counties"
        assert got.official is False
        assert len(got.contests) == 3
        assert got.source_version == "316199"
        assert got.source_updated.isoformat() == "2022-12-20T17:00:00"

    async def test_no_election_on_that_date_is_none(self, monkeypatch):
        async def fake_get(client, url, label):
            return SimpleNamespace(json=lambda: [{"EID": "1", "ElectionName": "2026 Primary", "Date": "6/30/2026"}])

        monkeypatch.setattr(clarity, "_get", fake_get)
        assert await clarity.fetch_general_results(None, date(2026, 11, 3), "CO", {}) is None


class TestEnhancedVoting:
    def test_general_contests(self):
        contests = _by_seat(ev.general_contests(_json("fixtures_ga_general_2024_data.json")))
        assert set(contests) == {("H", 1), ("H", 2)}  # the State Senate seat is refused
        ga1 = contests[("H", 1)]
        assert ga1.candidates[0][1:] in {("R", ga1.candidates[0][2]), ("D", ga1.candidates[0][2])}
        # Georgia's "(I) (Rep)" annotations are not part of anyone's name.
        assert all("(" not in name for name, _, _ in ga1.candidates)
        assert ga1.reporting_units == ga1.total_units and ga1.total_units > 0
        assert ga1.votes_counted == sum(v for _, _, v in ga1.candidates) or ga1.total_votes is not None

    def test_endpoints_from_a_tabular_entrys_discovery(self):
        source = {"strategy": "tabular", "discovery": {
            "mode": "sos_api_report",
            "jurisdiction_url": "https://results.sos.ga.gov/results/public/api/jurisdictions/Georgia",
            "election_url": "https://results.sos.ga.gov/results/public/api/elections/Georgia/{election_id}",
        }}
        index, data, page = ev._endpoints(source)
        assert index.endswith("/api/jurisdictions/Georgia")
        assert data.format(id="2024NovGen") == "https://results.sos.ga.gov/results/public/api/elections/Georgia/2024NovGen/data"
        # Verified 200 live: the public page a reader can check the count on.
        assert page.format(id="2024NovGen") == "https://results.sos.ga.gov/results/public/Georgia/elections/2024NovGen"

    def test_endpoints_from_rhode_islands_entry(self):
        index, data, page = ev._endpoints({"base_url": "https://electionresults.ri.gov/results/public", "jurisdiction": "rhodeisland"})
        assert data.format(id="X") == "https://electionresults.ri.gov/results/public/api/elections/rhodeisland/X/data"
        assert page.format(id="X") == "https://electionresults.ri.gov/results/public/rhodeisland/elections/X"

    async def test_fetch_picks_the_general_by_date(self, monkeypatch):
        index, data = _json("fixtures_ga_general_index.json"), _json("fixtures_ga_general_2024_data.json")
        urls = []

        async def fake(client, rl, method, url, **kw):
            urls.append(url)
            return SimpleNamespace(json=lambda: index if "/jurisdictions/" in url else data)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        got = await ev.fetch_general_results(
            None, date(2024, 11, 5), "GA",
            {"source_name": "GA SOS", "base_url": "https://results.sos.ga.gov/results/public", "jurisdiction": "Georgia"},
        )
        assert urls[-1].endswith("/elections/Georgia/2024NovGen/data")
        assert got.official is True
        assert len(got.contests) == 2


class TestTally:
    def test_general_contests_read_party_from_the_party_table(self):
        contests = _by_seat(tally.general_contests(
            _json("fixtures_ar_general_search.json"),
            _json("fixtures_ar_general_results.json"),
            _json("fixtures_ar_general_info.json")["response"]["parties"],
        ))
        assert set(contests) == {("H", 1), ("H", 2), ("H", 3), ("H", 4)}  # not the President, not State Senate
        ar3 = contests[("H", 3)]
        leader = max(ar3.candidates, key=lambda c: c[2])
        assert leader[1:] == ("R", 192101)
        assert (ar3.reporting_units, ar3.total_units, ar3.total_votes) == (876, 876, 301084)

    async def test_fetch_scopes_results_by_the_federal_type_code(self, monkeypatch):
        bodies = {
            "GetElectionList": _json("fixtures_ar_general_elections.json"),
            "GetContestSearchList": _json("fixtures_ar_general_search.json"),
            "GetElectionInfo": _json("fixtures_ar_general_info.json"),
            "GetContestResults": _json("fixtures_ar_general_results.json"),
        }
        urls = []

        async def fake(client, rl, method, url, **kw):
            urls.append(url)
            return SimpleNamespace(json=lambda: next(v for k, v in bodies.items() if k in url))

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        got = await tally.fetch_general_results(None, date(2024, 11, 5), "AR", {
            "base_url": "https://enr-results-api.totalresults.com", "cid": "arkansas",
            "results_page": "https://results.sos.arkansas.gov/",
        })
        assert any("electionID=1846" in u for u in urls)
        assert urls[-1].endswith("&contestType=FED")
        assert got.official is True
        assert got.page_url == "https://results.sos.arkansas.gov/"
        assert len(got.contests) == 4


class TestTotalVote:
    HTML = (FIXTURES / "fixtures_mt_general_2024.html").read_text()

    def test_general_contests_with_precincts(self):
        contests = _by_seat(totalvote.general_contests(self.HTML))
        assert set(contests) == {("S", None), ("H", 1), ("H", 2)}
        senate = contests[("S", None)]
        assert (senate.reporting_units, senate.total_units) == (727, 727)
        assert {p for _, p, _ in senate.candidates} >= {"R", "D"}

    def test_title_dates_the_page(self):
        assert totalvote._general_held_on(self.HTML) == "2024-11-05"

    async def test_a_page_for_another_election_is_not_this_one(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return SimpleNamespace(text=self.HTML)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        source = {"base_url": "https://electionresults.mt.gov", "queries": [{"type": "FED", "map": "CTY"}]}
        assert await totalvote.fetch_general_results(None, date(2026, 11, 3), "MT", source) is None
        got = await totalvote.fetch_general_results(None, date(2024, 11, 5), "MT", source)
        assert len(got.contests) == 3


class TestCoverage:
    def test_every_live_vendor_state_is_covered(self):
        states = election_results.live_results_states()
        assert {"CO", "IA", "WV", "AR", "ND", "MT", "NE", "RI", "GA", "WA", "VA", "UT", "ID"} <= states


class TestEnhancedVotingRefusesTestData:
    SOURCE = {"base_url": "https://results.sos.ga.gov/results/public", "jurisdiction": "Georgia"}

    async def _fetch(self, monkeypatch, mutate):
        index, data = _json("fixtures_ga_general_index.json"), _json("fixtures_ga_general_2024_data.json")
        mutate(data)

        async def fake(client, rl, method, url, **kw):
            return SimpleNamespace(json=lambda: index if "/jurisdictions/" in url else data)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        return await ev.fetch_general_results(None, date(2024, 11, 5), "GA", self.SOURCE)

    async def test_not_production(self, monkeypatch):
        with pytest.raises(election_results.UntrustedCount):
            await self._fetch(monkeypatch, lambda d: d["election"].update(isProduction=False))

    async def test_a_test_locality(self, monkeypatch):
        with pytest.raises(election_results.UntrustedCount):
            await self._fetch(monkeypatch, lambda d: d["localityElections"][1].update(isProduction=False))

    async def test_answer_for_another_day(self, monkeypatch):
        with pytest.raises(election_results.UntrustedCount):
            await self._fetch(monkeypatch, lambda d: d["election"].update(electionDate="2024-12-03"))

    async def test_real_data_carries_its_update_time(self, monkeypatch):
        got = await self._fetch(monkeypatch, lambda d: None)
        assert got.source_updated.isoformat().startswith("2025-01-07T16:55")


class TestTallyRefusesTestData:
    SOURCE = {
        "base_url": "https://enr-results-api.totalresults.com", "cid": "arkansas",
        "client_config": "https://results.example/config.json",
    }

    async def _fetch(self, monkeypatch, config=None, results=None):
        bodies = {
            "config.json": config or {"clientEnvDemo": "false", "previewElections": ["329_Preview"]},
            "GetElectionList": _json("fixtures_ar_general_elections.json"),
            "GetContestSearchList": _json("fixtures_ar_general_search.json"),
            "GetElectionInfo": _json("fixtures_ar_general_info.json"),
            "GetContestResults": results or _json("fixtures_ar_general_results.json"),
        }

        async def fake(client, rl, method, url, **kw):
            return SimpleNamespace(json=lambda: next(v for k, v in bodies.items() if k in url))

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        return await tally.fetch_general_results(None, date(2024, 11, 5), "AR", self.SOURCE)

    async def test_demo_site(self, monkeypatch):
        with pytest.raises(election_results.UntrustedCount):
            await self._fetch(monkeypatch, config={"clientEnvDemo": "true"})

    async def test_preview_election(self, monkeypatch):
        with pytest.raises(election_results.UntrustedCount):
            await self._fetch(monkeypatch, config={"clientEnvDemo": "false", "previewElections": ["1846_Preview"]})

    async def test_placeholder_answer(self, monkeypatch):
        """An unknown id answers 200 with lastUpdated 0001-01-01 and no id."""
        placeholder = {**_json("fixtures_ar_general_results.json"), "electionID": "", "lastUpdated": "0001-01-01T00:00:00"}
        with pytest.raises(election_results.UntrustedCount):
            await self._fetch(monkeypatch, results=placeholder)

    async def test_answer_for_another_election(self, monkeypatch):
        other = {**_json("fixtures_ar_general_results.json"), "electionID": "346"}
        with pytest.raises(election_results.UntrustedCount):
            await self._fetch(monkeypatch, results=other)

    async def test_production_carries_version_and_time(self, monkeypatch):
        got = await self._fetch(monkeypatch)
        assert got.source_version == "v1-1"
        assert got.source_updated.isoformat().startswith("2025-12-16T19:41:49")

    def test_north_dakota_shape_party_from_the_choice_and_write_in_dropped(self):
        """ND's party table is null and its search choices carry the code;
        its "write-in" row is not flagged isWriteIn (verified 2026-09-28)."""
        search = {"response": {"contests": {"20042": {
            "contestName": "Representative in Congress", "contestTypeCode": "SW",
            "choices": {
                "1": {"name": "Julie  Fedorchak ", "partyID": "REP", "isWriteIn": False},
                "2": {"name": "Trygve Hammer", "partyID": "DEM", "isWriteIn": False},
                "9901": {"name": "  write-in ", "partyID": "REP", "isWriteIn": False},
            },
        }}}}
        results = {"response": {"contests": {"20042": {
            "precinctsReporting": 200, "totalPrecincts": 358, "totalVotes": 1010,
            "choices": [
                {"choiceID": "1", "totalVotes": 700, "partyID": "   "},
                {"choiceID": "2", "totalVotes": 300, "partyID": "   "},
                {"choiceID": "9901", "totalVotes": 10, "partyID": "   "},
            ],
        }}}}
        [contest] = tally.general_contests(search, results, {})
        assert contest.candidates == [("Julie Fedorchak", "R", 700), ("Trygve Hammer", "D", 300)]
        assert contest.votes_counted == 1010  # the write-ins still count


def test_totalvote_reads_its_update_stamp():
    got = totalvote._updated_at((FIXTURES / "fixtures_mt_general_2024.html").read_text())
    assert got.isoformat() == "2024-12-19T20:25:20"  # 1:25:20 PM MST


class TestTotalVoteElectionDate:
    """fixtures_nm_general_2026_staged.html is New Mexico's REAL results
    page for the 2026 general, fetched 2026-09-28 with nothing counted yet
    (0 of 2,204 precincts) — the exact shape election night starts in."""

    NM = (FIXTURES / "fixtures_nm_general_2026_staged.html").read_text()

    def test_live_2026_page_is_dated_by_its_hidden_fields(self):
        assert totalvote._general_held_on(self.NM) == "2026-11-03"

    def test_a_primary_page_is_not_a_general(self):
        page = '<input id="hidElectionType" value="Primary" /><input id="hidElectionDate" value="6/2/2026 11:59:00 PM" />'
        assert totalvote._general_held_on(page) is None

    def test_staged_contests_read_with_nothing_counted(self):
        contests = _by_seat(totalvote.general_contests(self.NM))
        senate = contests[("S", None)]
        assert senate.reporting_units == 0 and senate.total_units == 2204
        assert all(v == 0 for _, _, v in senate.candidates)
        assert {p for _, p, _ in senate.candidates} >= {"D", "R"}
