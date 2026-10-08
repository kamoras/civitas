"""Regression test for presidential_roster.py's roster parser.

Covers the edge cases that took real fixes against live UCSB HTML to get
right: Trump's two non-consecutive terms (labelled "(1st/2nd Term)" on
this page, must not leak into the display name), Grover Cleveland's two
terms (rendered with *identical* text, disambiguated only by page order),
Garfield/Truman's name-punctuation mismatches against NAME_TO_ID's keys
(sourced from a different UCSB table with slightly different formatting),
and Garfield's term_end (UCSB's roster page has no end date for a
president who died in office: the successor's first day stands in until
the president's own page is read, which gives the day of death). Chester
Arthur (who actually succeeded Garfield) is included so the stand-in is
his real first day.

Fixture HTML is a trimmed real excerpt (div/span structure copied
verbatim from a live fetch, 2026-07) rather than a full 47-row page.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.fetch import presidential_roster as roster
from app.pipeline.fetch.presidential_roster import _parse_roster, fetch_presidential_roster, parse_dates_in_office

# Newest-first, matching the real page's order. Row structure copied
# verbatim from a live fetch of https://www.presidency.ucsb.edu/presidents.
_FIXTURE_HTML = """
<html><body><div class="view-content">
<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/donald-j-trump-2nd-term">Donald J. Trump (2nd Term)<span property="dc:date" datatype="xsd:dateTime" content="2025-01-20T12:00:00+00:00" class="date-display-single">2025</span></a></span>
</div></div>
<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/donald-j-trump-1st-term">Donald J. Trump (1st Term)<span class="date-display-range"><span property="dc:date" datatype="xsd:dateTime" content="2017-01-20T12:00:00+00:00" class="date-display-start">2017</span> to <span property="dc:date" datatype="xsd:dateTime" content="2021-01-20T12:00:00+00:00" class="date-display-end">2021</span></span></a></span>
</div></div>
<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/harry-s-truman">Harry S Truman<span class="date-display-range"><span property="dc:date" datatype="xsd:dateTime" content="1945-04-12T00:00:00+00:00" class="date-display-start">1945</span> to <span property="dc:date" datatype="xsd:dateTime" content="1953-01-20T23:59:00+00:00" class="date-display-end">1953</span></span></a></span>
</div></div>
<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/grover-cleveland-0">Grover Cleveland<span class="date-display-range"><span property="dc:date" datatype="xsd:dateTime" content="1893-03-04T00:00:00+00:00" class="date-display-start">1893</span> to <span property="dc:date" datatype="xsd:dateTime" content="1897-03-04T23:59:00+00:00" class="date-display-end">1897</span></span></a></span>
</div></div>
<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/benjamin-harrison">Benjamin Harrison<span class="date-display-range"><span property="dc:date" datatype="xsd:dateTime" content="1889-03-04T00:00:00+00:00" class="date-display-start">1889</span> to <span property="dc:date" datatype="xsd:dateTime" content="1893-03-04T23:59:00+00:00" class="date-display-end">1893</span></span></a></span>
</div></div>
<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/grover-cleveland">Grover Cleveland<span class="date-display-range"><span property="dc:date" datatype="xsd:dateTime" content="1885-03-04T00:00:00+00:00" class="date-display-start">1885</span> to <span property="dc:date" datatype="xsd:dateTime" content="1889-03-04T23:59:00+00:00" class="date-display-end">1889</span></span></a></span>
</div></div>
<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/chester-arthur">Chester A. Arthur<span class="date-display-range"><span property="dc:date" datatype="xsd:dateTime" content="1881-09-20T00:00:00+00:00" class="date-display-start">1881</span> to <span property="dc:date" datatype="xsd:dateTime" content="1885-03-04T23:59:00+00:00" class="date-display-end">1885</span></span></a></span>
</div></div>
<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/james-garfield">James A. Garfield<span property="dc:date" datatype="xsd:dateTime" content="1881-03-04T00:00:00+00:00" class="date-display-single">1881</span></a></span>
</div></div>
</div></body></html>
"""


def test_roster_parser_edge_cases():
    entries = _parse_roster(_FIXTURE_HTML)
    by_id = {e.id: e for e in entries}

    assert set(by_id) == {
        "trump-45", "trump-47", "truman-33", "cleveland-22",
        "cleveland-24", "bharrison-23", "garfield-20", "arthur-21",
    }

    # Trump: display name must not leak the "(1st/2nd Term)" disambiguator.
    assert by_id["trump-45"].name == "Donald J. Trump"
    assert by_id["trump-47"].name == "Donald J. Trump"
    assert by_id["trump-45"].term_start == "2017-01-20"

    # Cleveland: identical link text on the page, must resolve to the
    # correct non-consecutive term by date, and the display name must not
    # carry a "- I"/"- II" suffix either.
    assert by_id["cleveland-22"].name == "Grover Cleveland"
    assert by_id["cleveland-22"].term_start == "1885-03-04"
    assert by_id["cleveland-24"].term_start == "1893-03-04"

    # Numbering falls out of (reversed) page position: Cleveland's earlier
    # term (22) must sit before Harrison (23), which sits before
    # Cleveland's later term (24), regardless of newest-first page order.
    assert by_id["cleveland-22"].number < by_id["bharrison-23"].number < by_id["cleveland-24"].number

    # Garfield/Truman: real NAME_TO_ID punctuation mismatches (missing
    # middle initial, missing period) that the alias table + period
    # stripping must reconcile.
    assert by_id["garfield-20"].name == "James A. Garfield"
    assert by_id["truman-33"].name == "Harry S Truman"

    # The roster page has no end date for Garfield (died in office): Arthur's
    # first day stands in, not None (which would read as "still serving"),
    # until the fetch reads Garfield's own page (test below).
    assert by_id["garfield-20"].term_end == "1881-09-20"
    # The actual current president (no successor in the data) correctly
    # keeps term_end=None — the backfill must not run off the end of the list.
    assert by_id["trump-47"].term_end is None


class TestFetchPresidentialRosterSanityFloor:
    """2026-07 (#218 review S1): below the 40-row sanity floor, the fetcher
    used to still return the partial list (`entries or []`) — since
    `number` is pure list position, even one silently-dropped row
    mis-numbers every later president, and _sync_roster would persist that
    wrong numbering to the DB. Below the floor, nothing beats something
    wrong: return empty and let the prior DB rows stand."""

    @pytest.mark.asyncio
    async def test_below_floor_returns_empty_not_partial(self, db_session):
        response = MagicMock()
        response.status_code = 200
        response.text = _FIXTURE_HTML  # 8 rows — well under the 40-row floor
        with patch(
            "app.pipeline.fetch.presidential_roster.fetch_with_retry_requests",
            new=AsyncMock(return_value=response),
        ):
            entries = await fetch_presidential_roster(db_session)
        assert entries == []


def test_a_president_the_id_table_does_not_list_is_kept_with_a_derived_id():
    # Sworn in after NAME_TO_ID was written: skipping them left the sitting
    # president off the site while the row count still passed the floor.
    newest = """<div class="views-row"><div class="views-field views-field-title">
<span class="field-content"><a href="/people/president/jane-q-public">Jane Q. Public, Jr.<span property="dc:date" datatype="xsd:dateTime" content="2029-01-20T12:00:00+00:00" class="date-display-single">2029</span></a></span>
</div></div>"""
    html = _FIXTURE_HTML.replace('<div class="view-content">', '<div class="view-content">' + newest, 1)
    entries = _parse_roster(html)
    newest_entry = entries[-1]
    assert newest_entry.id == f"public-{newest_entry.number}"
    assert newest_entry.name == "Jane Q. Public, Jr."
    assert newest_entry.term_end is None
    # The listed ids are untouched, and the previous president's term now ends.
    assert {e.id for e in entries} >= {"trump-45", "trump-47", "cleveland-22"}
    assert next(e for e in entries if e.id == "trump-47").term_end == "2029-01-20"


_GARFIELD_PAGE = """<html><body><div class="field-docs-content">
<div>Dates In Office:&nbsp; March 04, 1881 to September 19, 1881</div>
<div>Birth - Death:&nbsp; November 19, 1831 to September 19, 1881</div></div></body></html>"""


def test_a_term_ended_by_death_ends_on_the_death_not_the_successors_oath():
    assert parse_dates_in_office(_GARFIELD_PAGE) == "1881-09-19"
    assert parse_dates_in_office("<html><body>Dates In Office: January 20, 2025 to Present</body></html>") is None


@pytest.mark.asyncio
async def test_the_fetch_reads_the_presidents_own_page_for_a_missing_end(db_session, monkeypatch):
    entries = _parse_roster(_FIXTURE_HTML)
    garfield = next(e for e in entries if e.id == "garfield-20")
    assert garfield.end_from_successor and garfield.page == "/people/president/james-garfield"
    monkeypatch.setattr(roster, "_parse_roster", lambda html: entries * 6)  # past the 40-row floor
    page = MagicMock(status_code=200, text=_GARFIELD_PAGE)
    with patch.object(roster, "fetch_with_retry_requests", new=AsyncMock(return_value=page)):
        await fetch_presidential_roster(db_session)
    assert garfield.term_end == "1881-09-19" and not garfield.end_from_successor
