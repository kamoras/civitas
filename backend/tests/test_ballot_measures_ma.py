"""Tests for Massachusetts's ballot-measure PDF strategy: parsing the
Secretary of the Commonwealth's own "Information For Voters" guide.

Fixtures are REAL — page.extract_words() output from the real 2024
general-election guide, fetched directly during development:

- fixtures_ma_ifv_q1_page5.json: Question 1, entirely on one page — the
  simple case (short summary, no real second column needed for it).
- fixtures_ma_ifv_q3_page{12,13}.json: Question 3, split across two
  pages — its summary alone fills page 12; "WHAT YOUR VOTE WILL DO" and
  fiscal impact don't appear until page 13, with the "QUESTION 3: ..."
  header repeated at the top of that page too. This is the real case
  that requires scanning multiple pages per question (see module
  docstring's challenge 1) — Q1 alone wouldn't catch a regression there.

Generic geometry helpers (find_column_boundary, looks_corrupted, etc.)
are tested independently in test_ballot_measure_pdf_geometry.py; the
generic fetch/cache/dispatch stage this strategy plugs into is tested in
test_ballot_measures_pdf.py.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from app.pipeline.fetch import ballot_measures_ma as ma


def _fixture(name):
    return json.loads((Path(__file__).parent / f"fixtures_ma_ifv_{name}.json").read_text())


def _fake_page(name):
    words = _fixture(name)
    return SimpleNamespace(extract_words=lambda extra_attrs=None: words)


def test_single_page_question_parses_cleanly():
    results = ma.parse_information_for_voters([_fake_page("q1_page5")])
    assert len(results) == 1
    q1 = results[0]
    assert q1["number"] == "1"
    assert q1["title"] == "State Auditor’s Authority to Audit the Legislature"
    assert q1["origin"] == "Law Proposed by Initiative Petition"
    assert q1["official_summary"] == (
        "This proposed law would specify that the State Auditor has the "
        "authority to audit the Legislature."
    )
    assert q1["fiscal_impact"] == (
        "The proposed law has no discernible material fiscal consequences "
        "for state and municipal government finances."
    )
    assert q1["yes_means"] == (
        "would specify that the State Auditor has the authority to audit "
        "the Legislature."
    )
    assert q1["no_means"] == (
        "would make no change in the law relative to the State Auditor’s "
        "authority."
    )


def test_question_spanning_two_pages_reunites_its_fields():
    """Q3's real content: summary fills page 12 entirely, "WHAT YOUR VOTE
    WILL DO" and fiscal impact don't appear until page 13. Neither page
    alone has everything — only scanning both together produces a
    complete, correct record. This is the regression Q1 alone can't
    catch (it never needed cross-page stitching)."""
    results = ma.parse_information_for_voters([
        _fake_page("q3_page12"), _fake_page("q3_page13"),
    ])
    assert len(results) == 1
    q3 = results[0]
    assert q3["number"] == "3"
    assert q3["official_summary"].startswith(
        "The proposed law would provide Transportation Network Drivers"
    )
    assert q3["official_summary"].endswith("would stay in effect.")
    assert q3["yes_means"] == (
        "would provide transportation network drivers the option to form "
        "unions to collectively bargain with transportation network "
        "companies regarding wages, benefits, and terms and conditions of "
        "work."
    )
    assert q3["no_means"] == (
        "would make no change in the law relative to the ability of "
        "transportation network drivers to form unions."
    )


def test_a_lone_page_from_a_multi_page_question_is_incomplete_alone():
    """Page 12 by itself has Q3's summary but not its WHAT/fiscal zones —
    confirms the single-page result really is partial (and therefore
    that the two-page test above is exercising real stitching, not
    accidentally getting everything from one page)."""
    results = ma.parse_information_for_voters([_fake_page("q3_page12")])
    assert len(results) == 1
    assert results[0]["yes_means"] is None
    assert results[0]["no_means"] is None


def test_page_with_no_question_marker_returns_empty():
    assert ma.parse_information_for_voters([_fake_page("q1_page5")][:0]) == []


def test_prose_reads_single_column_in_natural_order_when_no_real_split():
    """A short block with no genuine second column (find_column_boundary
    returns None) must not get an arbitrary gap-based split — see
    ballot_measure_pdf_geometry.find_column_boundary's docstring for the
    real failure this guards against."""
    words = [
        {"text": "This", "top": 0, "x0": 113, "x1": 130},
        {"text": "is", "top": 0, "x0": 134, "x1": 140},
        {"text": "fine.", "top": 0, "x0": 144, "x1": 165},
    ]
    assert ma._prose(words) == "This is fine."


def test_yes_no_returns_none_none_when_block_is_not_two_column():
    words = [{"text": "solo", "top": 0, "x0": 113, "x1": 130}]
    assert ma._yes_no(words) == (None, None)
