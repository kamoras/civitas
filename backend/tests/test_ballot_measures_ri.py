"""Tests for Rhode Island's strategy (ballot_measures_ri.py).

fixtures_ri_voter_handbook_2026.json is REAL (see its _source): pages of
the 2026 Voter Information Handbook and each question's ballot box as
ballot_columns() cut it.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_ri as ri
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

FIXTURE = json.loads((Path(__file__).parent / "fixtures_ri_voter_handbook_2026.json").read_text())
PAGES = FIXTURE["pages"]
COLUMNS = {int(k): tuple(v) for k, v in FIXTURE["columns"].items()}


def test_five_bond_questions_with_the_states_own_framing():
    measures = ri.parse_handbook(PAGES, COLUMNS, 2026)
    assert [m["number"] for m in measures] == ["1", "2", "3", "4", "5"]
    one = measures[0]
    assert one["title"] == "HIGHER EDUCATION FACILITIES - $275,000,000"
    assert one["official_summary"] == (
        "For capital improvements to higher education facilities, to be allocated as follows:\n"
        "a. University of Rhode Island Integrated Health Building - $165,000,000\n"
        "b. Rhode Island College Adams Library Renovations - $50,000,000\n"
        "c. Community College of Rhode Island Workforce Innovation Center - $60,000,000"
    )
    # The whole sentences, from the right-hand column only.
    assert one["yes_means"] == (
        "Your vote to “Approve” means that you support the State issuing $275,000,000 in general "
        "obligation bonds for improvements to higher education facilities."
    )
    assert one["no_means"].startswith("Your vote to “Reject” means that you are against the State issuing $275,000,000")
    assert one["official_title"].startswith("Shall the act passed by the General Assembly at the January 2026 session")
    assert one["official_title"].endswith("as outlined in the law?")
    for m in measures:
        assert "Approve" not in m["official_summary"] and "Your vote" not in m["official_summary"]
        assert m["origin"] == ri.ORIGIN
        assert m["title_authority"] is None  # the handbook names no drafter


def test_the_general_question_is_never_attached_outside_its_stated_range():
    pages = [p.replace("Referenda Questions 1 – 5 involve", "Referenda Questions 1 – 4 involve") for p in PAGES]
    measures = ri.parse_handbook(pages, COLUMNS, 2026)
    assert measures[3]["official_title"] and measures[4]["official_title"] is None
    assert measures[4]["origin"] is None


def test_contents_and_question_pages_must_agree():
    contents = [p.replace("Question 5 - Green Economy Bonds", "Question 6 - Green Economy Bonds") for p in PAGES]
    assert ri.parse_handbook(contents, COLUMNS, 2026) is None
    missing_box = {k: v for k, v in COLUMNS.items() if k != max(COLUMNS)}
    assert ri.parse_handbook(PAGES, missing_box, 2026) is None


def test_framing_in_another_shape_is_refused():
    k = min(COLUMNS)
    left, right = COLUMNS[k]
    reworded = {**COLUMNS, k: (left, right.replace("Your vote to “Reject” means", "Rejecting means"))}
    assert ri.parse_handbook(PAGES, reworded, 2026) is None
    renumbered = {**COLUMNS, k: (left.replace("1. HIGHER", "2. HIGHER"), right)}
    assert ri.parse_handbook(PAGES, renumbered, 2026) is None


def test_refuses_another_years_handbook():
    assert ri.parse_handbook(PAGES, COLUMNS, 2028) is None


@pytest.mark.asyncio
async def test_missing_handbook_is_not_yet_published(monkeypatch):
    async def missing(client, url, label, awaited, **kw):
        assert url.endswith("/VoterRef28.pdf")
        raise NotYetPublished(awaited, deadline_applies=False)

    monkeypatch.setattr(ri, "get_bytes_unless_missing", missing)
    with pytest.raises(NotYetPublished):
        await ri.fetch_measures(None, 2028)
