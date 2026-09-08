"""Tests for the "TotalVote" vendor strategy (state_candidates_totalvote.py),
shared by Montana and Nebraska.

fixtures_mt_federal_results.html is REAL -- a trimmed capture of
electionresults.mt.gov's resultsSW.aspx?type=FED&map=CTY, fetched live
2026-09-06 from the real, certified 2026 primary (page title "Primary
Election - June 2, 2026", 100% precincts reporting). Trimmed to the
page's title header and its 9 real federal contest blocks (Senate R/D/L,
CD1 R/D/L, CD2 R/D/L) -- outer page chrome (nav, scripts, footer) dropped,
unused by the parser. Real winners this proves out: Kurt Alme (Senate R,
3-way field), Alani Bankhead (Senate D, 5-way field), Kyle Austin
(Senate L, 2-way field), Aaron Flint (CD1 R, 4-way field), Sam Forstag
(CD1 D, 4-way field), Nick Sheedy (CD1 L, unopposed), Troy Downing (CD2
R, unopposed), Brian J Miller (CD2 D, 3-way field), Patrick McCracken
(CD2 L, unopposed).

fixtures_ne_statewide_results.html / fixtures_ne_congressional_results.html
are REAL -- trimmed captures of electionresults.nebraska.gov's
resultsSW.aspx?type=SW&map=CTY and ?type=CG&map=DIST, fetched live
2026-09-07 from the real, current 2026 primary (page title "Primary
Election May 12, 2026" -- no hyphen, unlike Montana's title). The
statewide page mixes Senate in with non-federal offices (Governor,
Secretary of State, ...) that parse_office must drop; the congressional
page carries all 3 House districts. Real winners this proves out: Pete
Ricketts (Senate R, 5-way field), Cindy Burbank (Senate D, 2-way field),
Mike Flood (CD1 R, unopposed), Chris Backemeyer (CD1 D, 2-way field),
Nik Sandman (CD1 L, unopposed), Brinker Harding (CD2 R, unopposed),
Denise Powell (CD2 D, close 7-way field), Eric Michael Foreman (CD2 L,
unopposed), Adrian Smith (CD3 R, 2-way field), Becky Kelly Stille (CD3
D, unopposed). Real "Legal Marijuana Now" candidates on the Senate and
CD3 ballots are silently dropped -- normalize_party doesn't recognise
that label, matching this system's existing behaviour elsewhere.
"""

from pathlib import Path
from types import SimpleNamespace

from app.pipeline.fetch import state_candidates_totalvote as tv

FIXTURES = Path(__file__).parent
MT_HTML = (FIXTURES / "fixtures_mt_federal_results.html").read_text()
NE_SW_HTML = (FIXTURES / "fixtures_ne_statewide_results.html").read_text()
NE_CG_HTML = (FIXTURES / "fixtures_ne_congressional_results.html").read_text()

MT_SOURCE = {
    "base_url": "https://electionresults.mt.gov",
    "queries": [{"type": "FED", "map": "CTY"}],
}
NE_SOURCE = {
    "base_url": "https://electionresults.nebraska.gov",
    "queries": [{"type": "SW", "map": "CTY"}, {"type": "CG", "map": "DIST"}],
}


def _resp(text):
    return SimpleNamespace(text=text)


def _patched_single(monkeypatch, html):
    async def fake(client, rl, method, url, **kw):
        return _resp(html)

    monkeypatch.setattr(tv, "fetch_with_retry", fake)


def _patched_by_type(monkeypatch, by_type):
    async def fake(client, rl, method, url, **kw):
        for type_param, html in by_type.items():
            if f"type={type_param}" in url:
                return _resp(html)
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(tv, "fetch_with_retry", fake)


class TestPageElection:
    def test_reads_the_real_montana_title(self):
        assert tv._page_election(MT_HTML) == (2026, "2026-06-02")

    def test_reads_the_real_nebraska_title_with_no_hyphen(self):
        assert tv._page_election(NE_SW_HTML) == (2026, "2026-05-12")

    def test_a_page_with_no_matching_title_returns_none(self):
        assert tv._page_election("<html><body>nothing here</body></html>") is None


class TestContests:
    def test_finds_all_nine_real_montana_contest_blocks(self):
        assert len(tv._contests(MT_HTML)) == 9

    def test_the_real_montana_senate_field_splits_into_three_single_party_blocks(self):
        senate = [c for c in tv._contests(MT_HTML) if c[0] == "S"]
        assert len(senate) == 3
        parties = sorted({cand[1] for _, _, cands in senate for cand in cands})
        assert parties == ["D", "L", "R"]

    def test_the_total_votes_row_is_never_treated_as_a_candidate(self):
        # Real page shape: a "total votes" summary row shares the same
        # "section group" class as each candidate row but has no
        # display-results-box-d (name/party) element at all.
        for _office, _district, candidates in tv._contests(MT_HTML):
            names = [c[0] for c in candidates]
            assert "total votes" not in [n.lower() for n in names]

    def test_montana_house_districts_are_read_from_the_real_office_labels(self):
        districts = sorted({d for office, d, _ in tv._contests(MT_HTML) if office == "H"})
        assert districts == [1, 2]

    def test_nebraskas_non_federal_statewide_offices_are_dropped(self):
        # The real SW page mixes Senate in with Governor, Secretary of
        # State, Treasurer, Attorney General and Auditor -- only the
        # federal one should survive parse_office.
        offices = {c[0] for c in tv._contests(NE_SW_HTML)}
        assert offices == {"S"}

    def test_nebraskas_three_real_house_districts_are_all_found(self):
        districts = sorted({d for office, d, _ in tv._contests(NE_CG_HTML) if office == "H"})
        assert districts == [1, 2, 3]

    def test_an_unrecognised_party_label_is_silently_dropped(self):
        # Real shape: Nebraska's 2026 Senate ballot also carried a "Legal
        # Marijuana Now" field that normalize_party doesn't recognise --
        # that whole block must vanish, not surface with party=None.
        senate = [c for c in tv._contests(NE_SW_HTML) if c[0] == "S"]
        parties = sorted({cand[1] for _, _, cands in senate for cand in cands})
        assert parties == ["D", "R"]

    def test_a_write_in_row_is_excluded(self):
        # Constructed, self-contained (not spliced into the real
        # fixtures, which use CRLF line endings that make byte-exact
        # string surgery fragile): ENR platforms commonly render a
        # write-in tally with the exact same markup as a real candidate
        # row -- not present in either state's real 2026 federal field,
        # so this is a defensive-guard test, matching the real DOM shape
        # confirmed against both real fixtures.
        html = """
        <div class="wrapper-inside wrapper-border">
            <div class="display-results-box-a"><h1>UNITED STATES SENATOR</h1></div>
            <div class="section group">
                <div class="col display-results-box-d"><h1>KURT ALME</h1><h2 class="Republican">Republican</h2></div>
                <div class="col display-results-box-f"><h1>128,716</h1></div>
            </div>
            <div class="section group">
                <div class="col display-results-box-d"><h1>WRITE-IN</h1><h2 class="Republican">Republican</h2></div>
                <div class="col display-results-box-f"><h1>12</h1></div>
            </div>
            <div class="section group">
                <div class="col display-results-box-totalvotes"><h2>total votes</h2></div>
                <div class="col display-results-box-total"><h2>128,728</h2></div>
            </div>
        </div>
        """
        contests = tv._contests(html)
        assert len(contests) == 1
        names = [name for name, _, _ in contests[0][2]]
        assert names == ["ALME"]


class TestFetchConfirmedCandidatesMontana:
    async def test_real_primary_resolves_to_the_real_certified_winners(self, monkeypatch):
        _patched_single(monkeypatch, MT_HTML)
        result = await tv.fetch_confirmed_candidates(None, 2026, "MT", MT_SOURCE)
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
        _patched_single(monkeypatch, MT_HTML)
        result = await tv.fetch_confirmed_candidates(None, 2026, "MT", MT_SOURCE)
        assert {"office": "H", "district": 2, "party": "R", "last_name": "DOWNING"} in result
        assert {"office": "H", "district": 1, "party": "L", "last_name": "SHEEDY"} in result

    async def test_a_page_still_on_the_prior_cycle_confirms_nothing_yet(self, monkeypatch):
        # Real risk this guards against: the site not having rolled over
        # to the requested cycle yet must read as "not published yet",
        # not silently confirm the prior cycle's winners under the new
        # year's label.
        _patched_single(monkeypatch, MT_HTML)
        result = await tv.fetch_confirmed_candidates(None, 2028, "MT", MT_SOURCE)
        assert result == []

    async def test_a_page_that_has_moved_on_to_the_general_confirms_nothing_but_does_not_fail(self, monkeypatch):
        # The bug this guards against: once a state's site rolls over to
        # the November general on this same no-eid URL (which this
        # module's own docstring says it will), the title no longer
        # contains "Primary Election" -- that must read as a healthy
        # "nothing to confirm from this stage", not the same
        # fetch_failed status a genuine site redesign gets, which would
        # otherwise fire on every run forever after a normal, expected
        # rollover.
        general_html = "<html><body><h2>General Election - November 3, 2026</h2></body></html>"
        _patched_single(monkeypatch, general_html)
        result = await tv.fetch_confirmed_candidates(None, 2026, "MT", MT_SOURCE)
        assert result == []

    async def test_not_yet_settled_confirms_nothing(self, monkeypatch):
        # Constructed: a page reporting an election held "today" (via a
        # future-proof relative construction) is never far enough past
        # for _settled to clear, regardless of the real fixture's actual
        # 2026-06-02 date.
        from datetime import UTC, datetime

        today = datetime.now(UTC).date()
        recent_html = MT_HTML.replace(
            "Primary Election - June 2, 2026",
            f"Primary Election - {today.strftime('%B %-d, %Y')}",
        )
        _patched_single(monkeypatch, recent_html)
        result = await tv.fetch_confirmed_candidates(None, today.year, "MT", {**MT_SOURCE, "settle_days": 45})
        assert result == []

    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(tv, "fetch_with_retry", fake)
        assert await tv.fetch_confirmed_candidates(None, 2026, "MT", MT_SOURCE) is None

    async def test_a_malformed_title_returns_none(self, monkeypatch):
        _patched_single(monkeypatch, "<html><body>site redesigned, no title here</body></html>")
        assert await tv.fetch_confirmed_candidates(None, 2026, "MT", MT_SOURCE) is None

    async def test_settle_days_override_from_config_is_honored(self, monkeypatch):
        # An absurdly high override must still withhold a real, otherwise-
        # confirmable field -- proves the config value actually reaches
        # _settled rather than always falling back to the default.
        _patched_single(monkeypatch, MT_HTML)
        result = await tv.fetch_confirmed_candidates(None, 2026, "MT", {**MT_SOURCE, "settle_days": 36500})
        assert result == []


class TestFetchConfirmedCandidatesNebraska:
    async def test_real_primary_resolves_to_the_real_current_winners(self, monkeypatch):
        # Unlike Montana's single query, Nebraska splits federal offices
        # across two (SW for Senate, CG for House) that must be fetched
        # and merged -- this proves both actually get requested and
        # combined, not just whichever query happens first.
        _patched_by_type(monkeypatch, {"SW": NE_SW_HTML, "CG": NE_CG_HTML})
        result = await tv.fetch_confirmed_candidates(None, 2026, "NE", NE_SOURCE)
        assert {"office": "S", "district": None, "party": "R", "last_name": "Ricketts"} in result
        assert {"office": "S", "district": None, "party": "D", "last_name": "Burbank"} in result
        assert {"office": "H", "district": 1, "party": "R", "last_name": "Flood"} in result
        assert {"office": "H", "district": 1, "party": "D", "last_name": "Backemeyer"} in result
        assert {"office": "H", "district": 1, "party": "L", "last_name": "Sandman"} in result
        assert {"office": "H", "district": 2, "party": "R", "last_name": "Harding"} in result
        assert {"office": "H", "district": 2, "party": "D", "last_name": "Powell"} in result
        assert {"office": "H", "district": 2, "party": "L", "last_name": "Foreman"} in result
        assert {"office": "H", "district": 3, "party": "R", "last_name": "Smith"} in result
        assert {"office": "H", "district": 3, "party": "D", "last_name": "Stille"} in result
        assert len(result) == 10

    async def test_a_close_real_field_still_resolves_to_the_true_plurality_winner(self, monkeypatch):
        # CD2's real Democratic field was a genuinely close 7-way race
        # (Powell 22,516 vs runner-up Cavanaugh 21,115) -- a fragile
        # "biggest number in the block" implementation could be right by
        # accident on Montana's more lopsided fields but wrong here.
        _patched_by_type(monkeypatch, {"SW": NE_SW_HTML, "CG": NE_CG_HTML})
        result = await tv.fetch_confirmed_candidates(None, 2026, "NE", NE_SOURCE)
        assert {"office": "H", "district": 2, "party": "D", "last_name": "Powell"} in result

    async def test_one_query_failing_fails_the_whole_fetch(self, monkeypatch):
        # If either half of Nebraska's two-query fetch fails, the result
        # is incomplete (e.g. Senate with no House) -- must read as a
        # real fetch failure, never a partial confirm.
        async def fake(client, rl, method, url, **kw):
            if "type=SW" in url:
                return _resp(NE_SW_HTML)
            return None

        monkeypatch.setattr(tv, "fetch_with_retry", fake)
        assert await tv.fetch_confirmed_candidates(None, 2026, "NE", NE_SOURCE) is None
