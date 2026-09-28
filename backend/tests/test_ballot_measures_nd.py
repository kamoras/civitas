"""Tests for North Dakota's ballot-measure strategy (ballot_measures_nd.py).

fixtures_nd_measures.json is REAL — the Measures on Ballot page's
content block and extract_text() of both 2026 "Official Ballot
Language" PDFs, fetched live 2026-09-28.
"""

import json
from pathlib import Path

from app.pipeline.fetch import ballot_measures_nd as nd

FIXTURE = json.loads((Path(__file__).parent / "fixtures_nd_measures.json").read_text())


class TestIndex:
    def test_two_ballot_language_links_under_2026_general(self):
        links = nd.ballot_language_links(FIXTURE["index"], 2026)
        assert links == [
            "/sites/default/files/documents/elections/measures/ballot-language-2026-general-measure1.pdf",
            "/sites/default/files/documents/elections/measures/ballot-language-2026-general-measure2.pdf",
        ]

    def test_other_year_is_none(self):
        assert nd.ballot_language_links(FIXTURE["index"], 2028) is None

    def test_measure_without_its_pdf_yet_refuses(self):
        html = FIXTURE["index"].replace("ballot-language-2026-general-measure2.pdf\" data-entity-type", "x.pdf\" data-entity-type")
        html = html.replace("<strong>Official Ballot Language</strong></a></li><li class=\"ck-list-marker-bold\" data-list-item-id=\"e5aafcb75b79", "<strong>Pending</strong></a></li><li class=\"ck-list-marker-bold\" data-list-item-id=\"e5aafcb75b79")
        assert nd.ballot_language_links(html, 2026) is None


class TestBallotLanguage:
    def test_legislative_measure_1(self):
        m = nd.parse_ballot_language(FIXTURE["ballot_language_1"])
        assert m["number"] == "1"
        assert m["title"] == "Constitutional Measure No. 1"
        assert m["origin"] == "North Dakota Legislative Assembly"
        assert m["official_summary"].startswith("This constitutional measure would amend and reenact section 9")
        assert "House Concurrent Resolution" not in m["official_summary"]
        assert m["fiscal_impact"] == "The estimated fiscal impact of this measure is none."
        assert m["yes_means"] == "Means you approve the measure as summarized above."
        assert m["no_means"] == "Means you reject the measure as summarized above."
        assert m["fiscal_authority"] == "North Dakota Legislative Council"

    def test_initiated_measure_2_wrapped_heading(self):
        m = nd.parse_ballot_language(FIXTURE["ballot_language_2"])
        assert m["number"] == "2"
        assert m["title"] == "Initiated Constitutional Measure No. 2"
        assert m["origin"] == "North Dakota voters (initiative petition)"
        assert m["official_summary"].endswith("supersede any other part of the constitution that conflicts with it.")
        assert m["fiscal_impact"].startswith("The estimated fiscal impact of this measure is a $124.3 million")
        assert "2027–2029 biennium" in m["fiscal_impact"]

    def test_missing_framing_refuses(self):
        text = FIXTURE["ballot_language_1"].replace("No – Means", "No Means")
        assert nd.parse_ballot_language(text) is None

    def test_a_wrapped_yes_or_no_sentence_is_kept_whole(self):
        """The regression: only each sentence's first line was kept, so a
        "Means ..." sentence wrapped over two lines was stored truncated."""
        text = (
            FIXTURE["ballot_language_1"]
            .replace("Yes – Means you approve the measure as summarized above.",
                     "Yes – Means you approve the measure as\nsummarized above.")
            .replace("No – Means you reject the measure as summarized above.",
                     "No – Means you reject the measure\nas summarized above.")
        )
        m = nd.parse_ballot_language(text)
        assert m["yes_means"] == "Means you approve the measure as summarized above."
        assert m["no_means"] == "Means you reject the measure as summarized above."

    def test_a_sentence_that_never_finishes_refuses(self):
        text = FIXTURE["ballot_language_1"].replace(
            "No – Means you reject the measure as summarized above.", "No – Means you reject the measure",
        )
        assert nd.parse_ballot_language(text) is None
        trailing = FIXTURE["ballot_language_1"] + "\nSomething after the framing"
        assert nd.parse_ballot_language(trailing) is None
