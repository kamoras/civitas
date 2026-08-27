"""Tests for California's ballot-measure PDF strategy: parsing the state's
own official Voter Information Guide PDF page format.

fixtures_ca_vig_page4.json is REAL — page.extract_words() output (text,
top, x0, x1 only) from the real 2024 general-election VIG PDF, page index
4, fetched directly during development. Not synthesized: this is the same
page that carries Proposition 2 (parses cleanly end to end, including
yes_means/no_means) and Proposition 3 (a real row-gap misclassification on
a wrapped line — see ballot_measure_pdf_geometry.looks_corrupted's
docstring — verified here to be safely dropped rather than shipped
scrambled).

Generic word-geometry/corruption-detection helpers this strategy relies
on (split_by_row_gap, looks_corrupted, etc.) are tested independently in
test_ballot_measure_pdf_geometry.py; the fetch/cache/upsert orchestration
this strategy plugs into is tested in test_ballot_measures_pdf.py.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from app.pipeline.fetch import ballot_measures_ca as ca

FIXTURE = json.loads((Path(__file__).parent / "fixtures_ca_vig_page4.json").read_text())


def _fake_page(words):
    return SimpleNamespace(extract_words=lambda: words)


def test_parses_two_real_propositions_from_one_page():
    results = ca.parse_quick_reference_page(_fake_page(FIXTURE))
    assert [r["number"] for r in results] == ["2", "3"]


def test_prop2_parses_cleanly_including_yes_no_framing():
    results = ca.parse_quick_reference_page(_fake_page(FIXTURE))
    prop2 = next(r for r in results if r["number"] == "2")
    assert prop2["origin"] == "the Legislature"
    assert prop2["official_summary"].startswith("Authorizes $10 billion")
    assert prop2["fiscal_impact"].startswith("Increased state costs")
    assert prop2["yes_means"] == (
        "The state could borrow $10 billion to build new or renovate "
        "existing public school and community college facilities."
    )
    assert prop2["no_means"] == (
        "The state could not borrow $10 billion to build new or renovate "
        "existing public school and community college facilities."
    )


def test_prop3_yes_no_framing_dropped_as_corrupted_not_guessed():
    """Prop 3's yes_means/no_means genuinely misclassify on the real PDF
    (see looks_corrupted). The rest of the proposition is unaffected —
    only the two suspect fields are dropped, not the whole record."""
    results = ca.parse_quick_reference_page(_fake_page(FIXTURE))
    prop3 = next(r for r in results if r["number"] == "3")
    assert prop3["yes_means"] is None
    assert prop3["no_means"] is None
    assert prop3["official_summary"].startswith("Amends California Constitution")


def test_page_with_no_quick_reference_content_returns_empty():
    assert ca.parse_quick_reference_page(_fake_page([])) == []
