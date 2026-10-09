"""Tests for New Mexico's strategy (ballot_measures_nm.py).

fixtures_nm_sos_questions_2026.json is REAL (see its _source): the
Secretary of State's 2026 statewide amendments and bond questions
publication, extract_text() per page, and the home page's links to it.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_nm as nm
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

FIXTURE = json.loads((Path(__file__).parent / "fixtures_nm_sos_questions_2026.json").read_text())
PAGES = FIXTURE["pages"]


def test_amendments_and_bond_questions_in_the_ballots_own_wording():
    measures = nm.parse_publication(PAGES, 2026)
    assert [m["number"] for m in measures] == ["1", "2", "3", "4", "Bond Question 1", "Bond Question 2", "Bond Question 3"]
    one = measures[0]
    assert one["title"] == "Constitutional Amendment 1"
    # Sentence case, as the printed ballot reads — not the joint
    # resolution's title in capitals, which the Legislative Council
    # Service's publication prints.
    assert one["official_title"].startswith(
        "Proposing an amendment to Article 4, Section 22 of the Constitution of New Mexico"
    )
    assert one["official_title"].endswith("will become law.")
    bond = measures[4]
    assert bond["title"] == (
        "The 2026 Capital Projects General Obligation Bond Act authorizes the issuance and sale of senior "
        "citizen facility improvement, construction and equipment acquisition bonds."
    )
    assert bond["official_summary"].endswith("collection of the tax as permitted by law?")
    assert "($21,582,213)" in bond["official_summary"]
    for m in measures:
        assert "Enmienda" not in str(m) and "Pregunta" not in str(m)
        assert m["yes_means"] is None and m["fiscal_impact"] is None


def test_refuses_another_years_publication():
    assert nm.parse_publication(PAGES, 2028) is None


def test_a_question_missing_from_the_english_half_is_caught_by_the_spanish_count():
    english = PAGES[1].replace("Bond Question 3\n", "")
    assert english != PAGES[1]
    assert nm.parse_publication([PAGES[0], english] + PAGES[2:], 2026) is None


def test_an_item_in_another_shape_is_refused():
    reshaped = PAGES[0].replace("Constitutional Amendment 2:\n", "Constitutional Amendment 2: ")
    assert reshaped != PAGES[0]
    assert nm.parse_publication([reshaped] + PAGES[1:], 2026) is None


def test_the_home_page_link_is_found():
    url, ok = nm.find_publication_url(FIXTURE["home_links"], 2026)
    assert ok and url.endswith("-2026-CA-and-GOB-Questions-final.pdf")
    assert nm.find_publication_url(FIXTURE["home_links"], 2028) == (None, True)


@pytest.mark.asyncio
async def test_fetch_paths(monkeypatch):
    page = {"html": FIXTURE["home_links"]}

    async def get_text(client, url, label, **kw):
        return page["html"]

    async def get_bytes(client, url, label, **kw):
        return b"%PDF-fixture"

    class _Pdf:
        pages = [type("P", (), {"extract_text": (lambda self, t=t: t)})() for t in PAGES]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(nm, "get_text", get_text)
    monkeypatch.setattr(nm, "get_bytes", get_bytes)
    monkeypatch.setattr(nm.pdfplumber, "open", lambda f: _Pdf())
    pairs = await nm.fetch_measures(None, 2026)
    assert len(pairs) == 7 and all(url.endswith(".pdf") for _, url in pairs)

    page["html"] = "<html><body><a href='/x.pdf'>2026 Voter Guide</a></body></html>"
    with pytest.raises(NotYetPublished):
        await nm.fetch_measures(None, 2026)
