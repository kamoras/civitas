"""Tests for Oklahoma's ballot-measure strategy (ballot_measures_ok.py).

fixtures_ok_state_questions.json is REAL — the header row and SQ
844-848 rows of the Secretary of State's State Questions register,
fetched live 2026-09-28.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_ok as ok
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

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


def test_year_with_no_row_is_not_yet_published_never_none_or_empty(monkeypatch):
    """The register isn't a certified list, so no row for the election
    can't be "none" — and it isn't a failure (it used to be None, an
    ingest-failure page every night of a year with no State Question)."""
    assert ok.parse_register(REGISTER, 2030) == []

    async def get_text(*a, **kw):
        return REGISTER

    monkeypatch.setattr(ok, "fetch_text_with_retry", get_text)
    with pytest.raises(NotYetPublished):
        asyncio.run(ok.fetch_measures(None, 2030))
    assert len(asyncio.run(ok.fetch_measures(None, 2026))) == 2


def test_an_election_date_in_another_form_refuses_the_register():
    """A row dated this election in a form the date pattern doesn't know
    used to be skipped as undated — dropping a real State Question."""
    reworded = REGISTER.replace("ELECTION DATE:  November 3, 2026", "ELECTION DATE: Nov. 3, 2026", 1)
    assert reworded != REGISTER
    assert ok.parse_register(reworded, 2026) is None


def test_general_election_label_follows_the_statutory_rule():
    assert ok._general_election_label(2026) == "November 3, 2026"
    assert ok._general_election_label(2028) == "November 7, 2028"
