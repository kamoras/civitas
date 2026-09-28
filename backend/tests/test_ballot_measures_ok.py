"""Tests for Oklahoma's ballot-measure strategy (ballot_measures_ok.py).

fixtures_ok_state_questions.json is REAL — the header row and SQ
844-848 rows of the Secretary of State's State Questions register,
fetched live 2026-09-28.
"""

import json
from pathlib import Path

from app.pipeline.fetch import ballot_measures_ok as ok

REGISTER = json.loads((Path(__file__).parent / "fixtures_ok_state_questions.json").read_text())["register"]


def test_only_the_two_november_2026_questions():
    rows = ok.parse_register(REGISTER, 2026)
    assert [(m["number"], m["title"]) for m, _ in rows] == [
        ("847", "Real Property Valuation"),
        ("845", "Judicial Nominating Commission"),
    ]
    # SQ 846 (August 25, 2026) and 848 (April 6, 2027) are other elections.
    assert rows[0][1] == "https://www.sos.ok.gov/documents/questions/847.pdf"


def test_no_ocr_text_is_presented_as_ballot_language():
    for m, _ in ok.parse_register(REGISTER, 2026):
        assert m["official_title"] is None
        assert m["official_summary"] is None
        assert m["yes_means"] is None and m["no_means"] is None
        assert m["origin"] == "Oklahoma Legislature"


def test_year_with_no_row_is_none_not_empty():
    assert ok.parse_register(REGISTER, 2030) is None


def test_general_election_label_follows_the_statutory_rule():
    assert ok._general_election_label(2026) == "November 3, 2026"
    assert ok._general_election_label(2028) == "November 7, 2028"
