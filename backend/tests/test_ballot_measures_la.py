"""Tests for Louisiana's ballot-measure PDF strategy: parsing the
Secretary of State's "Proposed Constitutional Amendments" guide.

fixtures_la_2022_pages.json is REAL — page.extract_text() output from
the real November 2022 general-election guide, both pages. This is the
real document where amendment 4's body sits directly against the
page-1/page-2 boundary: the page-1 footer ("Prepared by the Louisiana
Secretary of State 1") and page-2's running header ("NOVEMBER 8, 2022
PROPOSED CONSTITUTIONAL AMENDMENTS") both fall between amendment 4's
last sentence and amendment 5's marker in the joined text — exactly the
contamination case the module docstring describes, not a synthetic one.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.pipeline.fetch import ballot_measures_la as la
from app.pipeline.fetch.ballot_measure_pdf_sources import source_for_state
from app.pipeline.fetch.ballot_measures_pdf import discover_pdf_url_checked

FIXTURE = json.loads((Path(__file__).parent / "fixtures_la_2022_pages.json").read_text())


def _fake_pages(texts):
    return [SimpleNamespace(extract_text=lambda t=t: t) for t in texts]


def test_parses_all_eight_amendments():
    results = la.parse_document(_fake_pages(FIXTURE))
    assert [r["number"] for r in results] == [str(n) for n in range(1, 9)]


def test_page_footer_does_not_leak_into_the_preceding_amendment():
    four = next(r for r in la.parse_document(_fake_pages(FIXTURE)) if r["number"] == "4")
    assert four["official_summary"].endswith(
        "result of damage to the water system not caused by the customer?”"
    )
    assert "Secretary of State" not in four["official_summary"]


def test_page_header_does_not_leak_into_the_preceding_amendment():
    four = next(r for r in la.parse_document(_fake_pages(FIXTURE)) if r["number"] == "4")
    assert "PROPOSED CONSTITUTIONAL AMENDMENTS" not in four["official_summary"].upper()


def test_amendment_after_the_page_break_is_clean():
    five = next(r for r in la.parse_document(_fake_pages(FIXTURE)) if r["number"] == "5")
    assert five["official_summary"].startswith("Act 133 of the 2021 Regular Session")
    assert "PROPOSED CONSTITUTIONAL AMENDMENTS" not in five["official_summary"].upper()


def test_fixed_fields_reflect_legislature_referral_with_no_framing_or_fiscal_impact():
    results = la.parse_document(_fake_pages(FIXTURE))
    for r in results:
        assert r["origin"] == "Louisiana Legislature"
        assert r["title_authority"] == "Louisiana Legislature"
        assert r["yes_means"] is None
        assert r["no_means"] is None
        assert r["fiscal_impact"] is None
        assert r["fiscal_authority"] is None


def test_title_and_summary_match_real_source_text():
    two = next(r for r in la.parse_document(_fake_pages(FIXTURE)) if r["number"] == "2")
    assert two["title"] == "Proposed Amendment No. 2"
    assert two["official_summary"].startswith("Act 172 of the 2022 Regular Session")
    assert "expand certain property tax exemptions" in two["official_summary"]


def test_a_document_with_no_amendment_found_is_a_failure_never_none():
    """[] used to become confirmed_none — for an unreadable document (a
    scan with no text layer reads as ""), the guide the Secretary
    publishes because amendments are on the ballot."""
    with pytest.raises(ValueError):
        la.parse_document(_fake_pages([""]))


def test_a_numbered_amendment_with_no_text_refuses_the_document():
    pages = _fake_pages(["Proposed Amendment No. 1\nDo you support it?\nProposed Amendment No. 2\n"])
    with pytest.raises(ValueError):
        la.parse_document(pages)


FIXTURE_2026 = json.loads((Path(__file__).parent / "fixtures_la_2026_pages.json").read_text())
# The real Secretary of State "types of elections" page's constitutional-
# amendment PDF links for 2022-2026, in page order (2026-10-15).
LANDING_2026 = (Path(__file__).parent / "fixtures_la_types_of_elections_2026.html").read_text()


def test_parses_all_ten_2026_amendments():
    """The real November 3, 2026 guide (extract_text, both pages)."""
    results = la.parse_document(_fake_pages(FIXTURE_2026))
    assert [r["number"] for r in results] == [str(n) for n in range(1, 11)]
    assert results[0]["official_summary"].startswith("Act 39 (2026 Regular Session) - Proposing to amend Article VII")
    assert results[-1]["official_summary"].endswith("(Effective January 1, 2027) (Adds Article VII, Section 21(P))")


async def test_registry_discovers_the_guide_not_the_summaries_linked_above_it():
    """2026's statements document ("...2026-nov-summaries.pdf", a
    three-column layout parse_document can't read) is linked before the
    guide; the registry's exclude must skip it, or the state fails."""
    source = source_for_state("LA")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=LANDING_2026))
    async with httpx.AsyncClient(transport=transport) as client:
        url, complete = await discover_pdf_url_checked(
            client, source["landing_page_url"], 2026, tuple(source["keyword"]), exclude=tuple(source["exclude"]),
        )
    assert url == "https://www.sos.la.gov/media/jo2die1s/proposed-constitutional-amendments-2026-nov.pdf"
    assert complete
