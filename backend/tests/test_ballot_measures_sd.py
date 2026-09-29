"""Tests for South Dakota's ballot-measure strategy (ballot_measures_sd.py).

fixtures_sd_ballot_questions.json is REAL — the 2026 Ballot Questions
page's Ballot Question Information and General Election Ballot Measures
sections (plus the first "Approved for Circulation" entry, which must
not be read), and extract_text() of every page of the 2026 Ballot
Question Pro/Con Pamphlet, fetched live 2026-09-28.
"""

import json
from pathlib import Path

from app.pipeline.fetch import ballot_measures_sd as sd

FIXTURE = json.loads((Path(__file__).parent / "fixtures_sd_ballot_questions.json").read_text())


def test_certified_section_only():
    assert sd.certified_questions(FIXTURE["page"], 2026) == {
        "I": "South Dakota Legislature", "J": "South Dakota Legislature",
        "K": "South Dakota Legislature", "L": "South Dakota Legislature",
    }
    assert sd.certified_questions(FIXTURE["page"], 2028) is None


def test_pamphlet_link_is_the_english_pdf():
    url = sd.pamphlet_url(FIXTURE["page"], 2026)
    assert url.endswith("/2026%20BQ/2026GeneralBQPamphletFINAL.pdf")
    assert " " not in url


def test_page_url_encodes_the_years_folder():
    assert sd.page_url(2026) == (
        "https://sdsos.gov/elections-voting/upcoming-elections/general-information/"
        "2026%20Election%20Information/2026-ballot-questions.aspx"
    )


def test_real_pamphlet_four_amendments():
    pamphlet = sd.parse_pamphlet(FIXTURE["pamphlet_pages"])
    assert sorted(pamphlet) == ["I", "J", "K", "L"]
    i = pamphlet["I"]
    assert i["title"] == "Constitutional Amendment I"
    assert i["official_title"].startswith("An Amendment to the South Dakota Constitution that Repeals Expanded Medicaid")
    assert i["official_summary"].startswith("Medicaid is a program, funded by the State")
    # A word wrapped at its hyphen is rejoined as the word, not "low- income".
    assert "low-income children, pregnant women, disabled individuals, and the elderly." in i["official_summary"]
    assert "low- income" not in i["official_summary"]
    assert i["yes_means"] == "Vote “Yes” to adopt the amendment."
    assert i["no_means"] == "Vote “No” to leave the Constitution as it is."
    assert i["title_authority"] == "South Dakota Attorney General"
    for m in pamphlet.values():
        # Proponent/opponent statements never leak in.
        assert "Pro –" not in m["official_summary"]
        assert "The text of this constitutional amendment" not in m["official_summary"]
        assert m["fiscal_impact"] is None


def test_combine_requires_the_same_set():
    pamphlet = sd.parse_pamphlet(FIXTURE["pamphlet_pages"])
    certified = sd.certified_questions(FIXTURE["page"], 2026)
    assert [m["number"] for m in sd.combine(certified, pamphlet)] == ["I", "J", "K", "L"]
    assert sd.combine({**certified, "M": None}, pamphlet) is None
    del certified["L"]
    assert sd.combine(certified, pamphlet) is None


def test_a_wrapped_vote_sentence_is_kept_whole():
    """The regression: only a "Vote ..." sentence's first line was kept, so
    a recitation wrapped over two lines was stored cut off mid-sentence."""
    pages = [p.replace("Vote “Yes” to adopt the amendment.", "Vote “Yes” to adopt\nthe amendment.", 1) for p in FIXTURE["pamphlet_pages"]]
    assert pages != FIXTURE["pamphlet_pages"]
    i = sd.parse_pamphlet(pages)["I"]
    assert i["yes_means"] == "Vote “Yes” to adopt the amendment."
    assert i["no_means"] == "Vote “No” to leave the Constitution as it is."


def test_a_vote_sentence_that_never_finishes_refuses_the_page():
    pages = [p.replace("Vote “No” to leave the Constitution as it is.", "Vote “No” to leave the Constitution", 1) for p in FIXTURE["pamphlet_pages"]]
    assert sd.parse_pamphlet(pages) is None


def test_a_certified_entry_without_its_assigned_letter_refuses_the_page():
    """The regression: an entry missing "Letter Assigned:" was skipped, and
    with every entry skipped the section read as [] — confirmed none."""
    page = FIXTURE["page"].replace("Letter Assigned:", "Letter:")
    assert page != FIXTURE["page"]
    assert sd.certified_questions(page, 2026) is None
    one_missing = FIXTURE["page"].replace("Letter Assigned:", "Letter:", 1)
    assert sd.certified_questions(one_missing, 2026) is None
