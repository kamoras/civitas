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
"""

from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_al as al

_FIXTURE = (Path(__file__).parent / "fixtures_al_special_primary_results.html").read_text()


class TestVotes:
    def test_parses_thousands_separators(self):
        assert al._votes("23,325") == 23325

    def test_non_numeric_contributes_nothing_rather_than_raising(self):
        assert al._votes("n/a") == 0
        assert al._votes("") == 0


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

    def test_a_totals_rows_vote_cell_is_not_mistaken_for_a_candidate(self):
        # Every real contest's candidate votes sum to less than the total
        # ballots cast in that contest would suggest if a totals row were
        # being double-counted as an extra candidate.
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

    def test_a_page_with_no_contests_yields_an_empty_dict(self):
        assert self._parse("<html><body>no results yet</body></html>") == {}


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    async def test_real_page_resolves_to_the_real_winners(self, monkeypatch):
        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            class _Resp:
                text = _FIXTURE
            return _Resp()

        monkeypatch.setattr(al, "fetch_with_retry", fake_fetch_with_retry)
        result = await al.fetch_confirmed_candidates(None, 2026, "AL", {})
        assert sorted(
            (r["office"], r["district"], r["party"], r["last_name"]) for r in result
        ) == [
            ("H", 1, "R", "Carl"),
            ("H", 2, "R", "Marques"),
            ("H", 6, "D", "Mercer"),
            ("H", 6, "R", "Palmer"),
            ("H", 7, "R", "Akin"),
        ]

    async def test_a_crowded_field_only_keeps_the_plurality_winner(self, monkeypatch):
        # CD1 REP is a real 4-candidate field (Burger, Carl, Mills,
        # Sidwell) with no runoff by law -- only Carl's real 74.70%
        # plurality survives.
        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            class _Resp:
                text = _FIXTURE
            return _Resp()

        monkeypatch.setattr(al, "fetch_with_retry", fake_fetch_with_retry)
        result = await al.fetch_confirmed_candidates(None, 2026, "AL", {})
        cd1 = [r for r in result if r["district"] == 1]
        assert cd1 == [{"office": "H", "district": 1, "party": "R", "last_name": "Carl"}]

    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake(*a, **kw):
            return None

        monkeypatch.setattr(al, "fetch_with_retry", fake)
        assert await al.fetch_confirmed_candidates(None, 2026, "AL", {}) is None

    async def test_a_page_with_no_parsable_contests_returns_none(self, monkeypatch):
        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            class _Resp:
                text = "<html><body>Results Coming Soon</body></html>"
            return _Resp()

        monkeypatch.setattr(al, "fetch_with_retry", fake_fetch_with_retry)
        assert await al.fetch_confirmed_candidates(None, 2026, "AL", {}) is None
