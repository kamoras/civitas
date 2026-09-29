"""Tests for Massachusetts's confirmed-general-candidate strategy
(state_candidates_ma.py).

All fixtures are REAL data, trimmed to just the `<title>`, `<thead>`, and
`<tr class="total">` this module actually reads (fetched live 2026-09-08
from electionstats.state.ma.us), never fabricated:

fixtures_ma_house_d_district6.html -- the real, contested 2026 6th
Congressional District Democratic primary (6 real candidates: Dan Koh's
real 47,835-vote plurality win over Tram T. Nguyen, John A. Beccia III,
Jamie M. Belsito, Mariah L. Lancaster, Bethany Andres-Beck). Chosen
because it's the largest real field this module encountered, proving out
column-count alignment across many candidates at once.

fixtures_ma_house_d_district2.html -- the real, unopposed 2nd
Congressional District Democratic primary (James P. McGovern, a single
candidate column) -- proves a one-candidate contest still parses and
confirms correctly.

fixtures_ma_senate_d.html / fixtures_ma_senate_r.html -- the real 2026
U.S. Senate primaries, chosen specifically because Senate has no
district at all (statewide) -- proves district=None parsing. The real
Democratic primary's second candidate, revealed only by reading the raw
fixture (never assumed from memory), is Seth W. Moulton.

fixtures_ma_house_search.html -- a real trimmed link list (just the
`<a href="/elections/view/{id}/">` anchors, real ids) from the real 2026
U.S. House search results page, proving discovery finds every real
contested district and skips the real uncontested-with-no-filer ones
(the live page has 18 rows for 9 districts x 2 parties, only 15 of which
carry a real link).

fixtures_ma_house_search_small.html / fixtures_ma_senate_search.html --
smaller real 2-link subsets of the same real search page, used for the
end-to-end fetch_confirmed_candidates tests so they only need real
per-race fixtures this file actually has, not all 15/2 real districts.

Added 2026-09-28 for the statewide offices, fetched live from the same
archive and trimmed the same way:

fixtures_ma_all_offices_search.html -- 12 real rows, verbatim, of the
all-offices 2026 primaries search (510 rows live): Governor D/R, Auditor
D and its real "No Candidates" Republican row, Secretary of the
Commonwealth D, Governor's Council 1st D/R and 5th R, a County
Treasurer, a District Attorney, the State Senate special primary and the
U.S. Senate D row.

fixtures_ma_governor_d.html / _governor_r.html / _auditor_d.html /
_secretary_d.html / _council1_d.html / _council1_r.html /
_council5_r.html -- those rows' results pages. Council 1st R is Mary
Catherine Dormer, a write-in with 455 votes against 1,212 "All Others",
whom the archive does NOT mark as the winner; Council 5th R is William
Falcetano, a write-in it does mark.

fixtures_ma_house5_r.html -- the 5th Congressional District Republican
primary: Walter Grochowski, 13 votes against 978 "All Others", not
marked as the winner.
"""

from pathlib import Path

from app.pipeline.fetch import state_candidates_ma as mam
from app.pipeline.fetch.state_candidates_common import surname

FIXTURES = Path(__file__).parent
DISTRICT6 = (FIXTURES / "fixtures_ma_house_d_district6.html").read_text()
DISTRICT2 = (FIXTURES / "fixtures_ma_house_d_district2.html").read_text()
SENATE_D = (FIXTURES / "fixtures_ma_senate_d.html").read_text()
SENATE_R = (FIXTURES / "fixtures_ma_senate_r.html").read_text()
SEARCH_HOUSE_FULL = (FIXTURES / "fixtures_ma_house_search.html").read_text()
SEARCH_HOUSE_SMALL = (FIXTURES / "fixtures_ma_house_search_small.html").read_text()
SEARCH_SENATE = (FIXTURES / "fixtures_ma_senate_search.html").read_text()
SEARCH_ALL = (FIXTURES / "fixtures_ma_all_offices_search.html").read_text()
GOVERNOR_D = (FIXTURES / "fixtures_ma_governor_d.html").read_text()
GOVERNOR_R = (FIXTURES / "fixtures_ma_governor_r.html").read_text()
AUDITOR_D = (FIXTURES / "fixtures_ma_auditor_d.html").read_text()
SECRETARY_D = (FIXTURES / "fixtures_ma_secretary_d.html").read_text()
COUNCIL1_D = (FIXTURES / "fixtures_ma_council1_d.html").read_text()
COUNCIL1_R = (FIXTURES / "fixtures_ma_council1_r.html").read_text()
COUNCIL5_R = (FIXTURES / "fixtures_ma_council5_r.html").read_text()
HOUSE5_R = (FIXTURES / "fixtures_ma_house5_r.html").read_text()

STATEWIDE_PAGES = {
    "/172893/": GOVERNOR_D, "/172894/": GOVERNOR_R, "/172883/": AUDITOR_D,
    "/172903/": SECRETARY_D, "/172925/": COUNCIL1_D, "/172926/": COUNCIL1_R,
    "/172968/": COUNCIL5_R,
}

_REAL_HOUSE_IDS = {"172949", "172985", "172917", "172918", "173039", "173040", "173005", "173006", "172973", "172974", "173113", "172899", "172900", "172935", "172936"}


def _patched(monkeypatch, pages):
    """pages: {url_substring: html}. The first matching substring wins."""
    async def fake_text(client, rl, url, label, **kw):
        for substring, html in pages.items():
            if substring in url:
                return html
        raise AssertionError(f"unexpected URL in test: {url}")

    monkeypatch.setattr(mam, "fetch_text_with_retry", fake_text)


class TestParseElection:
    def test_reads_the_real_contested_district_field(self):
        result = mam._parse_election(DISTRICT6, "172973", 2026, "H")
        district, party, choices, _marked = result
        # The parse keeps printed names; the resolver reduces the winner.
        choices = [(surname(n), v) for n, v in choices]
        assert district == 6
        assert party == "D"
        assert dict(choices) == {
            "Koh": 47835, "Nguyen": 34324, "Beccia": 14215,
            "Belsito": 11268, "Lancaster": 4725, "Andres-Beck": 4385,
        }

    def test_reads_the_real_unopposed_district(self):
        district, party, choices, _marked = mam._parse_election(DISTRICT2, "172985", 2026, "H")
        # The parse keeps printed names; the resolver reduces the winner.
        choices = [(surname(n), v) for n, v in choices]
        assert district == 2
        assert party == "D"
        assert choices == [("McGovern", 80813)]

    def test_senate_has_no_district(self):
        district, party, choices, _marked = mam._parse_election(SENATE_D, "172905", 2026, "S")
        # The parse keeps printed names; the resolver reduces the winner.
        choices = [(surname(n), v) for n, v in choices]
        assert district is None
        assert party == "D"
        # Real data, not assumed: Markey's real primary opponent is Seth
        # W. Moulton.
        assert dict(choices) == {"Markey": 580628, "Moulton": 314198}

    def test_senate_republican_side_is_unopposed(self):
        district, party, choices, _marked = mam._parse_election(SENATE_R, "172906", 2026, "S")
        # The parse keeps printed names; the resolver reduces the winner.
        choices = [(surname(n), v) for n, v in choices]
        assert district is None
        assert party == "R"
        assert choices == [("Deaton", 221950)]

    def test_wrong_year_in_title_returns_none(self):
        assert mam._parse_election(DISTRICT6, "172973", 2028, "H") is None

    def test_wrong_chamber_returns_none(self):
        # A real House election id passed off as "S" (e.g. a mis-tagged
        # or stray link) must be refused, not silently mislabeled.
        assert mam._parse_election(DISTRICT6, "172973", 2026, "S") is None

    def test_a_title_attribute_after_the_totals_row_does_not_corrupt_results(self):
        # Real regression case: the WINNING candidate's own Totals-row
        # <td> carries the same candidate-id-{id} class as the header
        # (verified live). Trailing page content with its own title=
        # attribute (nav/footer -- absent from the trimmed fixture,
        # which is exactly what let this slip through una-caught the
        # first time) must not be pulled into the header scan.
        with_trailing_title = DISTRICT2.replace(
            "</body></html>", '<footer><a title="Other Elections">More</a></footer></body></html>',
        )
        district, party, choices, _marked = mam._parse_election(with_trailing_title, "172985", 2026, "H")
        # The parse keeps printed names; the resolver reduces the winner.
        choices = [(surname(n), v) for n, v in choices]
        assert district == 2
        assert party == "D"
        assert choices == [("McGovern", 80813)]

    def test_a_mismatched_totals_column_count_is_refused(self):
        # Real markup shape drift: a Totals row missing one candidate's
        # own number_ column must not silently zip against the wrong
        # header entry.
        broken = DISTRICT2.replace(
            '<td class=" number_80813 percent_0-996006754 party_background_extra_light '
            'democratic_party winner candidate-id-62621" '
            ' style="background-color:#ECF1E2 !important;">'
            '<div class="party_background_extra_light" '
            ' style="background-color:#ECF1E2 !important;">80,813</div></td>',
            "",
        )
        assert mam._parse_election(broken, "172985", 2026, "H") is None

    def test_no_totals_row_returns_none(self):
        broken = DISTRICT2.replace('<tr class="total">', '<tr class="not-total">')
        assert mam._parse_election(broken, "172985", 2026, "H") is None


class TestDiscoverElectionIds:
    async def test_finds_every_real_contested_district(self, monkeypatch):
        async def fake_text(client, rl, url, label, **kw):
            return SEARCH_HOUSE_FULL

        monkeypatch.setattr(mam, "fetch_text_with_retry", fake_text)
        ids = await mam._discover_election_ids(None, "MA", 5, 2026)
        assert set(ids) == _REAL_HOUSE_IDS

    async def test_fetch_failure_returns_none(self, monkeypatch):
        # A broken search page must not read the same as "genuinely no
        # primaries filed this year" -- both would otherwise map to the
        # same [] outcome.
        async def fake_text(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(mam, "fetch_text_with_retry", fake_text)
        assert await mam._discover_election_ids(None, "MA", 5, 2026) is None


class TestFetchConfirmedCandidates:
    async def test_real_primaries_resolve_to_the_real_winners(self, monkeypatch):
        _patched(monkeypatch, {
            "office_id:5": SEARCH_HOUSE_SMALL,
            "office_id:6": SEARCH_SENATE,
            "/172973/": DISTRICT6,
            "/172985/": DISTRICT2,
            "/172905/": SENATE_D,
            "/172906/": SENATE_R,
        })
        result = await mam.fetch_confirmed_candidates(None, 2026, "MA", {})
        assert {"office": "H", "district": 6, "party": "D", "last_name": "Koh", "display_name": "Dan Koh"} in result
        assert {"office": "H", "district": 2, "party": "D", "last_name": "McGovern", "display_name": "James P. McGovern"} in result
        assert {"office": "S", "district": None, "party": "D", "last_name": "Markey", "display_name": "Edward J. Markey"} in result
        assert {"office": "S", "district": None, "party": "R", "last_name": "Deaton", "display_name": "John Deaton"} in result
        assert len(result) == 4

    async def test_a_known_primary_date_gates_via_settle_days(self, monkeypatch):
        monkeypatch.setattr(mam, "primary_date", lambda state, year: "2026-09-01")
        _patched(monkeypatch, {
            "office_id:5": SEARCH_HOUSE_SMALL,
            "office_id:6": SEARCH_SENATE,
            "/172973/": DISTRICT6,
            "/172985/": DISTRICT2,
            "/172905/": SENATE_D,
            "/172906/": SENATE_R,
        })
        # 36500 days is never settled -- proves the calendar-derived date
        # actually reaches _settled() rather than being ignored.
        assert await mam.fetch_confirmed_candidates(None, 2026, "MA", {"settle_days": 36500}) == []
        # A small floor against the same real date clears normally.
        result = await mam.fetch_confirmed_candidates(None, 2026, "MA", {"settle_days": 1})
        assert len(result) == 4

    async def test_no_known_primary_date_skips_the_gate_rather_than_blocking(self, monkeypatch):
        monkeypatch.setattr(mam, "primary_date", lambda state, year: None)
        _patched(monkeypatch, {
            "office_id:5": SEARCH_HOUSE_SMALL,
            "office_id:6": SEARCH_SENATE,
            "/172973/": DISTRICT6,
            "/172985/": DISTRICT2,
            "/172905/": SENATE_D,
            "/172906/": SENATE_R,
        })
        result = await mam.fetch_confirmed_candidates(None, 2026, "MA", {"settle_days": 36500})
        assert len(result) == 4

    async def test_one_failed_race_page_does_not_lose_the_others(self, monkeypatch):
        # 172973 fetches fine; 172985 deliberately returns None below,
        # standing in for a genuine mid-run fetch failure -- unlike
        # Mississippi's two co-published same-day pages, MA's races are
        # independently discovered and fetched, so one page's outage
        # must not discard the other, already-successfully-parsed race.
        async def fake_text(client, rl, url, label, **kw):
            if "172973" in url:
                return DISTRICT6
            if "office_id:5" in url:
                return SEARCH_HOUSE_SMALL
            if "office_id:6" in url:
                return "<html><body></body></html>"
            return None  # the 172985 page: simulates a fetch failure

        monkeypatch.setattr(mam, "fetch_text_with_retry", fake_text)
        result = await mam.fetch_confirmed_candidates(None, 2026, "MA", {})
        assert {"office": "H", "district": 6, "party": "D", "last_name": "Koh", "display_name": "Dan Koh"} in result
        assert len(result) == 1

    async def test_every_fetch_failing_returns_none_not_a_healthy_empty(self, monkeypatch):
        async def fake_text(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(mam, "fetch_text_with_retry", fake_text)
        assert await mam.fetch_confirmed_candidates(None, 2026, "MA", {}) is None

    async def test_a_configured_runoff_threshold_withholds_a_sub_threshold_leader(self, monkeypatch):
        _patched(monkeypatch, {
            "office_id:5": SEARCH_HOUSE_SMALL,
            "office_id:6": "<html><body></body></html>",
            "/172973/": DISTRICT6,
            "/172985/": DISTRICT2,
        })
        # Koh's real 6-way field win (40.9%) falls under a 50% bar.
        result = await mam.fetch_confirmed_candidates(None, 2026, "MA", {"runoff_threshold_pct": 50.0})
        assert {"office": "H", "district": 6, "party": "D", "last_name": "Koh", "display_name": "Dan Koh"} not in result
        # McGovern's real unopposed 99.6% clears any real bar.
        assert {"office": "H", "district": 2, "party": "D", "last_name": "McGovern", "display_name": "James P. McGovern"} in result


class TestWinnerMark:
    def test_the_archive_marks_its_winner(self):
        *_, marked = mam._parse_election(DISTRICT6, "172973", 2026, "H")
        assert marked == {"Dan Koh"}

    async def test_an_unmarked_write_in_is_not_published(self, monkeypatch):
        # Real: Walter Grochowski, the only named candidate in the 2026
        # 5th District Republican primary, 13 votes to 978 "All Others".
        # The archive does not mark him; on votes alone he was published.
        _patched(monkeypatch, {
            "office_id:5": '<a href="/elections/view/173006/">x</a>',
            "office_id:6": "<html><body></body></html>",
            "/173006/": HOUSE5_R,
        })
        assert await mam.fetch_confirmed_candidates(None, 2026, "MA", {}) == []


class TestStatewideOffices:
    SOURCE = {"statewide_offices": True}

    def _pages(self, monkeypatch, **overrides):
        pages = {
            "office_id:5": "<html><body></body></html>",
            "office_id:6": SEARCH_SENATE,
            "/172905/": SENATE_D,
            "/172906/": SENATE_R,
            "year_from:2026/year_to:2026/stage:Primaries": SEARCH_ALL,
            **STATEWIDE_PAGES,
        }
        pages.update(overrides)
        _patched(monkeypatch, pages)

    def test_search_rows_read_the_sites_own_cells(self):
        rows = {r[0]: r[1:] for r in mam._search_rows(SEARCH_ALL, 2026)}
        assert rows["172894"] == ("Governor", "Statewide", "R", "Republican")
        assert rows["172925"] == ("Governor's Council", "1st", "D", "Democratic")
        # "No Candidates" links no page; the special primary is refused.
        assert "172884" not in rows and "171919" not in rows

    def test_labels(self):
        assert mam._statewide_label("Auditor", "Statewide") == "Statewide Auditor"
        assert mam._statewide_label("Governor's Council", "3rd") == "Governor's Council 3rd District"

    async def test_real_statewide_nominees(self, monkeypatch):
        self._pages(monkeypatch)
        result = await mam.fetch_confirmed_candidates(None, 2026, "MA", self.SOURCE)
        statewide = sorted(
            (r["office"], r["district"] or "", r["party"], r["last_name"])
            for r in result if r["office"] not in ("S", "H")
        )
        assert statewide == [
            ("auditor", "", "D", "Diana DiZoglio"),
            ("governor", "", "D", "Maura Healey"),
            # Minogue 194,758 over Brian J. Shortsleeve's 62,161.
            ("governor", "", "R", "Michael Roger Minogue"),
            ("governors_council", "1", "D", "Joseph C. Ferreira"),
            # Falcetano is a write-in the archive marks; Dormer (1st, R)
            # is one it does not, and is absent.
            ("governors_council", "5", "R", "William Falcetano"),
            ("secretary_of_commonwealth", "", "D", "William Francis Galvin"),
        ]

    async def test_county_offices_are_never_fetched(self, monkeypatch):
        fetched = []
        self._pages(monkeypatch)
        inner = mam.fetch_text_with_retry

        async def spy(client, rl, url, label, **kw):
            fetched.append(url)
            return await inner(client, rl, url, label, **kw)
        monkeypatch.setattr(mam, "fetch_text_with_retry", spy)
        await mam.fetch_confirmed_candidates(None, 2026, "MA", self.SOURCE)
        # 172929 County Treasurer, 172945 District Attorney, 171919 special.
        assert not any(i in u for u in fetched for i in ("172929", "172945", "171919"))

    async def test_without_the_opt_in_nothing_statewide_is_read(self, monkeypatch):
        self._pages(monkeypatch)
        result = await mam.fetch_confirmed_candidates(None, 2026, "MA", {})
        assert {r["office"] for r in result} == {"S"}

    async def test_a_failed_statewide_page_fails_the_run(self, monkeypatch):
        # A partial list would be synced as the whole truth.
        async def fake(client, rl, url, label, **kw):
            return None if "/172883/" in url else pages_lookup(url)
        pages = {
            "office_id:5": "<html><body></body></html>", "office_id:6": SEARCH_SENATE,
            "/172905/": SENATE_D, "/172906/": SENATE_R,
            "year_from:2026/year_to:2026/stage:Primaries": SEARCH_ALL, **STATEWIDE_PAGES,
        }

        def pages_lookup(url):
            return next(v for k, v in pages.items() if k in url)
        monkeypatch.setattr(mam, "fetch_text_with_retry", fake)
        assert await mam.fetch_confirmed_candidates(None, 2026, "MA", self.SOURCE) is None

    async def test_a_failed_search_fails_the_run(self, monkeypatch):
        self._pages(monkeypatch, **{"year_from:2026/year_to:2026/stage:Primaries": None})
        assert await mam.fetch_confirmed_candidates(None, 2026, "MA", self.SOURCE) is None

    async def test_a_page_whose_title_disagrees_with_its_row_fails_the_run(self, monkeypatch):
        # The Auditor row's link serving the Governor's page.
        self._pages(monkeypatch, **{"/172883/": GOVERNOR_D})
        assert await mam.fetch_confirmed_candidates(None, 2026, "MA", self.SOURCE) is None
