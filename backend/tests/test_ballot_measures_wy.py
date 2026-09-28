"""Tests for Wyoming's strategy (ballot_measures_wy.py).

fixtures_wy_ballot_propositions_2026.json is REAL — extract_text() of
pages 1-2 of sos.wyo.gov/Elections/Docs/2026/2026_Statewide_Ballot_
Propositions.pdf, fetched 2026-09-28 (page 2 begins the attached full
text of the law, which must not be read as ballot language).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import ballot_measures_wy as wy

PAGES = json.loads((Path(__file__).parent / "fixtures_wy_ballot_propositions_2026.json").read_text())


def _pages(texts):
    return [SimpleNamespace(extract_text=lambda t=t: t) for t in texts]


def test_the_one_real_proposition_parses():
    [m] = wy.parse_document(_pages(PAGES))
    assert m["number"] == "PROPOSED INITIATIVE PROPOSITION NUMBER ONE"
    assert m["official_summary"].startswith("Shall a law be enacted: Establishing a property tax exemption")
    assert m["official_summary"].endswith("and specifying applicability?")
    assert m["fiscal_impact"].startswith("Anticipated Revenue / (decrease) Fiscal Year 2028 ($92,614,266)")
    assert m["fiscal_impact"].endswith("Future impacts depend on unpredictable economic conditions.")
    assert "FOR" not in m["fiscal_impact"].split()
    assert m["origin"] == "Wyoming voters (initiative petition)"
    assert m["yes_means"] is None and m["no_means"] is None


def test_heading_without_for_against_close_fails():
    broken = [PAGES[0].replace("\nFOR\nAGAINST", "")]
    with pytest.raises(ValueError):
        wy.parse_document(_pages(broken))


def test_document_with_no_proposition_heading_is_empty():
    assert wy.parse_document(_pages(["2026 GENERAL ELECTION STATEWIDE BALLOT PROPOSITIONS"])) == []


def test_a_question_under_an_unrecognised_heading_fails():
    extra = PAGES[0] + "\nCONSTITUTIONAL AMENDMENT A\nShall it?\nFOR\nAGAINST"
    with pytest.raises(ValueError):
        wy.parse_document(_pages([extra]))
