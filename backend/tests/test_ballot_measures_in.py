"""Tests for Indiana's ballot-measure strategy (ballot_measures_in.py).

fixtures_in_legislation_summary.json is REAL — two links from in.gov's
Indiana Election Legislation Summaries index, and pdf_text() of the
"Summary of 2026 Election Legislation" PDF from just before its INDIANA
CONSTITUTIONAL AMENDMENTS section to the end, fetched live 2026-09-28.
"""

import json
from pathlib import Path

from app.pipeline.fetch import ballot_measures_in as ind

FIXTURE = json.loads((Path(__file__).parent / "fixtures_in_legislation_summary.json").read_text())


def test_summary_link_is_found_by_its_text():
    url = ind.find_summary_url(FIXTURE["index"], 2026)
    assert url == "https://www.in.gov/dA/d13ecb1f55/2026-Indiana-Election-Legislation-Summary.FINAL.pdf?language_id=1"
    assert ind.find_summary_url(FIXTURE["index"], 2030) is None


def test_both_real_public_questions_verbatim():
    one, two = ind.parse_summary(FIXTURE["summary_text"], 2026)
    assert (one["number"], one["title"]) == ("1", "Public Question #1")
    assert one["official_summary"].startswith("Currently, under the Constitution of the State of Indiana")
    assert one["official_summary"].endswith("(This question concerns Article 1, Section 17 of the Constitution of the State of Indiana.)")
    assert (two["number"], two["title"]) == ("2", "Public Question #2")
    assert two["official_summary"].startswith("Shall the Constitution of the State of Indiana be amended to permit")
    # The page-break page number ("21") never lands in ballot text.
    assert " 21 " not in two["official_summary"]
    for m in (one, two):
        assert m["origin"] == "Indiana General Assembly"
        assert m["yes_means"] is None and m["fiscal_impact"] is None
        # The Division's own topic heading is not ballot text.
        assert "Limitations on the Right to Bail" not in m["official_summary"]


def test_questions_dated_to_another_election_are_not_read():
    assert ind.parse_summary(FIXTURE["summary_text"], 2028) is None


def test_no_question_found_is_never_confirmed_none():
    assert ind.parse_summary("SUMMARY OF 2027 ELECTION LEGISLATION\nNothing here.", 2027) is None
