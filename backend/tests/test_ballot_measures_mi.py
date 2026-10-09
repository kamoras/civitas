"""Tests for Michigan's ballot-measure strategy (ballot_measures_mi.py).

fixtures_mi_ballot_questions.json is REAL — the "2026 November ballot
questions" link from michigan.gov/sos/elections and pdf_lines() of the
PDF it pointed to (text + bold flag per line), fetched live 2026-09-28.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_mi as mi

FIXTURE = json.loads((Path(__file__).parent / "fixtures_mi_ballot_questions.json").read_text())


def _pages():
    return [[tuple(line) for line in page] for page in FIXTURE["pdf_lines"]]


def test_headers_keep_civitas_but_drop_the_contact_comment():
    assert mi.HEADERS["User-Agent"].endswith("Civitas/1.0")
    assert "(+" not in mi.HEADERS["User-Agent"]


def test_finds_the_pdf_link_with_its_rev_suffix():
    url = mi.find_pdf_url(FIXTURE["landing"], 2026)
    assert url.startswith("https://www.michigan.gov/sos/-/media/")
    assert "2026-November-ballot-questions.pdf?rev=" in url
    assert mi.find_pdf_url(FIXTURE["landing"], 2028) is None


def test_both_real_2026_proposals():
    one, two = mi.parse_lines(_pages(), 2026)
    assert one["number"] == "2026-1"
    assert one["title"] == (
        "A proposal to convene a constitutional convention for the purpose of drafting a "
        "general revision of the state constitution"
    )
    assert one["official_summary"].startswith("Shall a convention of elected delegates be convened in 2027")
    assert one["origin"] is None
    assert two["number"] == "2026-2"
    assert two["title"].startswith("A proposed initiated law to prohibit campaign contributions")
    assert two["origin"] == "Michigan voters (initiative petition)"
    # The bullet list stays a list; the question closes it.
    assert two["official_summary"].startswith("The proposal would:\n• Prohibit regulated electric")
    assert two["official_summary"].count("\n• ") == 3
    assert two["official_summary"].endswith("Should this proposal be adopted?")
    # The closing question stands apart from the last bullet, as printed.
    assert two["official_summary"].endswith("internet political communications.\n\nShould this proposal be adopted?")
    assert "\n\n" not in one["official_summary"]
    for m in (one, two):
        assert m["yes_means"] is None and m["no_means"] is None
        assert "[ ]" not in m["official_summary"]
        assert "BUREAU OF ELECTIONS" not in m["official_summary"]


def test_other_years_proposals_are_not_read():
    assert mi.parse_lines(_pages(), 2028) is None


def test_page_without_a_yes_box_refuses_the_document():
    pages = _pages()
    pages[0] = [line for line in pages[0] if "Yes" not in line[0]]
    assert mi.parse_lines(pages, 2026) is None


def test_every_proposal_on_a_page_is_read_not_just_the_first():
    """The regression: only the first "Proposal" line per page was read,
    so two proposals sharing a page published as one."""
    pages = _pages()
    merged = [pages[0] + pages[1]]
    assert [m["number"] for m in mi.parse_lines(merged, 2026)] == ["2026-1", "2026-2"]


def test_an_unrecognised_proposal_heading_refuses_the_document():
    pages = _pages()
    pages[1] = [("Proposal 26-2", True) if t.startswith("Proposal ") else (t, b) for t, b in pages[1]]
    assert mi.parse_lines(pages, 2026) is None


def test_the_bold_title_is_the_ballot_title():
    one, _ = mi.parse_lines(_pages(), 2026)
    assert one["official_title"] == one["title"]


async def test_no_document_linked_yet_is_not_yet_published(monkeypatch):
    """The Bureau posts the document only once something is certified, so
    no link is 'not yet' — not an ingest failure paging every night of a
    year with nothing certified, and never 'none'."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    async def get_text(*a, **kw):
        return FIXTURE["landing"]

    monkeypatch.setattr(mi, "fetch_text_with_retry", get_text)
    with pytest.raises(NotYetPublished):
        await mi.fetch_measures(None, 2028)
    # Two candidate links is ambiguity, not absence.
    doubled = FIXTURE["landing"].replace("</body", FIXTURE["landing"].split("<body>")[1].split("</body")[0].replace("rev=", "rev=x") + "</body")

    async def get_doubled(*a, **kw):
        return doubled

    monkeypatch.setattr(mi, "fetch_text_with_retry", get_doubled)
    assert await mi.fetch_measures(None, 2026) is None
