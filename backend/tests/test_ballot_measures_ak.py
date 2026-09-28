"""Tests for Alaska's sample-ballot strategy (ballot_measures_ak.py).

fixtures_ak_sample_ballot_hd1.json is REAL — pdfplumber
extract_words(extra_attrs=["fontname", "size"]) output (subset-font
prefixes stripped, coordinates rounded to 0.1pt) for page 2 of the
Division of Elections' House District 1 general-election sample ballot,
fetched 2026-09-28 for both 2026 (.../election/2026/General/SampleBallots/
HD1-JD1.pdf) and 2024 (same pattern). The page's three columns share
y-positions: the measures in the left column, judicial-retention
questions beside them.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_ak as ak

FIXTURE = json.loads((Path(__file__).parent / "fixtures_ak_sample_ballot_hd1.json").read_text())


def _measures(year: str) -> dict[str, dict]:
    page = FIXTURE[year]
    return {m["number"]: m for m in ak.parse_page_words(page["words"], page["width"])}


def test_2026_general_carries_ballot_measures_2_and_3():
    # No. 1 was on the August 2026 primary: numbers are read, not assumed.
    assert sorted(_measures("2026")) == ["2", "3"]


def test_2024_general_carries_ballot_measures_1_and_2():
    assert sorted(_measures("2024")) == ["1", "2"]


def test_title_and_summary_are_cropped_to_the_measure_column():
    two = _measures("2026")["2"]
    assert two["title"] == (
        "24ESEG - An Act Restoring Political Party Primaries, Ending Ranked-Choice "
        "General Elections, and Repealing Recent Campaign Finance Laws"
    )
    assert two["official_summary"].startswith("This act would get rid of open primary elections,")
    assert two["official_summary"].endswith("remove some fines. Should this initiative become law?")
    # Neighbouring judicial-retention column must not leak in.
    for leak in ("Carpeneti", "retained", "YES NO", "Judge"):
        assert leak not in two["official_summary"]
        assert leak not in two["title"]


def test_origin_comes_from_the_ballot_question_and_no_framing_is_invented():
    three = _measures("2026")["3"]
    assert three["official_summary"] == (
        "Alaska law requires that someone must be a United States citizen and meet other "
        "requirements to vote. This act would specify that only those who are United States "
        "citizens and meet the other requirements may vote. This act would not change the "
        "requirements to vote. Should this initiative become law?"
    )
    assert three["origin"] == "Alaska voters (initiative petition)"
    assert three["yes_means"] is None and three["no_means"] is None
    assert three["fiscal_impact"] is None
    assert three["title_authority"]


def test_a_recognised_heading_without_a_yes_no_close_fails_the_document():
    page = FIXTURE["2026"]
    # Drop the YES/NO row under Ballot Measure No. 3 (left column, below
    # its question) — the measure no longer closes the expected way.
    words = [w for w in page["words"] if not (w["x0"] < 220 and 795 < w["top"] < 810)]
    with pytest.raises(ValueError):
        ak.parse_page_words(words, page["width"])


def test_page_without_any_measure_has_no_measures():
    # Only the judicial-retention column (x >= 220) of the real page.
    words = [w for w in FIXTURE["2026"]["words"] if w["x0"] >= 220]
    assert ak.parse_page_words(words, FIXTURE["2026"]["width"]) == []


def test_measure_text_without_a_recognised_heading_is_a_failure_not_none():
    # Heading word removed; "a 2020 ballot measure." remains in the body.
    words = [w for w in FIXTURE["2026"]["words"] if w["text"] != "Ballot"]
    with pytest.raises(ValueError):
        ak.parse_page_words(words, FIXTURE["2026"]["width"])


class _Page:
    def __init__(self, words, width):
        self._words, self.width = words, width

    def extract_words(self, **kw):
        return self._words


_HEADER = [
    {"text": t, "x0": 10.0 + 40 * i, "x1": 45.0 + 40 * i, "top": 20.0, "bottom": 30.0,
     "fontname": "Arial", "size": 10.0}
    for i, t in enumerate("State of Alaska Official Ballot November 3, 2026 General Election".split())
]


def test_document_reads_measures_under_the_ballots_own_header():
    page = FIXTURE["2026"]
    measures = ak.parse_document([_Page(_HEADER, page["width"]), _Page(page["words"], page["width"])])
    assert [m["number"] for m in measures] == ["2", "3"]
    # The bold title is the Lieutenant Governor's ballot title, printed on
    # the ballot — it is the official title.
    assert measures[0]["official_title"] == measures[0]["title"]


def test_a_document_with_no_readable_text_is_a_failure_never_none():
    """The regression: a scanned sample ballot (no text layer) has no
    words, so no measure heading, so [] — confirmed none."""
    with pytest.raises(ValueError):
        ak.parse_document([_Page([], 612.0), _Page([], 612.0)])
