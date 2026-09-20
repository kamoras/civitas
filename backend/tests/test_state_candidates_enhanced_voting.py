"""Tests for the Enhanced Voting results-API strategy
(state_candidates_enhanced_voting.py), live on Rhode Island.

Both fixtures are REAL captures of electionresults.ri.gov, fetched live
2026-09-17 from the real, fully certified 2026 statewide primary
(isOfficialResults true, 40/40 localities reporting, lastUpdated
2026-09-15).

fixtures_ri_elections_index.json is the `elections` array off
/api/jurisdictions/rhodeisland, verbatim and untrimmed. It is kept whole
because its SHAPE is the thing under test: 10 entries spanning 2024,
2025 and 2026, NOT in date order, of which 9 are local special elections
and exactly one is this cycle's statewide primary. Discovery has to find
that one on date-year plus name, never on position.

fixtures_ri_federal_results.json is /api/elections/rhodeisland/
RI2026StatewidePrimary/data, trimmed to the election envelope plus 10 of
its 192 contests: all 6 real federal ones, and 4 of the state/party
contests that share the ballot and must be refused. Per-candidate
`groupResults` (Election Day / Early Voting / Mail Ballots splits) are
left verbatim even though the parser never reads them.

The federal winners this proves out are the real, certified ones: John
F. Reed (Senate D, 98,473 of 128,194 over Connor F. Burbridge and Luis
Daniel Muñoz), Raymond T. McKay (Senate R, unopposed), Gabriel Amo (CD1
D, unopposed), Kellie Keenan (CD1 R, unopposed), Seth Magaziner (CD2 D,
unopposed), and Victor Mellor (CD2 R, 8,837 over 6,380).

That last one is the load-bearing one, twice over: Rhode Island prints
"*" next to the party-ENDORSED candidate, and CD2's Republican primary
was won by UNendorsed Victor Mellor over endorsed "Stephen T. Skoly*" —
so the asterisk is neither a winner signal nor part of a name, and both
mistakes would be visible here.

The refusal cases are the other half of the point. Rhode Island's STATE
legislature is named the "General Assembly", so 140 of this primary's
192 contests are labelled "Senator in General Assembly District 5" or
"Representative in General Assembly District 13" — a few characters from
the federal "Senator in Congress" / "Representative in Congress District
1" on the same ballot — plus party "Senatorial District Committee"
races. Every one of them must parse as non-federal.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_enhanced_voting as ev

FIXTURES = Path(__file__).parent
INDEX = json.loads((FIXTURES / "fixtures_ri_elections_index.json").read_text())
RESULTS = json.loads((FIXTURES / "fixtures_ri_federal_results.json").read_text())

RI_SOURCE = {
    "base_url": "https://electionresults.ri.gov/results/public",
    "jurisdiction": "rhodeisland",
    "runoff_threshold_pct": None,
    "settle_days": 21,
}


def _patched(monkeypatch, index=INDEX, results=RESULTS):
    """Both GETs, answered from fixtures. `index`/`results` may be set to
    None to simulate that hop failing."""
    async def fake(client, rl, url, label, **kw):
        if "/api/jurisdictions/" in url:
            return index
        if "/data" in url:
            return results
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(ev, "fetch_json_with_retry", fake)


async def _fetch(source=RI_SOURCE, year=2026):
    return await ev.fetch_confirmed_candidates(None, year, "RI", source)


def _by_seat(records):
    return {(r["office"], r["district"], r["party"]): r["last_name"] for r in records}


class TestText:
    def test_reads_the_english_entry(self):
        assert ev._text([{"languageId": "en", "text": "2026 Statewide Primary"}]) == "2026 Statewide Primary"

    def test_prefers_english_over_document_order(self):
        """A jurisdiction that adds a second language must not flip the
        labels offices are parsed out of into it."""
        entries = [
            {"languageId": "es", "text": "Senador en el Congreso"},
            {"languageId": "en", "text": "Senator in Congress"},
        ]
        assert ev._text(entries) == "Senator in Congress"

    def test_falls_back_to_any_text_when_no_english_entry(self):
        assert ev._text([{"languageId": "es", "text": "Senador"}]) == "Senador"

    def test_missing_or_malformed_is_empty(self):
        assert ev._text(None) == ""
        assert ev._text([]) == ""
        assert ev._text(["not a dict"]) == ""


class TestIsPrimary:
    def test_finds_the_real_2026_statewide_primary(self):
        matches = [e for e in INDEX["elections"] if ev._is_primary(e, 2026)]
        assert len(matches) == 1
        assert matches[0]["publicElectionId"] == "RI2026StatewidePrimary"

    def test_the_nine_real_special_elections_are_not_primaries(self):
        """The index is mostly local specials — town councils, referenda —
        and one of them really is named "Special Election Central Falls
        City Council Ward 4 & Referendum and Special Election Senate
        District 4 Primary" (2025-07-08), carrying the word Primary for a
        purely local contest."""
        assert sum(ev._is_primary(e, 2026) for e in INDEX["elections"]) == 1
        assert sum(ev._is_primary(e, 2025) for e in INDEX["elections"]) == 0

    def test_a_special_primary_later_in_the_same_year_cannot_hijack_the_match(self):
        """The newest match wins, so without the special exclusion a
        special primary held after the statewide one would be selected —
        and its ballot carries no federal contest at all, so the state
        would silently stop confirming anyone."""
        later_special = {
            "publicElectionId": "spec26",
            "electionDate": "2026-11-10",
            "name": [{"languageId": "en", "text": "Special Election Senate District 9 Primary"}],
        }
        assert ev._is_primary(later_special, 2026) is False
        matches = [
            e for e in INDEX["elections"] + [later_special] if ev._is_primary(e, 2026)
        ]
        assert [e["publicElectionId"] for e in matches] == ["RI2026StatewidePrimary"]

    def test_a_year_with_no_primary_indexed_matches_nothing(self):
        assert not any(ev._is_primary(e, 2028) for e in INDEX["elections"])

    def test_a_presidential_primary_is_excluded(self):
        entry = {
            "electionDate": "2026-03-03",
            "name": [{"languageId": "en", "text": "2026 Presidential Preference Primary"}],
        }
        assert ev._is_primary(entry, 2026) is False


class TestFederalResults:
    @pytest.mark.asyncio
    async def test_resolves_exactly_the_six_real_federal_nominees(self, monkeypatch):
        _patched(monkeypatch)
        assert _by_seat(await _fetch()) == {
            ("S", None, "D"): "Reed",
            ("S", None, "R"): "McKay",
            ("H", 1, "D"): "Amo",
            ("H", 1, "R"): "Keenan",
            ("H", 2, "D"): "Magaziner",
            ("H", 2, "R"): "Mellor",
        }

    @pytest.mark.asyncio
    async def test_no_state_general_assembly_seat_is_ever_confirmed(self, monkeypatch):
        """The whole federal-only risk in one assertion: Rhode Island's
        state legislature is the "General Assembly", and its seats sit on
        this same ballot under labels a few characters from the federal
        ones. 140 of the real primary's 192 contests are these."""
        _patched(monkeypatch)
        records = await _fetch()
        assert len(records) == 6  # not 6 + every state seat in the fixture
        # Both state contests in the fixture are district-numbered seats
        # that would collide with real federal districts if they leaked.
        assert ("H", 13, "R") not in _by_seat(records)
        assert ("S", None, "D") in _by_seat(records)  # ... the real one still resolves

    @pytest.mark.asyncio
    async def test_the_endorsement_asterisk_is_not_part_of_the_name(self, monkeypatch):
        """"John F. Reed*" must confirm as "Reed" — "Reed*" matches no FEC
        row at all, so this failing looks like a missing nominee."""
        _patched(monkeypatch)
        assert _by_seat(await _fetch())[("S", None, "D")] == "Reed"

    @pytest.mark.asyncio
    async def test_the_endorsed_candidate_losing_is_reported_honestly(self, monkeypatch):
        """CD2's real Republican primary: unendorsed Victor Mellor (8,837)
        beat party-endorsed "Stephen T. Skoly*" (6,380). Reading the
        asterisk as a winner flag would return Skoly here."""
        _patched(monkeypatch)
        assert _by_seat(await _fetch())[("H", 2, "R")] == "Mellor"

    @pytest.mark.asyncio
    async def test_a_multi_candidate_field_resolves_to_the_top_vote_getter(self, monkeypatch):
        """Senate D is the only real contested federal field with more
        than two names: Reed 98,473 / Burbridge 17,192 / Muñoz 12,529."""
        _patched(monkeypatch)
        assert _by_seat(await _fetch())[("S", None, "D")] == "Reed"


class TestCandidates:
    def _contest(self, label):
        return next(
            bi for bi in RESULTS["ballotItems"]
            if ev._text(bi.get("name")) == label
        )

    def test_reads_the_real_senate_field(self):
        got = ev._candidates(self._contest("DEM Senator in Congress"))
        assert got == [
            ("John F. Reed*", "D", 98473),
            ("Connor F. Burbridge", "D", 17192),
            ("Luis Daniel Muñoz", "D", 12529),
        ]

    def test_write_ins_are_dropped_on_the_vendors_own_flags(self):
        contest = json.loads(json.dumps(self._contest("DEM Senator in Congress")))
        options = contest["summaryResults"]["ballotOptions"]
        options.append({**options[0], "name": [{"languageId": "en", "text": "Write-in"}],
                        "isWriteIn": True, "voteCount": 99})
        options.append({**options[0], "name": [{"languageId": "en", "text": "Qualified Write-in"}],
                        "isQualifiedWriteIn": True, "voteCount": 98})
        assert [n for n, _, _ in ev._candidates(contest)] == [
            "John F. Reed*", "Connor F. Burbridge", "Luis Daniel Muñoz",
        ]

    def test_an_unrecognised_party_is_dropped_not_bucketed(self):
        contest = json.loads(json.dumps(self._contest("DEM Senator in Congress")))
        option = contest["summaryResults"]["ballotOptions"][0]
        option["party"] = {"abbreviation": "LMN"}  # Legal Marijuana Now, real elsewhere
        assert [n for n, _, _ in ev._candidates(contest)] == [
            "Connor F. Burbridge", "Luis Daniel Muñoz",
        ]

    def test_a_non_integer_vote_count_is_dropped(self):
        contest = json.loads(json.dumps(self._contest("REP Senator in Congress")))
        contest["summaryResults"]["ballotOptions"][0]["voteCount"] = None
        assert ev._candidates(contest) == []


class TestFreshnessGate:
    @pytest.mark.asyncio
    async def test_certified_results_pass_immediately(self, monkeypatch):
        """Rhode Island certified 6 days after its 2026-09-09 primary.
        settle_days is a failsafe under the flag, never an extra AND — a
        gate wanting both would make honest certification wait anyway."""
        _patched(monkeypatch)
        assert len(await _fetch({**RI_SOURCE, "settle_days": 3650})) == 6

    @pytest.mark.asyncio
    async def test_uncertified_and_unsettled_confirms_nobody(self, monkeypatch):
        results = json.loads(json.dumps(RESULTS))
        results["election"]["isOfficialResults"] = False
        results["election"]["electionDate"] = "2099-01-01"
        _patched(monkeypatch, results=results)
        assert await _fetch() == []

    @pytest.mark.asyncio
    async def test_uncertified_but_long_settled_still_confirms(self, monkeypatch):
        """Utah's 2026 primary was certified — signed canvass published on
        this same vendor's portal — while isOfficialResults still read
        false a month later. Without this, such a state confirms nobody
        forever, which looks exactly like working code.

        The indexed date is moved to a fixed, long-past one rather than
        leaning on the fixture's own 2026-09-09: that is only days old at
        the time of writing, so this would assert nothing today and start
        asserting something different later."""
        index = json.loads(json.dumps(INDEX))
        for entry in index["elections"]:
            if entry["publicElectionId"] == "RI2026StatewidePrimary":
                entry["electionDate"] = "2026-01-06"
        results = json.loads(json.dumps(RESULTS))
        results["election"]["isOfficialResults"] = False
        _patched(monkeypatch, index=index, results=results)
        assert len(await _fetch()) == 6

    @pytest.mark.asyncio
    async def test_the_settle_window_is_measured_from_the_indexed_election_date(self, monkeypatch):
        """The date comes from the election INDEX entry, which is what
        dates the cycle — not from the results payload, which a vendor
        could re-stamp on every amendment."""
        index = json.loads(json.dumps(INDEX))
        for entry in index["elections"]:
            if entry["publicElectionId"] == "RI2026StatewidePrimary":
                entry["electionDate"] = "2026-12-31"
        results = json.loads(json.dumps(RESULTS))
        results["election"]["isOfficialResults"] = False
        _patched(monkeypatch, index=index, results=results)
        assert await _fetch(year=2026) == []


class TestDiscoveryAndFailures:
    @pytest.mark.asyncio
    async def test_a_year_not_indexed_yet_is_healthy_empty(self, monkeypatch):
        """Not the same as a failure: before a cycle's primary is posted
        there is genuinely nothing to confirm."""
        _patched(monkeypatch)
        assert await _fetch(year=2028) == []

    @pytest.mark.asyncio
    async def test_the_newest_match_wins_not_the_first_listed(self, monkeypatch):
        """The index is not date-ordered, so a cycle carrying two matching
        elections must resolve by date, never by position."""
        index = {"elections": [
            {"publicElectionId": "stale", "electionDate": "2026-03-01",
             "name": [{"languageId": "en", "text": "2026 Statewide Primary"}]},
            {"publicElectionId": "RI2026StatewidePrimary", "electionDate": "2026-09-09",
             "name": [{"languageId": "en", "text": "2026 Statewide Primary"}]},
        ]}
        seen = {}

        async def fake(client, rl, url, label, **kw):
            if "/api/jurisdictions/" in url:
                return index
            seen["url"] = url
            return RESULTS

        monkeypatch.setattr(ev, "fetch_json_with_retry", fake)
        await _fetch()
        assert "RI2026StatewidePrimary" in seen["url"]

    @pytest.mark.asyncio
    async def test_an_index_fetch_failure_is_none_not_empty(self, monkeypatch):
        _patched(monkeypatch, index=None)
        assert await _fetch() is None

    @pytest.mark.asyncio
    async def test_a_results_fetch_failure_is_none_not_empty(self, monkeypatch):
        _patched(monkeypatch, results=None)
        assert await _fetch() is None

    @pytest.mark.asyncio
    async def test_an_index_with_no_elections_array_is_a_failure(self, monkeypatch):
        _patched(monkeypatch, index={"elections": "not a list"})
        assert await _fetch() is None

    @pytest.mark.asyncio
    async def test_a_matched_election_with_no_id_is_a_failure(self, monkeypatch):
        _patched(monkeypatch, index={"elections": [
            {"electionDate": "2026-09-09",
             "name": [{"languageId": "en", "text": "2026 Statewide Primary"}]},
        ]})
        assert await _fetch() is None

    @pytest.mark.asyncio
    async def test_missing_config_is_a_failure_not_a_silent_empty(self, monkeypatch):
        _patched(monkeypatch)
        assert await _fetch({"jurisdiction": "rhodeisland"}) is None
        assert await _fetch({"base_url": "https://example.gov"}) is None
