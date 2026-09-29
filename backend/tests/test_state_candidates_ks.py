"""Tests for Kansas's confirmed-general-candidate strategy
(state_candidates_ks.py).

fixtures_ks_primary_2026.pdf (15 pages, 41KB) is REAL — the Secretary of
State's own "Official Vote Totals" PDF for the real, certified 2026-08-04
primary, fetched live 2026-09-04. Kept whole: the ~140 non-federal race
sections after the 5 federal ones on page 1 are what prove the race-reset
logic actually works — without it, every one of those state house/senate
candidates falsely inherited whichever federal race printed last (found
and fixed during initial build, not a hypothetical).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import state_candidates_ks as ks

_PRIMARY_PDF = (Path(__file__).parent / "fixtures_ks_primary_2026.pdf").read_bytes()


def _resp(*, text=None, content=None):
    return SimpleNamespace(text=text, content=content)


class TestBannerRe:
    def test_matches_a_title_line_pdfplumber_widens_with_extra_spaces(self):
        # A real quirk found during initial build: pdfplumber's layout
        # mode rendered this exact title line with multiple spaces per
        # gap ("Kansas   Secretary   of State") to preserve its wider
        # font's visual column alignment -- a literal-single-space
        # pattern silently failed to recognise it as a banner on every
        # page, which would drop real candidates on any future document
        # whose federal race spans a page break. Kept even though the
        # module has since switched to plain extract_text() (which
        # doesn't widen gaps) -- \s+ is defense in depth against the
        # NEXT extraction quirk, not just this one.
        assert ks._BANNER_RE.match("Kansas   Secretary   of State")
        assert ks._BANNER_RE.match("Kansas Secretary of State")


class TestParseTotalsPdf:
    def test_real_primary_resolves_to_the_real_certified_winners(self):
        results = ks._parse_totals_pdf(_PRIMARY_PDF)
        assert sorted(
            (r["office"], r["district"], r["party"], r["last_name"]) for r in results
        ) == [
            ("H", 1, "D", "Reinhold"),
            ("H", 1, "R", "Mann"),
            ("H", 2, "D", "Coover"),
            ("H", 2, "R", "Schmidt"),
            ("H", 3, "D", "Davids"),
            ("H", 3, "R", "Jenkins"),
            ("H", 4, "D", "Tyndell"),
            ("H", 4, "R", "Estes"),
            ("S", None, "D", "Hamilton"),
            ("S", None, "R", "Marshall"),
        ]

    def test_a_crowded_senate_primary_correctly_excludes_the_losers(self):
        # The real Democratic Senate primary had 11 candidates; only
        # Hamilton's real plurality (34.63%) survives.
        results = ks._parse_totals_pdf(_PRIMARY_PDF)
        senate_d = [r for r in results if r["office"] == "S" and r["party"] == "D"]
        assert senate_d == [{"office": "S", "district": None, "party": "D", "last_name": "Hamilton", "display_name": "Adam Hamilton"}]

    def test_non_federal_races_after_the_federal_section_are_excluded(self):
        # The real document prints ~140 state house/senate races AFTER
        # the 5 federal ones on page 1 -- this is the regression the
        # race-reset logic exists for: without it, every one of those
        # non-federal candidates falsely inherited "US House 4" (the
        # last federal section printed) rather than being ignored.
        results = ks._parse_totals_pdf(_PRIMARY_PDF)
        assert len(results) == 10
        assert all(r["office"] in ("H", "S") for r in results)
        house_districts = {r["district"] for r in results if r["office"] == "H"}
        assert house_districts == {1, 2, 3, 4}


class TestStateOffices:
    """The same real PDF's state contests, read only under the
    `statewide_offices` opt-in. Every expected name below is the real
    plurality winner printed in fixtures_ks_primary_2026.pdf."""

    def _state(self):
        return [
            r for r in ks._parse_totals_pdf(_PRIMARY_PDF, state_offices=True)
            if r["office"] not in ("S", "H")
        ]

    def test_without_the_opt_in_nothing_but_federal_is_read(self):
        assert all(r["office"] in ("S", "H") for r in ks._parse_totals_pdf(_PRIMARY_PDF))

    def test_the_opt_in_leaves_the_federal_nominees_unchanged(self):
        federal = [r for r in ks._parse_totals_pdf(_PRIMARY_PDF, state_offices=True) if r["office"] in ("S", "H")]
        assert federal == ks._parse_totals_pdf(_PRIMARY_PDF)

    def test_every_executive_office_resolves_to_its_real_primary_winner(self):
        got = {
            (r["office"], r["party"]): r["last_name"]
            for r in self._state() if r["office"] not in ("upper", "lower", "state_board_of_education")
        }
        assert got == {
            # A joint ticket, kept whole and under the Governor: the
            # document prints "Governor / Lt. Governor" and the pair.
            # Holscher took 48.76% of a three-way Democratic field;
            # Masterson 43.20% of a seven-way Republican one, with
            # Sarnecki (22.37%) the runner-up that must not appear.
            ("governor", "D"): "Cindy Holscher / KC Ohaebosim",
            ("governor", "R"): "Ty Masterson / Jeffrey Klemp",
            ("secretary_of_state", "D"): "Jennifer Day",
            ("secretary_of_state", "R"): "Pat Proctor",
            ("attorney_general", "D"): "Chris Mann",
            ("attorney_general", "R"): "Kris Kobach",
            ("treasurer", "D"): "Juan C. Luengo",
            ("treasurer", "R"): "Steven Johnson",
            ("insurance_commissioner", "D"): "Dinah Sykes",
            ("insurance_commissioner", "R"): "Daniel Hawkins",
        }

    def test_board_of_education_seats_stay_apart(self):
        """"Member, State Board of Education 5" prints its seat as a bare
        trailing number. Without reading it, the five contests collapse
        into one office and each overwrites the last."""
        board = {
            (r["district"], r["party"]): r["last_name"]
            for r in self._state() if r["office"] == "state_board_of_education"
        }
        assert sorted({d for d, _ in board}) == ["1", "3", "5", "7", "9"]
        # Seat 5's real Republican primary was 51.00% to 49.00%.
        assert board[("5", "R")] == "Jean Clifford"
        assert board[("9", "R")] == "Destry Brown"

    def test_every_legislative_seat_on_the_ballot_is_read(self):
        state = self._state()
        lower = {r["district"] for r in state if r["office"] == "lower"}
        assert lower == {str(n) for n in range(1, 126)}
        upper = {(r["district"], r["party"]): r["last_name"] for r in state if r["office"] == "upper"}
        assert upper == {
            ("24", "R"): "Scott Hill",
            ("25", "D"): "Silas J. Miller",
            ("25", "R"): "Christopher Parisho",
        }
        # House 2's real Republican primary: Muter 57.84% over Collins.
        house_2 = {r["party"]: r["last_name"] for r in state if r["office"] == "lower" and r["district"] == "2"}
        assert house_2 == {"D": "Avery Rowland", "R": "Dan Muter"}

    def test_judgeships_and_the_amendment_are_refused(self):
        offices = {r["office"] for r in self._state()}
        assert offices == {
            "governor", "secretary_of_state", "attorney_general", "treasurer",
            "insurance_commissioner", "state_board_of_education", "upper", "lower",
        }

    def test_a_header_this_module_does_not_read_does_not_inherit_rows(self):
        # "District Court Judge 24" follows the Board of Education seats;
        # its candidates must not land on seat 9.
        board_9 = [r for r in self._state() if r["office"] == "state_board_of_education" and r["district"] == "9"]
        assert len(board_9) == 2


@pytest.mark.asyncio
class TestDiscoverPdfUrl:
    async def test_matches_the_link_by_its_title_text_not_a_url_template(self, monkeypatch):
        # The real listing page also carries prior years' links whose
        # FILE SLUG varies ("2024-Primary-Official-Vote-Totals.pdf" vs
        # 2026's "2026-Primary-Election-Official-Vote-Totals.pdf") --
        # only the anchor's own title text reliably carries the year.
        html = (
            '<a href="24elec/2024-Primary-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2024 Primary Election Official results in a new window">x</a>'
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            assert url == ks.LISTING_URL
            return _resp(text=html)

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        result = await ks._discover_pdf_url(None, 2026)
        assert result == "https://sos.ks.gov/elections/26elec/2026-Primary-Election-Official-Vote-Totals.pdf"

    async def test_a_year_not_yet_listed_yields_none(self, monkeypatch):
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            return _resp(text=html)

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks._discover_pdf_url(None, 2028) is None

    async def test_listing_page_fetch_failure_yields_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks._discover_pdf_url(None, 2026) is None

    async def test_an_empty_first_response_is_retried_once(self, monkeypatch):
        # A 200 response with none of the page's own links on it usually
        # means a bot-manager challenge intercepted the first request,
        # same as state_candidates_tabular.py's identical retry (added
        # for Minnesota) -- the second call succeeding must recover.
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )
        calls = []

        async def fake(client, rl, method, url, **kw):
            calls.append(url)
            if len(calls) == 1:
                return _resp(text="<html>challenge page, no real links</html>")
            return _resp(text=html)

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        result = await ks._discover_pdf_url(None, 2026)
        assert result == "https://sos.ks.gov/elections/26elec/2026-Primary-Election-Official-Vote-Totals.pdf"
        assert len(calls) == 2

    async def test_a_genuinely_empty_page_stays_empty_after_the_retry(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return _resp(text="<html>nothing here, for real</html>")

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks._discover_pdf_url(None, 2026) is None


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    def _patched(self, monkeypatch):
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            if url == ks.LISTING_URL:
                return _resp(text=html)
            if url.endswith(".pdf"):
                return _resp(content=_PRIMARY_PDF)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(ks, "fetch_with_retry", fake)

    async def test_real_primary_resolves_end_to_end(self, monkeypatch):
        self._patched(monkeypatch)
        result = await ks.fetch_confirmed_candidates(None, 2026, "KS", {})
        assert len(result) == 10
        assert {"office": "S", "district": None, "party": "R", "last_name": "Marshall", "display_name": "Roger Marshall"} in result

    async def test_the_opt_in_reaches_the_parser(self, monkeypatch):
        self._patched(monkeypatch)
        result = await ks.fetch_confirmed_candidates(None, 2026, "KS", {"statewide_offices": True})
        assert {"office": "governor", "district": None, "party": "R", "last_name": "Ty Masterson / Jeffrey Klemp"} in result
        assert len(result) == 243

    async def test_not_yet_published_this_cycle_is_a_healthy_empty_list(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return _resp(text="<html>nothing here</html>")

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks.fetch_confirmed_candidates(None, 2026, "KS", {}) == []

    async def test_pdf_download_failure_returns_none(self, monkeypatch):
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            if url == ks.LISTING_URL:
                return _resp(text=html)
            return None

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks.fetch_confirmed_candidates(None, 2026, "KS", {}) is None

    async def test_an_unparsable_pdf_returns_none(self, monkeypatch):
        html = (
            '<a href="26elec/2026-Primary-Election-Official-Vote-Totals.pdf" target="_blank" '
            'title="Click to open the 2026 Primary Election Official results in a new window">x</a>'
        )

        async def fake(client, rl, method, url, **kw):
            if url == ks.LISTING_URL:
                return _resp(text=html)
            if url.endswith(".pdf"):
                return _resp(content=b"not a real pdf")
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(ks, "fetch_with_retry", fake)
        assert await ks.fetch_confirmed_candidates(None, 2026, "KS", {}) is None

    async def test_a_pdf_that_parses_but_matches_no_rows_returns_none(self, monkeypatch):
        # A well-formed PDF whose text simply doesn't match any of the
        # race-header/candidate-row shapes (e.g. Kansas reformats the
        # document) must be reported as a failure, not a real primary
        # that genuinely confirmed zero candidates -- same convention as
        # NJ/KY/AL's identical guard.
        self._patched(monkeypatch)
        monkeypatch.setattr(ks, "_parse_totals_pdf", lambda content, state_offices=False: [])
        assert await ks.fetch_confirmed_candidates(None, 2026, "KS", {}) is None
