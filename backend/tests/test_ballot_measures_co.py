"""Tests for Colorado's ballot-measure PDF strategy: parsing the
Legislative Council's "Blue Book", "Quick Ballot Reference Guide"
section.

fixtures_co_bluebook_page6.json is REAL — page.extract_words() output
from the real 2024 general-election Blue Book, page index 6, which
carries two full measures (Amendments G and H) back to back — enough to
exercise the per-measure boundary logic (a title/badge that must not
pick up the PREVIOUS measure's "YES"/"NO" vote-means badges, and a
vote-means zone that must not run into the NEXT measure's title).
"""

import json
from pathlib import Path
from types import SimpleNamespace

from app.pipeline.fetch import ballot_measures_co as co

FIXTURE = json.loads((Path(__file__).parent / "fixtures_co_bluebook_page6.json").read_text())


def _fake_page(words):
    return SimpleNamespace(extract_words=lambda extra_attrs=None: words)


def test_parses_both_measures_on_the_page():
    results = co.parse_page(_fake_page(FIXTURE))
    assert [r["number"] for r in results] == ["G", "H"]


def test_first_measure_does_not_pick_up_the_next_measures_badge():
    """The regression this guards: title-sized "YES"/"NO" vote-means
    badges are as large as a real title, and an earlier version let
    measure G's window absorb measure H's badge/title text."""
    g = next(r for r in co.parse_page(_fake_page(FIXTURE)) if r["number"] == "G")
    assert g["title"] == "Modify Property Tax Exemption for Veterans with Disabilities"
    assert "Judicial" not in g["title"]


def test_vote_means_zone_does_not_bleed_into_next_measures_title():
    """The regression this guards: the vote-means paragraph has no
    marker of its own end, so an earlier version's NO text ran straight
    into the next measure's title ("...total. Judicial Discipline H
    Confidentiality Procedures and")."""
    g = next(r for r in co.parse_page(_fake_page(FIXTURE)) if r["number"] == "G")
    assert g["no_means"].endswith("100 percent permanent and total.")
    assert "Judicial" not in g["no_means"]


def test_yes_no_and_summary_match_the_real_source_text():
    h = next(r for r in co.parse_page(_fake_page(FIXTURE)) if r["number"] == "H")
    assert h["origin"] == "the legislature"
    assert h["official_summary"].startswith("Shall there be an amendment")
    assert h["yes_means"] == (
        "creates an independent adjudicative board made up of citizens, "
        "lawyers, and judges to conduct judicial misconduct hearings and "
        "impose disciplinary actions, and allows more information to be "
        "shared earlier with the public."
    )
    assert h["no_means"] == (
        "means that a select panel of judges will continue to conduct "
        "judicial misconduct hearings and recommend disciplinary actions, "
        "and cases remain confidential unless public sanctions are "
        "recommended at the end of the process."
    )


def test_no_fiscal_impact_field_in_this_section():
    """Colorado's quick-reference section never publishes one — verified
    real, not a parsing gap (see module docstring)."""
    for r in co.parse_page(_fake_page(FIXTURE)):
        assert r["fiscal_impact"] is None


def test_page_with_no_measures_returns_empty():
    assert co.parse_page(_fake_page([])) == []
