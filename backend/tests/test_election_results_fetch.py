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


@pytest.fixture(autouse=True)
def _fresh_clarity_caches():
    clarity._general_eids.clear()
    clarity._general_eid_misses.clear()
    yield
    clarity._general_eids.clear()
    clarity._general_eid_misses.clear()


class TestPickGeneral:
    def test_the_only_election_that_day(self):
        assert election_results.pick_general([("November 5 Election", "a")]) == "a"

    def test_the_one_named_general_among_several(self):
        assert election_results.pick_general([("Local Special", "a"), ("2024 General", "b")]) == "b"

    def test_refuses_to_guess(self):
        """Several same-day entries with nothing singling one out raise (and
        so alert) rather than reading as "not published yet"."""
        with pytest.raises(election_results.UntrustedCount):
            election_results.pick_general([("Special A", "a"), ("Special B", "b")], "XX")
        with pytest.raises(election_results.UntrustedCount):
            election_results.pick_general([("2026 General", "a"), ("November General", "b")], "XX")
        assert election_results.pick_general([]) is None

    def test_special_recount_runoff_and_demo_are_never_the_general(self):
        pick = election_results.pick_general
        assert pick([("2024 General Election", "a"), ("Special General Election Town X", "b")]) == "a"
        assert pick([("General Election Recount", "a"), ("General Election", "b")]) == "b"
        assert pick([("General Election Runoff", "a"), ("November General", "b")]) == "b"
        assert pick([("General Election DEMO", "a"), ("General Election", "b")]) == "b"
        # A lone demo, recount or runoff on the day is still not the count.
        assert pick([("2024 Primary Election - US House 2 Recount", "a")]) is None
        assert pick([("Test General Election", "a")]) is None

    def test_a_general_that_is_also_a_special_is_still_the_general(self):
        # Georgia's real 2022 name, beside a hypothetical local special.
        assert election_results.pick_general([
            ("November 8, 2022 - General/Special Election", "a"), ("Town of X Special Election", "b"),
        ]) == "a"


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


# --- Review fixes: Enhanced Voting labels, demo ids, units, write-ins -------

_EV = _json("fixtures_ev_general_payloads.json")


def _ev_payload(key):
    import copy

    entry = _EV["payloads"][key]
    return copy.deepcopy({k: entry[k] for k in ("election", "localityElections", "ballotItems")})


def _ev_patterns(state):
    from app.pipeline.fetch.state_candidate_sources import source_for_state

    return ev._house_patterns(source_for_state(state))


class TestEnhancedVotingLabelsPerState:
    """fixtures_ev_general_payloads.json: every federal ballot item of each
    covered Enhanced Voting state's real payload (see its _source), read
    through that state's REAL entry in state_candidate_sources.json."""

    EXPECTED = {
        "UT24": {("S", None)} | {("H", d) for d in range(1, 5)},
        "VA24": {("S", None)} | {("H", d) for d in range(1, 12)},
        "GA22": {("S", None)} | {("H", d) for d in range(1, 15)},
        "ID26": {("S", None), ("H", 1), ("H", 2)},
        "WA26": {("H", d) for d in range(1, 11)},
        "RI26": {("S", None), ("H", 1), ("H", 2)},
    }

    @pytest.mark.parametrize("key", sorted(EXPECTED))
    def test_every_district_resolves(self, key):
        state = _EV["payloads"][key]["state"]
        contests = ev.general_contests(_ev_payload(key), _ev_patterns(state))
        assert {(c.office, c.district) for c in contests} == self.EXPECTED[key]
        # No House seat is left without its number: no state here is at-large.
        assert all(c.district is not None for c in contests if c.office == "H")

    @pytest.mark.parametrize("key", ["UT24", "VA24", "GA22"])
    def test_without_the_states_label_config_they_did_not(self, key):
        """The bug being fixed: UT's "U.S. House 1" read as an at-large
        seat, VA's "Member, House of Representatives (2nd District)" and
        GA's "US House Dist 3" as nothing (or no district)."""
        contests = ev.general_contests(_ev_payload(key))
        resolved = {(c.office, c.district) for c in contests if c.district is not None}
        assert resolved != {s for s in self.EXPECTED[key] if s[1] is not None}

    def test_state_legislative_decoys_stay_refused(self):
        """Each fixture carries two of its state's own legislative contests
        (UT "State Senate 2 (Multi-County)", RI "Senator in General
        Assembly District 1", WA "State Representative Pos. 1 - Legislative
        District 1", ...): none may read as federal, config or not."""
        decoys = 0
        for key in self.EXPECTED:
            state = _EV["payloads"][key]["state"]
            payload = _ev_payload(key)
            payload["ballotItems"] = [
                i for i in payload["ballotItems"]
                if ev._text(i["name"]).startswith(("State ", "DEM Senator in General", "REP Senator in General"))
            ]
            decoys += len(payload["ballotItems"])
            assert ev.general_contests(payload, _ev_patterns(state)) == []
        assert decoys == 10  # VA's 2024 general had no legislative contest

    def test_bad_config_patterns_are_skipped_not_fatal(self):
        assert ev._house_patterns({"house_label_regex": ["(unclosed", r"House (\d+)"]}) == []
        [pattern] = ev._house_patterns({"house_label_regex": r"House (?P<district>\d+)"})
        assert ev._office("House 7", [pattern]) == ("H", 7)


class TestEnhancedVotingDemoAndAmbiguity:
    # Utah's real entry, so its house_label_regex is the one exercised.
    from app.pipeline.fetch.state_candidate_sources import source_for_state as _src

    UT = _src("UT")

    @staticmethod
    def _serve(monkeypatch, index, data=None, urls=None):
        async def fake(client, rl, method, url, **kw):
            if urls is not None:
                urls.append(url)
            return SimpleNamespace(json=lambda: index if "/jurisdictions/" in url else data)

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)

    async def test_a_demo_election_is_never_picked(self, monkeypatch):
        """Utah's real index lists "primary09052023_Demo" as a plain "2023
        Primary Election" dated 2023-09-05, the only entry that day."""
        urls = []
        self._serve(monkeypatch, _EV["indexes"]["UT"], urls=urls)
        assert await ev.fetch_general_results(None, date(2023, 9, 5), "UT", self.UT) is None
        assert not any("_Demo" in u for u in urls)

    async def test_a_demo_beside_the_real_election_is_ignored(self, monkeypatch):
        index = {"elections": _EV["indexes"]["UT"]["elections"] + [
            {"publicElectionId": "general11052024_Demo", "electionDate": "2024-11-05",
             "name": [{"languageId": "en", "text": "2024 General Election"}]},
        ]}
        data = _ev_payload("UT24")
        urls = []
        self._serve(monkeypatch, index, data, urls)
        got = await ev.fetch_general_results(None, date(2024, 11, 5), "UT", self.UT)
        assert urls[-1].endswith("/elections/Utah/general11052024/data")
        assert {(c.office, c.district) for c in got.contests} == TestEnhancedVotingLabelsPerState.EXPECTED["UT24"]

    async def test_a_recount_sharing_the_date_is_not_picked(self, monkeypatch):
        # Real: "PrimaryCD2Recount2024" carries the primary's own date.
        urls = []
        self._serve(monkeypatch, _EV["indexes"]["UT"], _ev_payload("UT24"), urls)
        with pytest.raises(election_results.UntrustedCount):
            # The payload served is the 2024 general, so the date check trips
            # -- what matters is WHICH id was asked for.
            await ev.fetch_general_results(None, date(2024, 6, 25), "UT", self.UT)
        assert urls[-1].endswith("/elections/Utah/primary06252024/data")

    async def test_two_same_day_generals_raise_rather_than_guess(self, monkeypatch):
        index = {"elections": [
            {"publicElectionId": "a", "electionDate": "2026-11-03", "name": [{"languageId": "en", "text": "2026 General"}]},
            {"publicElectionId": "b", "electionDate": "2026-11-03", "name": [{"languageId": "en", "text": "November General"}]},
        ]}
        self._serve(monkeypatch, index)
        with pytest.raises(election_results.UntrustedCount):
            await ev.fetch_general_results(None, date(2026, 11, 3), "UT", self.UT)


class TestEnhancedVotingUnitsAndWriteIns:
    @pytest.mark.parametrize("key", sorted(TestEnhancedVotingLabelsPerState.EXPECTED))
    def test_locality_units_read_as_counties(self, key):
        """Each real payload's statewide totalUnits equals its locality
        count (UT 29, VA 133, GA 159, ID 44, WA 39, RI 40)."""
        assert ev._unit_label(_ev_payload(key)) == "counties"

    @pytest.mark.parametrize("key", sorted(TestEnhancedVotingLabelsPerState.EXPECTED))
    def test_localities_are_named_as_the_state_names_them(self, key):
        """Rhode Island's 40 units are its cities and towns (it has five
        counties); Virginia's are counties and independent cities."""
        from app.pipeline.fetch.state_candidate_sources import source_for_state

        state = key[:2]
        expected = {"RI": "cities and towns", "VA": "counties and cities"}.get(state, "counties")
        assert ev._unit_label(_ev_payload(key), source_for_state(state)) == expected

    def test_precinct_units_stay_precincts(self):
        payload = _ev_payload("UT24")
        for item in payload["ballotItems"]:
            item["reportingStatus"] = {"reportingUnits": 100, "totalUnits": 2279}
        assert ev._unit_label(payload) == "precincts"
        assert ev._unit_label({"ballotItems": [], "localityElections": []}) == "precincts"

    async def test_fetch_carries_the_unit_label(self, monkeypatch):
        TestEnhancedVotingDemoAndAmbiguity._serve(monkeypatch, _EV["indexes"]["UT"], _ev_payload("UT24"))
        got = await ev.fetch_general_results(None, date(2024, 11, 5), "UT", TestEnhancedVotingDemoAndAmbiguity.UT)
        assert got.unit_label == "counties"
        assert _by_seat(got.contests)[("H", 3)].total_units == 11

    def test_a_qualified_write_in_is_a_candidate(self):
        """Utah's 2024 Senate count lists qualified write-in Laird Fetzer
        Hamblin (isWriteIn AND isQualifiedWriteIn) with 322 real votes."""
        senate = _by_seat(ev.general_contests(_ev_payload("UT24"), _ev_patterns("UT")))[("S", None)]
        assert ("LAIRD FETZER HAMBLIN", None, 322) in senate.candidates
        assert sum(v for _, _, v in senate.candidates) == senate.total_votes

    def test_the_aggregate_write_in_line_is_not(self):
        """Virginia's per-contest "Write-In" (isWriteIn, not qualified)."""
        senate = _by_seat(ev.general_contests(_ev_payload("VA24"), _ev_patterns("VA")))[("S", None)]
        assert [n for n, _, _ in senate.candidates] == ["Timothy M. Kaine", "Hung Cao"]
        # Its votes still count toward the contest.
        assert senate.votes_counted > sum(v for _, _, v in senate.candidates)

    def test_blank_and_withdrawn_people_keep_their_votes(self):
        item = {"contestType": "Candidate", "name": [{"languageId": "en", "text": "United States Senator"}],
                "summaryResults": {"ballotOptions": [
                    {"name": [{"languageId": "en", "text": "Mary Blank"}], "voteCount": 40, "party": {"abbreviation": "DEM"}},
                    {"name": [{"languageId": "en", "text": "Jo Doe (Withdrawn)"}], "voteCount": 7, "party": {"abbreviation": "REP"}},
                    {"name": [{"languageId": "en", "text": "Blank Votes"}], "voteCount": 3},
                    {"name": [{"languageId": "en", "text": "Write-In"}], "voteCount": 2},
                ]}}
        [contest] = ev.general_contests({"ballotItems": [item]})
        assert contest.candidates == [("Mary Blank", "D", 40), ("Jo Doe", "R", 7)]


class TestUnnumberedHouseCollision:
    async def test_several_district_less_house_contests_are_dropped_not_merged(self, monkeypatch):
        contests = [
            election_results.ContestCount("S", None, [("A", "D", 1)]),
            election_results.ContestCount("H", None, [("B", "D", 1)]),
            election_results.ContestCount("H", None, [("C", "R", 1)]),
        ]

        async def reader(client, day, state, source):
            return election_results.StateCount("x", None, False, contests=list(contests))

        monkeypatch.setattr(election_results, "_reader_for", lambda source: reader)
        monkeypatch.setattr(election_results, "source_for_state", lambda s: {"strategy": "x"})
        got = await election_results.fetch_state_count(None, "UT", date(2026, 11, 3))
        assert [(c.office, c.district) for c in got.contests] == [("S", None)]

    async def test_one_at_large_seat_is_kept(self, monkeypatch):
        contests = [election_results.ContestCount("H", None, [("B", "R", 1)])]

        async def reader(client, day, state, source):
            return election_results.StateCount("x", None, False, contests=list(contests))

        monkeypatch.setattr(election_results, "_reader_for", lambda source: reader)
        monkeypatch.setattr(election_results, "source_for_state", lambda s: {"strategy": "x"})
        got = await election_results.fetch_state_count(None, "WY", date(2026, 11, 3))
        assert len(got.contests) == 1


# --- Clarity: special contests, landing-page discovery ---------------------

class TestClaritySpecialSenate:
    def test_regular_and_special_senate_in_one_state_stay_apart(self):
        summary = {"Contests": [
            {"C": "United States Senator", "CH": ["Jane Roe", "Rick Poe"], "V": [10, 9], "P": ["DEM", "REP"], "TP": 5, "PR": 2},
            {"C": "United States Senator - Unexpired Term", "CH": ["Amy Loe", "Bo Moe"], "V": [7, 8], "P": ["DEM", "REP"], "TP": 5, "PR": 2},
        ]}
        regular, special = clarity.general_contests(summary)
        assert (regular.is_special, special.is_special) == (False, True)
        # A candidate's name can never flip the flag: "Special" as a surname.
        summary["Contests"][0]["CH"] = ["Jane Special", "Rick Poe"]
        assert clarity.general_contests(summary)[0].is_special is False

    def test_blank_named_and_withdrawn_people_are_kept(self):
        summary = {"Contests": [{"C": "United States Senator", "CH": ["John Blank", "Ann Lee (Withdrawn)", "Blank", "Write-In"],
                                 "V": [5, 4, 3, 2], "P": ["DEM", "REP", "", ""]}]}
        [contest] = clarity.general_contests(summary)
        assert contest.candidates == [("John Blank", "D", 5), ("Ann Lee", "R", 4)]


class TestClarityLandingPage:
    DISCOVERY = {"mode": "landing_page", "page_url": "https://sc.example/results/",
                 "link_regex": r"enr-scvotes\.org/SC/(\d+)/"}
    SETTINGS_OK = {"istestmode": False, "electiondate": "11/5/2024"}

    def _serve(self, monkeypatch, page, dates, settings=None, seen=None):
        """`dates` maps EID -> (date, name). Only 122436 carries a sum.json."""
        summary = {"Contests": []}

        async def fake_get(client, url, label):
            if seen is not None:
                seen.append(url)
            if url == self.DISCOVERY["page_url"]:
                return SimpleNamespace(text=page)
            eid = url.split("/SC/")[1].split("/")[0]
            if url.endswith("current_ver.txt"):
                return SimpleNamespace(text="100")
            if url.endswith("sum.json"):
                return SimpleNamespace(json=lambda: summary, headers={})
            day, name = dates[eid]
            details = {"electiondate": day, "internalname": name, **(settings or {})}
            return SimpleNamespace(json=lambda: {"settings": {"electiondetails": details}})

        monkeypatch.setattr(clarity, "_get", fake_get)

    SOURCE = {"base_url": "https://www.enr-scvotes.org", "discovery": DISCOVERY}

    async def test_eid_is_cached_only_after_a_trusted_read(self, monkeypatch):
        page = '<a href="https://www.enr-scvotes.org/SC/122436/">2024 General</a>'
        dates = {"122436": ("11/5/2024", "General")}
        self._serve(monkeypatch, page, dates, settings={"istestmode": True})
        with pytest.raises(election_results.UntrustedCount):
            await clarity.fetch_general_results(None, date(2024, 11, 5), "SC", self.SOURCE)
        assert clarity._general_eids == {}  # a test copy is never remembered

        self._serve(monkeypatch, page, dates, settings={"istestmode": False})
        got = await clarity.fetch_general_results(None, date(2024, 11, 5), "SC", self.SOURCE)
        assert got.page_url.endswith("/SC/122436/")
        assert clarity._general_eids == {("https://www.enr-scvotes.org", "SC", "2024-11-05"): "122436"}

        seen = []
        self._serve(monkeypatch, page, dates, settings={"istestmode": False}, seen=seen)
        await clarity.fetch_general_results(None, date(2024, 11, 5), "SC", self.SOURCE)
        assert self.DISCOVERY["page_url"] not in seen  # no re-walk once trusted

    async def test_a_miss_is_remembered_for_a_while_then_retried(self, monkeypatch):
        page = '<a href="https://www.enr-scvotes.org/SC/100/">2022 General</a>'
        seen = []
        self._serve(monkeypatch, page, {"100": ("11/8/2022", "General")}, seen=seen)
        now = [1000.0]
        monkeypatch.setattr(clarity.time, "monotonic", lambda: now[0])
        assert await clarity.fetch_general_results(None, date(2024, 11, 5), "SC", self.SOURCE) is None
        walks = seen.count(self.DISCOVERY["page_url"])
        now[0] += 60
        assert await clarity.fetch_general_results(None, date(2024, 11, 5), "SC", self.SOURCE) is None
        assert seen.count(self.DISCOVERY["page_url"]) == walks  # within the TTL: no walk
        now[0] += clarity._MISS_TTL_S
        await clarity.fetch_general_results(None, date(2024, 11, 5), "SC", self.SOURCE)
        assert seen.count(self.DISCOVERY["page_url"]) == walks + 1  # retried later

    async def test_the_walk_is_bounded_and_newest_first(self, monkeypatch):
        links = "".join(f'<a href="https://www.enr-scvotes.org/SC/{eid}/">Old election</a>' for eid in range(1000, 1060))
        seen = []
        dates = {str(eid): ("6/1/2010", "Primary") for eid in range(1000, 1060)}
        self._serve(monkeypatch, links, dates, seen=seen)
        assert await clarity.fetch_general_results(None, date(2024, 11, 5), "SC", self.SOURCE) is None
        probed = [u for u in seen if u.endswith("current_ver.txt")]
        assert len(probed) == clarity._MAX_WALK
        assert "/SC/1059/" in probed[0]

    def test_link_text_without_a_closing_anchor_is_empty(self):
        page = '<a href="x/SC/5/">no close ... 2024 appears later in the page'
        assert clarity._link_text(page, page.index("/SC/5/") + 6) == ""
        page = '<a href="x/SC/5/"><b>2024</b> General</a>'
        assert clarity._link_text(page, page.index("/SC/5/") + 6).endswith("2024 General")

    async def test_an_unclosed_link_does_not_count_as_naming_the_year(self, monkeypatch):
        """With find() == -1 the old slice ran to the end of the page, so a
        year anywhere after an unclosed link made it "named"."""
        page = ('<a href="https://www.enr-scvotes.org/SC/1/">Old'
                '<a href="https://www.enr-scvotes.org/SC/2/">2024 General</a>')
        seen = []
        self._serve(monkeypatch, page, {"1": ("6/1/2010", "Primary"), "2": ("11/5/2024", "General")},
                    settings={"istestmode": False}, seen=seen)
        await clarity.fetch_general_results(None, date(2024, 11, 5), "SC", self.SOURCE)
        probed = [u for u in seen if u.endswith("current_ver.txt")]
        assert "/SC/2/" in probed[0]


# --- Tally: one publication per read ----------------------------------------

async def test_tally_refuses_a_version_change_mid_read(monkeypatch):
    results = {**_json("fixtures_ar_general_results.json"), "versionID": "v1-2"}
    with pytest.raises(election_results.UntrustedCount, match="version"):
        await TestTallyRefusesTestData()._fetch(monkeypatch, results=results)


# --- TotalVote: Nebraska / New Mexico's split-span, minute stamp -----------

def test_totalvote_reads_the_split_span_minute_stamp():
    nm = ('<div class="last-updated">\n    <span>Results last updated:</span><span>9/24/2026 2:59 PM MT</span>\n</div>')
    ne = '<span>Results last updated:</span><span>6/1/2026 2:27 PM CT</span></div>'
    assert totalvote._updated_at(nm).isoformat() == "2026-09-24T20:59:00"  # MDT
    assert totalvote._updated_at(ne).isoformat() == "2026-06-01T19:27:00"  # CDT


# --- Shared: aggregate rows vs people ---------------------------------------

class TestNotAPerson:
    @pytest.mark.parametrize("label", [
        "Write-In", "  write-in ", "Write-ins", "Write-in: Not Certified", "Write-in: Not Assigned",
        "OTHER WRITE-INS", "Write-In Scattering", "Scattering", "BLANK", "Blank Votes", "Over Votes",
        "Overvotes", "Under Votes", "Undervotes", "None of these candidates", "Uncommitted", "Withdrawn",
        "Total Votes", "Ballots Cast",
    ])
    def test_aggregate_rows(self, label):
        from app.pipeline.fetch.state_candidates_common import is_not_a_person

        assert is_not_a_person(label)

    @pytest.mark.parametrize("label", [
        "John Blank", "Mary Blank-Smith", "Jane Doe (Withdrawn)", "Bryce Blankenship", "Ann Underwood",
        "Tom Overton", "Scott Uncommon",
    ])
    def test_people(self, label):
        from app.pipeline.fetch.state_candidates_common import is_not_a_person

        assert not is_not_a_person(label)


# --- Poll-close file ---------------------------------------------------------

class TestPollCloseYear:
    def test_a_stale_file_is_warned_about_once_and_still_gates(self, monkeypatch, caplog):
        from app.pipeline.fetch import poll_close

        monkeypatch.setattr(poll_close, "_cache", {"year": 2026, "states": {
            "GA": {"close": "19:00", "zone": "America/New_York"},
        }})
        monkeypatch.setattr(poll_close, "_warned_years", set())
        with caplog.at_level("WARNING", logger=poll_close.__name__):
            first = poll_close.last_poll_close("GA", date(2028, 11, 7))
            poll_close.last_poll_close("GA", date(2028, 11, 7))
        assert first.isoformat() == "2028-11-08T00:00:00"  # 7 p.m. EST
        assert sum("regenerate" in r.message for r in caplog.records) == 1

    def test_a_current_file_is_silent(self, monkeypatch, caplog):
        from app.pipeline.fetch import poll_close

        monkeypatch.setattr(poll_close, "_cache", {"year": 2026, "states": {}})
        monkeypatch.setattr(poll_close, "_warned_years", set())
        with caplog.at_level("WARNING", logger=poll_close.__name__):
            poll_close.last_poll_close("GA", date(2026, 11, 3))
        assert not caplog.records

    def test_the_checked_in_file_names_its_year(self):
        from app.pipeline.fetch import poll_close

        assert isinstance(json.loads(poll_close._PATH.read_text()).get("year"), int)


def _poll_script():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "fetch_poll_close_times.py"
    spec = importlib.util.spec_from_file_location("fetch_poll_close_times", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPollCloseScript:
    """The rows are Ballotpedia's real 2026 cells for each state."""

    def test_nebraska_split_zones_take_the_latest_instant(self):
        script = _poll_script()
        hours = "8 a.m. to 8 p.m. (Central time zone); 7 a.m. to 7 p.m. (Mountain time zone)"
        close, zone = script.last_close(hours, script.STATE_ZONES["NE"], date(2026, 11, 10))
        # 8 p.m. Central and 7 p.m. Mountain are the same instant; either
        # names it, and both are 02:00 UTC.
        from datetime import datetime
        from zoneinfo import ZoneInfo

        hh, mm = (int(x) for x in close.split(":"))
        instant = datetime(2026, 11, 3, hh, mm, tzinfo=ZoneInfo(zone))
        assert instant == datetime(2026, 11, 3, 20, 0, tzinfo=ZoneInfo("America/Chicago"))

    def test_north_dakota_range_takes_the_end_of_the_range_in_the_western_zone(self):
        script = _poll_script()
        hours = "Open between 7 a.m. to 9 a.m.; close between 7 p.m. to 9 p.m."
        assert script.last_close(hours, script.STATE_ZONES["ND"], date(2026, 11, 10)) == ("21:00", "America/Denver")

    def test_varies_is_none(self):
        script = _poll_script()
        assert script.last_close("Varies by municipality", ["America/New_York"], date(2026, 11, 10)) is None
