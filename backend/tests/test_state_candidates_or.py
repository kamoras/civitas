"""Tests for Oregon's confirmed-general-candidate strategy
(state_candidates_or.py).

fixtures_or_primary_results.pdf is a REAL 3-page slice of the actual 2026
primary "Abstract of Votes" PDF (fetched live 2026-09-08 via
records.sos.state.or.us's own DocumentStream.ashx?uri=16180585, 63 pages,
1.37MB), trimmed with pypdfium2's page-import (not hand-authored -- PDF's
binary structure isn't something to safely hand-write the way this
session's OOXML/HTML fixtures are) to just US Senator (both parties, 2
pages) and CD1 (both parties on one page, 1 page). Chosen specifically
because these 3 pages already prove out every real quirk this module's
own docstring documents: office+district detection from plain text (not
the table), multiple party blocks on one page (CD1), and the real
middle-name-wrap bug this module was built to survive (Senate R's
"David Brock Smith" -- the real regression case; a naive parser
misreads this as "Brock", not "Smith").

Every name and vote total below is read directly off this real fixture,
never recalled -- a real mistake happened once already while researching
this module (several candidates' first names were wrong on the first
draft, guessed from general political recollection instead of the
actual document) and is why this docstring says "read directly" rather
than "verified against real 2026 winners" the way earlier modules this
session could.
"""

import io
from pathlib import Path

import pdfplumber
import pytest

from app.pipeline.fetch import state_candidates_or as orm

FIXTURES = Path(__file__).parent
PDF_BYTES = (FIXTURES / "fixtures_or_primary_results.pdf").read_bytes()

_DISCOVERY_JSON = {
    "value": [{
        "Title": "May 19, 2026",
        "Election_x0020_Date": "2026-05-19T05:00:00Z",
        "Results": (
            '<div><a href="https&#58;//records.sos.state.or.us/ORSOSCMSearch/'
            'Search/RecordViewer.aspx?uri=16180585" target="_blank">'
            "Official Results of May Primary\u200b</a><br></div>"
        ),
    }],
}


def _patched(monkeypatch, json_body=_DISCOVERY_JSON, pdf=PDF_BYTES):
    async def fake_json(client, rl, url, label, **kw):
        return json_body

    async def fake_bytes(client, rl, url, label, **kw):
        return pdf

    monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
    monkeypatch.setattr(orm, "fetch_bytes_with_retry", fake_bytes)


class TestPageOffice:
    # The real Senate and CD1 pages are read in
    # TestFederalContests.test_finds_every_real_federal_candidate_across_all_three_pages.

    def test_a_non_federal_office_is_refused(self):
        assert orm._page_office("title\nGovernor\nDemocrat\nmore text") is None

    def test_too_few_lines_returns_none(self):
        assert orm._page_office("one line only") is None


class TestPageCandidates:
    def test_the_middle_name_wrap_does_not_overwrite_the_real_surname(self):
        # The real regression: "David Brock Smith"'s overflowing middle
        # name ("Brock") wraps onto its own row below the real surname
        # row ("*Smith") in the actual PDF. Without the awaiting_surnames
        # guard, that continuation row is mistaken for a second surname
        # row and silently overwrites the real one just before the Total
        # row is read -- attaching Smith's real 107,953 votes to the
        # surname "Brock" instead.
        with pdfplumber.open(io.BytesIO(PDF_BYTES)) as pdf:
            candidates = orm._page_candidates(pdf.pages[1])
        by_name = {name: votes for name, _party, votes in candidates}
        assert by_name["Smith"] == 107953
        assert "Brock" not in by_name


class _FakePage:
    """Minimal stand-in for a pdfplumber Page: _page_candidates only ever
    calls .extract_table(table_settings=...) on it."""

    def __init__(self, table):
        self._table = table

    def extract_table(self, table_settings=None):  # noqa: ARG002
        return self._table


class TestPageCandidatesTotalRowGuard:
    def test_a_total_row_with_a_different_column_count_is_skipped_not_misattributed(self):
        # A malformed extraction (the "text" table strategy clustered the
        # Total row's numeric columns differently than the surname row's)
        # must not zip() the wrong vote total onto a candidate -- it should
        # drop the whole block, matching state_candidates_wy.py's own
        # fail-closed Total-row guard.
        table = [
            ["Democrat", "", ""],
            ["", "Merkley", "Wells"],
            ["County", "Jeff", "Paul"],
            ["Total", "457006", "30544", "2907"],  # one extra column
        ]
        assert orm._page_candidates(_FakePage(table)) == []


class TestFederalContests:
    def test_finds_every_real_federal_candidate_across_all_three_pages(self):
        # Exactly these: each page's office and district come from its
        # own text (Senate on pages 1-2, CD1 on page 3), and no "Misc"
        # write-in column is read as a candidate.
        contests = orm._federal_contests(PDF_BYTES)
        assert sorted((o, d, p, n) for o, d, p, n, _v in contests) == [
            # CD1's real page carries a complete Democratic block AND a
            # complete Republican block, one below the other -- proves
            # current_party updates mid-page rather than being fixed
            # once per page.
            ("H", 1, "D", "Ahmad"), ("H", 1, "D", "Bonamici"),
            ("H", 1, "R", "Kahl"), ("H", 1, "R", "Verbeek"),
            ("S", None, "D", "Merkley"), ("S", None, "D", "Wells"),
            ("S", None, "R", "Barker"), ("S", None, "R", "Brown"),
            ("S", None, "R", "Burch"), ("S", None, "R", "McAlmond"),
            ("S", None, "R", "Perkins"), ("S", None, "R", "Skelton"),
            ("S", None, "R", "Smith"),
        ]


class TestDiscoverPdfUrl:
    async def test_reads_the_real_uri_and_date(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return _DISCOVERY_JSON

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        result = await orm._discover_pdf_url(None, "OR", 2026)
        assert result == (
            "https://records.sos.state.or.us/ORSOSCMSearch/Search/DocumentStream.ashx?uri=16180585",
            "2026-05-19",
        )

    async def test_a_row_for_the_wrong_year_returns_none(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return _DISCOVERY_JSON

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        assert await orm._discover_pdf_url(None, "OR", 2028) is None

    async def test_fetch_failure_raises_discovery_failed(self, monkeypatch):
        # A genuine fetch/parse failure must NOT read the same as "no rows
        # for this year" -- it has to be distinguishable so the caller can
        # report fetch_failed instead of a healthy empty result.
        async def fake_json(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        with pytest.raises(orm.DiscoveryFailed):
            await orm._discover_pdf_url(None, "OR", 2026)

    async def test_no_matching_uri_in_results_field_raises_discovery_failed(self, monkeypatch):
        # A row for the right year with a malformed Results field is a
        # genuine parse failure, not a healthy "nothing published yet".
        async def fake_json(client, rl, url, label, **kw):
            return {"value": [{"Election_x0020_Date": "2026-05-19T05:00:00Z", "Results": "no link here"}]}

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        with pytest.raises(orm.DiscoveryFailed):
            await orm._discover_pdf_url(None, "OR", 2026)


class TestFetchConfirmedCandidates:
    async def test_real_primary_resolves_to_the_real_winners(self, monkeypatch):
        _patched(monkeypatch)
        result = await orm.fetch_confirmed_candidates(None, 2026, "OR", {"settle_days": 1})
        assert {"office": "S", "district": None, "party": "D", "last_name": "Merkley"} in result
        assert {"office": "S", "district": None, "party": "R", "last_name": "Smith"} in result
        assert {"office": "H", "district": 1, "party": "D", "last_name": "Bonamici"} in result
        assert {"office": "H", "district": 1, "party": "R", "last_name": "Kahl"} in result
        assert len(result) == 4

    async def test_no_row_for_the_requested_year_confirms_nothing_yet(self, monkeypatch):
        _patched(monkeypatch)
        result = await orm.fetch_confirmed_candidates(None, 2028, "OR", {"settle_days": 1})
        assert result == []

    async def test_not_yet_settled_confirms_nothing(self, monkeypatch):
        _patched(monkeypatch)
        result = await orm.fetch_confirmed_candidates(None, 2026, "OR", {"settle_days": 36500})
        assert result == []

    async def test_discovery_failure_returns_none_not_empty(self, monkeypatch):
        # A broken SharePoint fetch must surface as fetch_failed (None), not
        # a healthy "0 confirmed" -- otherwise an outage looks identical to
        # a legitimately-not-yet-published primary and nothing ever falls
        # back or alerts on it.
        async def fake_json(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(orm, "fetch_json_with_retry", fake_json)
        assert await orm.fetch_confirmed_candidates(None, 2026, "OR", {}) is None

    async def test_pdf_fetch_failure_returns_none(self, monkeypatch):
        _patched(monkeypatch)

        async def fake_bytes(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(orm, "fetch_bytes_with_retry", fake_bytes)
        assert await orm.fetch_confirmed_candidates(None, 2026, "OR", {"settle_days": 1}) is None

    async def test_a_configured_runoff_threshold_withholds_a_sub_threshold_leader(self, monkeypatch):
        # Real data: Senate R's real 7-way field has Smith's real 107,953
        # as a minority of the group's total votes -- Oregon nominates by
        # plurality so this confirms today, but proves runoff_threshold_
        # pct is actually wired through config, not just harmlessly
        # present at null.
        _patched(monkeypatch)
        result = await orm.fetch_confirmed_candidates(
            None, 2026, "OR", {"settle_days": 1, "runoff_threshold_pct": 50.0},
        )
        assert {"office": "S", "district": None, "party": "R", "last_name": "Smith"} not in result
        # Merkley's real 2-way majority is untouched by the same threshold.
        assert {"office": "S", "district": None, "party": "D", "last_name": "Merkley"} in result


# ── Statewide executive contests ─────────────────────────────────────
#
# fixtures_or_primary_statewide.pdf is a REAL 9-page slice of the same
# official 2026 Abstract of Votes (uri=16180585, fetched 2026-09-28),
# trimmed with pypdfium2's page import, not hand-built: the US Senator
# Democratic page (federal, must stay federal), all four Governor pages
# ("Democrat", "Democrat (cont.)", "Republican", "Republican (cont.)"),
# the first State Senator page (Districts 3, 4 and 6), the Commissioner
# of the Bureau of Labor and Industries page, the Supreme Court
# Position 4 page and the Clatsop County District Attorney page. Every
# name and figure asserted below is read off those real pages.

STATEWIDE_PDF = (FIXTURES / "fixtures_or_primary_statewide.pdf").read_bytes()
_OR_SOURCE = {
    "settle_days": 1, "statewide_offices": True,
    "nonpartisan_resolution": "elects", "nonpartisan_advance_count": 2,
}


def _contests():
    return orm._statewide_contests(STATEWIDE_PDF)


class TestStatewideContests:
    def test_only_the_two_real_statewide_offices_are_read(self):
        # State Senator, the Supreme Court and a county DA are all on
        # the slice and none of them is a statewide executive office.
        assert set(_contests()) == {("governor", None), ("labor_commissioner", None)}

    def test_the_governor_field_is_merged_across_its_continuation_pages(self):
        blocks = _contests()[("governor", None)]
        dem = [c for party, block in blocks if party == "D" for c in block]
        rep = [c for party, block in blocks if party == "R" for c in block]
        # 10 named Democrats + Misc., 14 named Republicans + Misc.
        assert len(dem) == 11
        assert len(rep) == 15

    def test_whole_names_come_from_word_positions_not_the_split_table(self):
        # The text-strategy table cuts these mid-word ("Alexander At" |
        # "kinson IV", "County Fo" | "rest (Fora)") and on the
        # continuation page hands "William" to the wrong column.
        cells = {cell: (given, votes) for _p, block in _contests()[("governor", None)]
                 for cell, given, votes in block}
        assert cells["*Kotek"] == ("Tina", 385999)
        assert cells["Atkinson IV"] == ("James", 5902)
        assert cells["Alexander"] == ("Forest (Fora)", 12684)
        assert cells["Laible"] == ("Steve William", 3692)
        assert cells["Jones"] == ("Brittany", 13939)
        assert cells["Weigler"] == ("Miranda", 10161)
        assert cells["*Drazan"] == ("Christine", 172474)
        assert cells["Romero Jr"] == ("Paul J", 1615)

    def test_boli_is_one_non_partisan_block(self):
        assert _contests()[("labor_commissioner", None)] == [
            (None, [("Lynch", "Chris", 341903), ("**Stephenson", "Christina E", 595583), ("Misc.", "", 4535)]),
        ]


class TestStatewideNominees:
    def test_the_real_governor_nominees(self):
        # And nothing for BOLI: Christina E Stephenson took 595,583 of
        # 942,021 -- 63.2% counting write-ins -- and the Abstract marks her
        # "**" (Elected). ORS 249.088(1)(b): a majority ELECTS, so nobody
        # runs in November.
        assert orm._statewide_nominees(_contests(), _OR_SOURCE) == [
            {"office": "governor", "district": None, "party": "D", "last_name": "Tina Kotek"},
            {"office": "governor", "district": None, "party": "R", "last_name": "Christine Drazan"},
        ]

    def test_a_no_majority_non_partisan_contest_sends_the_top_two(self):
        # ORS 249.088(1)(a). Hypothetical figures on the real layout.
        contests = {("labor_commissioner", None): [
            (None, [("*Lynch", "Chris", 400), ("*Stephenson", "Christina E", 450),
                    ("Helt", "Cheri", 200), ("Misc.", "", 10)]),
        ]}
        assert orm._statewide_nominees(contests, _OR_SOURCE) == [
            {"office": "labor_commissioner", "district": None, "party": "N", "party_label": "Nonpartisan",
             "last_name": "Christina E Stephenson"},
            {"office": "labor_commissioner", "district": None, "party": "N", "party_label": "Nonpartisan",
             "last_name": "Chris Lynch"},
        ]

    def test_write_ins_count_toward_the_majority(self):
        # 510 of 1,010 named votes is a majority of the named field but
        # not of the votes cast for the office once 30 write-ins count.
        contests = {("labor_commissioner", None): [
            (None, [("*Lynch", "Chris", 510), ("*Stephenson", "Christina E", 490), ("Misc.", "", 30)]),
        ]}
        names = [r["last_name"] for r in orm._statewide_nominees(contests, _OR_SOURCE)]
        assert names == ["Chris Lynch", "Christina E Stephenson"]

    # Every case below FAILS rather than dropping the contest: with the
    # opt-in set, a missing contest is recorded as a confirmed absence.

    def test_a_non_partisan_contest_without_a_configured_rule_fails(self):
        contests = {("labor_commissioner", None): [
            (None, [("*Lynch", "Chris", 400), ("*Stephenson", "Christina E", 450)]),
        ]}
        with pytest.raises(orm.DiscoveryFailed):
            orm._statewide_nominees(contests, {"statewide_offices": True})

    def test_a_majority_winner_the_abstract_does_not_mark_elected_fails(self):
        # ORS 249.091(2)(b): a majority in a VACANCY contest nominates
        # rather than elects, and the Abstract would print "*". Publishing
        # nobody there would hide a real November contest.
        contests = {("labor_commissioner", None): [
            (None, [("Lynch", "Chris", 400), ("*Stephenson", "Christina E", 600)]),
        ]}
        with pytest.raises(orm.DiscoveryFailed):
            orm._statewide_nominees(contests, _OR_SOURCE)

    def test_a_computed_winner_the_abstract_does_not_mark_fails(self):
        contests = {("governor", None): [
            ("D", [("Kotek", "Tina", 385999), ("*Jones", "Brittany", 13939)]),
        ]}
        with pytest.raises(orm.DiscoveryFailed):
            orm._statewide_nominees(contests, _OR_SOURCE)

    def test_a_contest_mixing_party_and_non_party_blocks_fails(self):
        contests = {("governor", None): [
            ("D", [("*Kotek", "Tina", 385999)]),
            (None, [("*Drazan", "Christine", 172474)]),
        ]}
        with pytest.raises(orm.DiscoveryFailed):
            orm._statewide_nominees(contests, _OR_SOURCE)

    def test_an_unreadable_page_fails(self):
        with pytest.raises(orm.DiscoveryFailed):
            orm._statewide_nominees({("governor", None): None}, _OR_SOURCE)


def _w(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top}


class TestPageBlocks:
    def test_a_block_with_no_total_row_makes_the_page_unreadable(self):
        lines = [
            [_w("Democrat", 41, 87, 93)],
            [_w("*Kotek", 315, 347, 106)],
            [_w("County", 41, 74, 120), _w("Tina", 327, 347, 120)],
            [_w("Baker", 41, 67, 133), _w("635", 330, 347, 133)],
        ]
        assert orm._page_blocks(lines) is None

    def test_a_name_outside_every_column_makes_the_page_unreadable(self):
        lines = [
            [_w("Democrat", 41, 87, 93)],
            [_w("*Kotek", 315, 347, 106), _w("Stray", 500, 530, 106)],
            [_w("County", 41, 74, 120), _w("Tina", 327, 347, 120)],
            [_w("Total", 59, 82, 622), _w("385,999", 310, 347, 622)],
        ]
        assert orm._page_blocks(lines) is None

    def test_an_unrecognised_party_heading_does_not_inherit_the_last_party(self):
        lines = [
            [_w("Democrat", 41, 87, 93)],
            [_w("*Kotek", 315, 347, 106)],
            [_w("County", 41, 74, 120), _w("Tina", 327, 347, 120)],
            [_w("Total", 59, 82, 200), _w("385,999", 310, 347, 200)],
            [_w("Progressive", 41, 97, 220)],
            [_w("*Someone", 315, 347, 233)],
            [_w("County", 41, 74, 246), _w("Pat", 327, 347, 246)],
            [_w("Total", 59, 82, 300), _w("9", 340, 347, 300)],
        ]
        assert orm._page_blocks(lines) is None


class TestFetchWithStatewideOffices:
    async def test_statewide_nominees_ride_the_same_fetch(self, monkeypatch):
        _patched(monkeypatch, pdf=STATEWIDE_PDF)
        result = await orm.fetch_confirmed_candidates(None, 2026, "OR", _OR_SOURCE)
        assert {"office": "S", "district": None, "party": "D", "last_name": "Merkley"} in result
        statewide = [r for r in result if r["office"] not in ("S", "H")]
        assert statewide == [
            {"office": "governor", "district": None, "party": "D", "last_name": "Tina Kotek"},
            {"office": "governor", "district": None, "party": "R", "last_name": "Christine Drazan"},
        ]

    async def test_an_unreadable_statewide_page_fails_the_whole_fetch(self, monkeypatch):
        # Returning the federal records alone would sync Oregon as
        # "checked, no governor's race".
        _patched(monkeypatch, pdf=STATEWIDE_PDF)
        monkeypatch.setattr(orm, "_page_blocks", lambda lines: None)
        assert await orm.fetch_confirmed_candidates(None, 2026, "OR", _OR_SOURCE) is None

    async def test_without_the_opt_in_nothing_statewide_is_read(self, monkeypatch):
        _patched(monkeypatch, pdf=STATEWIDE_PDF)
        result = await orm.fetch_confirmed_candidates(None, 2026, "OR", {"settle_days": 1})
        assert all(r["office"] in ("S", "H") for r in result)
