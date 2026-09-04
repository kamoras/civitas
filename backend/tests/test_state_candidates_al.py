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
