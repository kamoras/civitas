"""Tests for Alabama's confirmed-general-candidate strategy
(state_candidates_al.py).

fixtures_al_special_primary_results.html is REAL — the whole
`<table id="dlstContest">...</table>` element off Alabama's own
election-night results page (ecode=1001300), fetched live 2026-09-03 from
the real, certified-by-count 2026-08-11 special primary. Kept whole rather
than trimmed: it's what proves the parser correctly skips the un-contested
CD1/CD2/CD7 Democratic sections (they simply don't exist on the real page —
Democrats fielded no candidate there) and correctly ignores a totals row
whose vote-count cell reuses the same CSS class as a real candidate's, with
no name cell before it.

_votes() itself is not retested here — it's imported straight from
state_candidates_tabular.py, whose own test file already covers it.
"""

from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_al as al

_FIXTURE = (Path(__file__).parent / "fixtures_al_special_primary_results.html").read_text()
_SOURCE = {"ecode": 1001300}


class TestContestResultsParser:
    def _parse(self, html: str) -> dict:
        parser = al._ContestResultsParser()
        parser.feed(html)
        return parser.contests

    def test_real_page_finds_every_real_contest(self):
        contests = self._parse(_FIXTURE)
        assert set(contests) == {
            "UNITED STATES REPRESENTATIVE, 1ST CONGRESSIONAL DISTRICT (REP)",
            "UNITED STATES REPRESENTATIVE, 2ND CONGRESSIONAL DISTRICT (REP)",
            "UNITED STATES REPRESENTATIVE, 6TH CONGRESSIONAL DISTRICT (DEM)",
            "UNITED STATES REPRESENTATIVE, 6TH CONGRESSIONAL DISTRICT (REP)",
            "UNITED STATES REPRESENTATIVE, 7TH CONGRESSIONAL DISTRICT (REP)",
        }

    def test_real_candidate_and_vote_count(self):
        contests = self._parse(_FIXTURE)
        cd1 = contests["UNITED STATES REPRESENTATIVE, 1ST CONGRESSIONAL DISTRICT (REP)"]
        assert ("Jerry Carl                             (REP)", 23325) in cd1

    def test_a_district_with_no_democratic_candidate_has_no_democratic_section(self):
        # Real: Democrats fielded no candidate in CD1, CD2 or CD7 — those
        # sections simply don't exist on the page, rather than existing
        # empty, and the parser must not invent one.
        contests = self._parse(_FIXTURE)
        assert "UNITED STATES REPRESENTATIVE, 1ST CONGRESSIONAL DISTRICT (DEM)" not in contests

    def test_the_column_labels_row_is_not_mistaken_for_a_candidate(self):
        # Real: every contest's candidate rows are preceded by a labels
        # row ("enrCandidatesHeader enrCandNameCol", holding only &nbsp;)
        # that shares the bare "CandNameCol" substring with a real
        # candidate row's own class ("enrCandidateListItemCol
        # enrCandNameCol") — only the latter also carries
        # "CandidateListItemCol". If that weren't required, this fixture
        # would show an extra empty-named "candidate" ahead of the first
        # real one in every contest.
        contests = self._parse(_FIXTURE)
        cd1 = contests["UNITED STATES REPRESENTATIVE, 1ST CONGRESSIONAL DISTRICT (REP)"]
        assert all(name.strip() for name, _ in cd1)

    def test_a_totals_rows_vote_cell_is_not_mistaken_for_a_candidate(self):
        html = """
        <table>
        <td class="enrContestHeader">UNITED STATES REPRESENTATIVE, 9TH CONGRESSIONAL DISTRICT (REP)</td>
        <tr><td class="enrCandidateListItemCol enrCandNameCol">Alpha Jones (REP)</td></tr>
        <tr><td class="enrCandidateListItemCol enrCandVoteNumCol"><div>500</div></td></tr>
        <tr><td class="enrTotalsCol enrCandVoteNumCol"><div>500</div></td></tr>
        </table>
        """
        contests = self._parse(html)
        cd9 = contests["UNITED STATES REPRESENTATIVE, 9TH CONGRESSIONAL DISTRICT (REP)"]
        assert cd9 == [("Alpha Jones (REP)", 500)]

    def test_a_name_with_no_votes_row_does_not_leak_into_the_next_contest(self):
        # A candidate name captured just before the page is truncated (or
        # a still-tabulating precinct simply has no votes cell yet) must
        # not survive past the NEXT contest's header and get attributed
        # to that contest's first real vote count.
        html = """
        <table>
        <td class="enrContestHeader">UNITED STATES REPRESENTATIVE, 8TH CONGRESSIONAL DISTRICT (REP)</td>
        <tr><td class="enrCandidateListItemCol enrCandNameCol">Stale Candidate (REP)</td></tr>
        <td class="enrContestHeader">UNITED STATES REPRESENTATIVE, 9TH CONGRESSIONAL DISTRICT (REP)</td>
        <tr><td class="enrCandidateListItemCol enrCandVoteNumCol"><div>9999</div></td></tr>
        </table>
        """
        contests = self._parse(html)
        assert contests["UNITED STATES REPRESENTATIVE, 8TH CONGRESSIONAL DISTRICT (REP)"] == []
        assert contests["UNITED STATES REPRESENTATIVE, 9TH CONGRESSIONAL DISTRICT (REP)"] == []

    def test_a_page_with_no_contests_yields_an_empty_dict(self):
        assert self._parse("<html><body>no results yet</body></html>") == {}

    def test_a_nested_matching_class_td_does_not_hijack_an_in_progress_capture(self):
        # A matching-class td can only ever directly hold text on this
        # page (never another td nested inside it) — but if some future
        # markup variant did nest one, it must not steal the buffer the
        # OUTER td is still filling, and the outer td's real close (not
        # the nested one's) must be what ends the capture. The nested
        # content ends up folded into the outer text rather than parsed
        # as its own vote count — garbled, but not silently lost or
        # misattributed to some other contest.
        html = """
        <table>
        <td class="enrContestHeader">UNITED STATES REPRESENTATIVE, 5TH CONGRESSIONAL DISTRICT (REP)</td>
        <tr><td class="enrCandidateListItemCol enrCandNameCol">Outer Name<td class="enrCandidateListItemCol enrCandVoteNumCol">42</td> tail (REP)</td></tr>
        </table>
        """
        contests = self._parse(html)
        cd5 = contests["UNITED STATES REPRESENTATIVE, 5TH CONGRESSIONAL DISTRICT (REP)"]
        assert cd5 == []


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    async def test_real_page_resolves_to_the_real_winners(self, monkeypatch):
        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            class _Resp:
                text = _FIXTURE
            return _Resp()

        monkeypatch.setattr(al, "fetch_with_retry", fake_fetch_with_retry)
        result = await al.fetch_confirmed_candidates(None, 2026, "AL", _SOURCE)
        # CD1 REP is a real 4-candidate field (Burger, Carl, Mills,
        # Sidwell) with no runoff by law, and CD2 REP a real 6-candidate
        # field — this list already proves only each real plurality
        # winner survives, not just that a winner exists.
        assert sorted(
            (r["office"], r["district"], r["party"], r["last_name"]) for r in result
        ) == [
            ("H", 1, "R", "Carl"),
            ("H", 2, "R", "Marques"),
            ("H", 6, "D", "Mercer"),
            ("H", 6, "R", "Palmer"),
            ("H", 7, "R", "Akin"),
        ]

    async def test_a_cycle_other_than_the_verified_one_confirms_nobody(self, monkeypatch):
        # ecode=1001300 names one specific 2026 election with no date of
        # its own; a later cycle asking this strategy for candidates must
        # not get 2026's winners back just because nothing refuses them.
        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            class _Resp:
                text = _FIXTURE
            return _Resp()

        monkeypatch.setattr(al, "fetch_with_retry", fake_fetch_with_retry)
        assert await al.fetch_confirmed_candidates(None, 2028, "AL", _SOURCE) == []

    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake(*a, **kw):
            return None

        monkeypatch.setattr(al, "fetch_with_retry", fake)
        assert await al.fetch_confirmed_candidates(None, 2026, "AL", _SOURCE) is None

    async def test_a_page_with_no_parsable_contests_returns_none(self, monkeypatch):
        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            class _Resp:
                text = "<html><body>Results Coming Soon</body></html>"
            return _Resp()

        monkeypatch.setattr(al, "fetch_with_retry", fake_fetch_with_retry)
        assert await al.fetch_confirmed_candidates(None, 2026, "AL", _SOURCE) is None


# ── Statewide offices: the official primary and runoff precinct results ──
#
# fixtures_al_primary_precincts_2026.zip and fixtures_al_runoff_precincts_
# 2026.zip are REAL: two of the 67 county workbooks (Bullock, Lowndes),
# byte-for-byte, out of the Secretary of State's own 2026_Primary_Election.
# zip and 2026_PRIMARY_RUNOFF_ELECTION.zip, fetched live 2026-09-28 from
# sos.alabama.gov/alabama-votes/voter/election-data. Two counties are not
# the state, so resolution is tested separately below on the REAL
# statewide totals those zips sum to over all 67 counties.

_PRIMARY_ZIP = (Path(__file__).parent / "fixtures_al_primary_precincts_2026.zip").read_bytes()
_RUNOFF_ZIP = (Path(__file__).parent / "fixtures_al_runoff_precincts_2026.zip").read_bytes()
_EXCLUDE = {"Under Votes", "Over Votes"}

_PAGE = """
<a href="/sites/default/files/election-data/2026-07/2026_PRIMARY_RUNOFF_ELECTION.zip">runoff</a>
<a href="/sites/default/files/election-data/2026-07/2026_Primary_Election.zip">primary</a>
<a href="/sites/default/files/election-data/2026-06/2026%20AL%20Republican%20Party%20Primary%20Precinct%20Results.zip">party pdfs</a>
"""
_STATE_OFFICES = {
    "page_url": "https://www.sos.alabama.gov/alabama-votes/voter/election-data",
    "primary_link_regex": 'href="([^"]*/election-data/{year}-\\d{2}/{year}_Primary_Election\\.zip)"',
    "runoff_link_regex": 'href="([^"]*/election-data/{year}-\\d{2}/{year}_PRIMARY_RUNOFF_ELECTION\\.zip)"',
    "runoff_threshold_pct": 50,
    "exclude_choices": ["Under Votes", "Over Votes"],
}

# Real statewide sums over all 67 county workbooks (2026-09-28).
_STATEWIDE_PRIMARY = {
    ("GOVERNOR (REP)", "REP"): {"Thomas Tuberville": 422255, "Ken McFeeters": 47239, '"Alabama" Will Santivasci': 24494},
    ("LIEUTENANT GOVERNOR (REP)", "REP"): {
        "John Wahl": 192664, "Wes Allen": 180497, "Rick Pate": 34652, "Nicole Jones Wadsworth": 27948,
        "Pat Bishop": 15428, "Stewart Hill Tankersley": 13537, "George Childress": 10339,
    },
    ("PUBLIC SERVICE COMMISSION, PLACE 1 (DEM)", "DEM"): {"James O. Gordon": 191236, "Jeff Ramsey": 89293, "John Northrop": 50937},
    ("PUBLIC SERVICE COMMISSION, PLACE 2 (REP)", "REP"): {
        "Jim Zig Zeigler": 194062, "Chris Beeker": 106380, "Brent Woodall": 79275, "Priscilla Andrews": 53627,
    },
    ("STATE BOARD OF EDUCATION MEMBER DISTRICT 6 (REP)", "REP"): {"Cathi Bradford": 35647, "Marie Manning": 30416},
    ("MEMBER, BALDWIN COUNTY BOARD OF EDUCATION, DIST 5", "REP"): {"Jason P. Woerner": 2269, "Whitney Scapecchi": 1243},
    ("PROPOSED STATEWIDE AMENDMENT 1", ""): {"Yes": 900000, "No": 100000},
    ("STATE SENATOR, DISTRICT 10 (REP)", "REP"): {"Andrew Jones": 8325, "Amy Dozier Minton": 7141},
    ("UNITED STATES REPRESENTATIVE, 1ST CONGRESSIONAL DISTRICT (REP)", "REP"): {"Jerry Carl": 32714, "Rhett Marques": 25235},
}
_STATEWIDE_RUNOFF = {
    ("LIEUTENANT GOVERNOR (REP)", "REP"): {"John Wahl": 175911, "Wes Allen": 132890},
    ("PUBLIC SERVICE COMMISSION, PLACE 2 (REP)", "REP"): {"Jim Zig Zeigler": 152845, "Chris Beeker": 144868},
}


class TestContestTotals:
    def test_real_county_workbooks_sum_across_precincts_and_counties(self):
        totals = al.contest_totals(_PRIMARY_ZIP, _EXCLUDE)
        # Bullock + Lowndes, every precinct column plus absentee and
        # provisional, Under/Over Votes excluded.
        assert totals[("GOVERNOR (REP)", "REP")] == {
            "Ken McFeeters": 31, '"Alabama" Will Santivasci': 15, "Thomas Tuberville": 712,
        }
        assert totals[("STATE TREASURER (REP)", "REP")] == {"Young Boozer": 604, "Steve Lolley": 124}

    def test_the_runoff_file_reads_the_same_way(self):
        totals = al.contest_totals(_RUNOFF_ZIP, _EXCLUDE)
        assert totals[("ATTORNEY GENERAL (REP)", "REP")] == {"Jay Mitchell": 188, "Katherine Robertson": 279}


class TestResolveStatewide:
    def _resolved(self, runoff):
        return sorted(
            (r["office"], r["district"], r["party"], r["last_name"])
            for r in al.resolve_statewide(_STATEWIDE_PRIMARY, runoff, 50)
        )

    def test_real_2026_nominees(self):
        # A majority winner stands; Wahl (40.6%) and Zeigler (44.8%) were
        # short and are named by the runoff instead. The county school
        # board, the amendment, the legislative seat and the voided
        # pre-redistricting House primary are all refused.
        assert self._resolved(_STATEWIDE_RUNOFF) == [
            ("governor", None, "R", "Thomas Tuberville"),
            ("lt_governor", None, "R", "John Wahl"),
            ("public_service_commission", "Place 1", "D", "James O. Gordon"),
            ("public_service_commission", "Place 2", "R", "Jim Zig Zeigler"),
            ("state_board_of_education", "6", "R", "Cathi Bradford"),
        ]

    def test_a_primary_leader_short_of_a_majority_is_never_the_nominee(self):
        resolved = self._resolved({})
        assert ("lt_governor", None, "R", "John Wahl") not in resolved
        assert ("public_service_commission", "Place 2", "R", "Jim Zig Zeigler") not in resolved

    def test_a_runoff_winner_who_was_not_on_the_primary_ballot_is_refused(self):
        runoff = {("LIEUTENANT GOVERNOR (REP)", "REP"): {"Somebody Else": 9, "John Wahl": 1}}
        assert not any(r[0] == "lt_governor" for r in self._resolved(runoff))


@pytest.mark.asyncio
class TestStatewideFetch:
    def _patched(self, monkeypatch, page=_PAGE):
        async def fake_text(client, rl, url, label, **kw):
            assert url == _STATE_OFFICES["page_url"]
            return page

        async def fake_fetch(client, rl, method, url, **kw):
            if url.endswith("_Primary_Election.zip"):
                return type("R", (), {"content": _PRIMARY_ZIP})()
            if url.endswith("_PRIMARY_RUNOFF_ELECTION.zip"):
                return type("R", (), {"content": _RUNOFF_ZIP})()
            return type("R", (), {"text": _FIXTURE})()

        monkeypatch.setattr(al, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(al, "fetch_with_retry", fake_fetch)

    async def test_statewide_nominees_ride_beside_the_special_primary(self, monkeypatch):
        self._patched(monkeypatch)
        source = {**_SOURCE, "statewide_offices": True, "state_office_results": _STATE_OFFICES}
        result = await al.fetch_confirmed_candidates(None, 2026, "AL", source)
        offices = {(r["office"], r["party"], r["last_name"]) for r in result}
        assert ("H", "R", "Carl") in offices
        # In these two counties the Attorney General runoff went to
        # Robertson (279 to 188) and the Governor primary to Tuberville.
        assert ("attorney_general", "R", "Katherine Robertson") in offices
        assert ("governor", "R", "Thomas Tuberville") in offices
        assert not any(r["office"] in ("upper", "lower") for r in result)

    async def test_without_the_opt_in_no_state_office_is_read(self, monkeypatch):
        self._patched(monkeypatch)
        result = await al.fetch_confirmed_candidates(None, 2026, "AL", {**_SOURCE, "state_office_results": _STATE_OFFICES})
        assert {r["office"] for r in result} == {"H"}

    async def test_results_not_posted_yet_fail_rather_than_read_as_none(self, monkeypatch):
        # An empty list would be stored as "no statewide offices on this
        # ballot".
        self._patched(monkeypatch, page="<html>nothing yet</html>")
        source = {**_SOURCE, "statewide_offices": True, "state_office_results": _STATE_OFFICES}
        assert await al.fetch_confirmed_candidates(None, 2026, "AL", source) is None

    async def test_a_runoff_owed_but_not_posted_withholds_the_whole_read(self, monkeypatch):
        primary_only = '<a href="/sites/default/files/election-data/2026-07/2026_Primary_Election.zip">p</a>'
        self._patched(monkeypatch, page=primary_only)
        source = {**_SOURCE, "statewide_offices": True, "state_office_results": _STATE_OFFICES}
        assert await al.fetch_confirmed_candidates(None, 2026, "AL", source) is None

    async def test_another_cycle_still_reads_its_own_state_offices(self, monkeypatch):
        # The special primary's ecode is 2026-only; the precinct-results
        # links are found by year, so 2028 reads 2028's files (none here).
        self._patched(monkeypatch)
        source = {**_SOURCE, "statewide_offices": True, "state_office_results": _STATE_OFFICES}
        assert await al.fetch_confirmed_candidates(None, 2028, "AL", source) is None
