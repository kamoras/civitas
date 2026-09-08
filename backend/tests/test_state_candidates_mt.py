"""Tests for Montana's confirmed-general-candidate strategy
(state_candidates_mt.py).

fixtures_mt_federal_results.html is REAL — a trimmed capture of
electionresults.mt.gov's resultsSW.aspx?type=FED&map=CTY, fetched live
2026-09-06 from the real, certified 2026 primary (page title "Primary
Election - June 2, 2026", 100% precincts reporting). Trimmed to the
page's title header and its 9 real federal contest blocks (Senate R/D/L,
CD1 R/D/L, CD2 R/D/L) — outer page chrome (nav, scripts, footer) dropped,
unused by the parser. Real winners this proves out: Kurt Alme (Senate R,
3-way field), Alani Bankhead (Senate D, 5-way field), Kyle Austin
(Senate L, 2-way field), Aaron Flint (CD1 R, 4-way field), Sam Forstag
(CD1 D, 4-way field), Nick Sheedy (CD1 L, unopposed), Troy Downing (CD2
R, unopposed), Brian J Miller (CD2 D, 3-way field), Patrick McCracken
(CD2 L, unopposed).
"""

from pathlib import Path
from types import SimpleNamespace

from app.pipeline.fetch import state_candidates_mt as mt

FIXTURES = Path(__file__).parent
REAL_HTML = (FIXTURES / "fixtures_mt_federal_results.html").read_text()


def _resp(text):
    return SimpleNamespace(text=text)


def _patched(monkeypatch, html):
    async def fake(client, rl, method, url, **kw):
        return _resp(html)

    monkeypatch.setattr(mt, "fetch_with_retry", fake)


class TestPageElection:
    def test_reads_the_real_page_title(self):
        assert mt._page_election(REAL_HTML) == (2026, "2026-06-02")

    def test_a_page_with_no_matching_title_returns_none(self):
        assert mt._page_election("<html><body>nothing here</body></html>") is None


class TestContests:
    def test_finds_all_nine_real_contest_blocks(self):
        contests = mt._contests(REAL_HTML)
        assert len(contests) == 9

    def test_the_real_senate_field_splits_into_three_single_party_blocks(self):
        contests = mt._contests(REAL_HTML)
        senate = [c for c in contests if c[0] == "S"]
        assert len(senate) == 3
        parties = sorted({cand[1] for _, _, cands in senate for cand in cands})
        assert parties == ["D", "L", "R"]

    def test_the_total_votes_row_is_never_treated_as_a_candidate(self):
        # Real page shape: a "total votes" summary row shares the same
        # "section group" class as each candidate row but has no
        # display-results-box-d (name/party) element at all.
        for _office, _district, candidates in mt._contests(REAL_HTML):
            names = [c[0] for c in candidates]
            assert "total votes" not in [n.lower() for n in names]

    def test_house_districts_are_read_from_the_real_office_labels(self):
        contests = mt._contests(REAL_HTML)
        districts = sorted({d for office, d, _ in contests if office == "H"})
        assert districts == [1, 2]


class TestFetchConfirmedCandidates:
    async def test_real_primary_resolves_to_the_real_certified_winners(self, monkeypatch):
        _patched(monkeypatch, REAL_HTML)
        result = await mt.fetch_confirmed_candidates(None, 2026, "MT", {})
        assert {"office": "S", "district": None, "party": "R", "last_name": "ALME"} in result
        assert {"office": "S", "district": None, "party": "D", "last_name": "BANKHEAD"} in result
        assert {"office": "S", "district": None, "party": "L", "last_name": "AUSTIN"} in result
        assert {"office": "H", "district": 1, "party": "R", "last_name": "FLINT"} in result
        assert {"office": "H", "district": 1, "party": "D", "last_name": "FORSTAG"} in result
        assert {"office": "H", "district": 1, "party": "L", "last_name": "SHEEDY"} in result
        assert {"office": "H", "district": 2, "party": "R", "last_name": "DOWNING"} in result
        assert {"office": "H", "district": 2, "party": "D", "last_name": "MILLER"} in result
        assert {"office": "H", "district": 2, "party": "L", "last_name": "MCCRACKEN"} in result
        assert len(result) == 9

    async def test_unopposed_candidates_still_confirm(self, monkeypatch):
        # Troy Downing (CD2 R) and Nick Sheedy (CD1 L) both ran unopposed
        # in their party's primary -- pick_nominee must still confirm a
        # single real choice, not treat "nobody to rank against" as
        # nothing to confirm.
        _patched(monkeypatch, REAL_HTML)
        result = await mt.fetch_confirmed_candidates(None, 2026, "MT", {})
        assert {"office": "H", "district": 2, "party": "R", "last_name": "DOWNING"} in result
        assert {"office": "H", "district": 1, "party": "L", "last_name": "SHEEDY"} in result

    async def test_a_page_still_on_the_prior_cycle_confirms_nothing_yet(self, monkeypatch):
        # Real risk this guards against: the site not having rolled over
        # to the requested cycle yet must read as "not published yet",
        # not silently confirm the prior cycle's winners under the new
        # year's label.
        _patched(monkeypatch, REAL_HTML)
        result = await mt.fetch_confirmed_candidates(None, 2028, "MT", {})
        assert result == []

    async def test_not_yet_settled_confirms_nothing(self, monkeypatch):
        # Constructed: a page reporting an election held "today" (via a
        # future-proof relative construction) is never far enough past
        # for _settled to clear, regardless of the real fixture's actual
        # 2026-06-02 date.
        from datetime import UTC, datetime

        today = datetime.now(UTC).date()
        recent_html = REAL_HTML.replace(
            "Primary Election - June 2, 2026",
            f"Primary Election - {today.strftime('%B %-d, %Y')}",
        )
        _patched(monkeypatch, recent_html)
        result = await mt.fetch_confirmed_candidates(None, today.year, "MT", {})
        assert result == []

    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(mt, "fetch_with_retry", fake)
        assert await mt.fetch_confirmed_candidates(None, 2026, "MT", {}) is None

    async def test_a_malformed_title_returns_none(self, monkeypatch):
        _patched(monkeypatch, "<html><body>site redesigned, no title here</body></html>")
        assert await mt.fetch_confirmed_candidates(None, 2026, "MT", {}) is None

    async def test_settle_days_override_from_config_is_honored(self, monkeypatch):
        # An absurdly high override must still withhold a real, otherwise-
        # confirmable field -- proves the config value actually reaches
        # _settled rather than always falling back to the default.
        _patched(monkeypatch, REAL_HTML)
        result = await mt.fetch_confirmed_candidates(None, 2026, "MT", {"settle_days": 36500})
        assert result == []
