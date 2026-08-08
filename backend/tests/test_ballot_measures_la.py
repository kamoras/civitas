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

from app.pipeline.fetch import ballot_measures_la as la

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


def test_empty_document_returns_no_amendments():
    assert la.parse_document(_fake_pages([""])) == []
