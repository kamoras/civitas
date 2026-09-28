"""Tests for Michigan's ballot-measure strategy (ballot_measures_mi.py).

fixtures_mi_ballot_questions.json is REAL — the "2026 November ballot
questions" link from michigan.gov/sos/elections and pdf_lines() of the
PDF it pointed to (text + bold flag per line), fetched live 2026-09-28.
"""

import json
from pathlib import Path

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
