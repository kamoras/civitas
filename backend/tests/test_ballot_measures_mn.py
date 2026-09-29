"""Tests for Minnesota's ballot-measure strategy (ballot_measures_mn.py).

fixtures_mn_constitutional_amendments.json is REAL — the content of the
Secretary of State's What's On My Ballot > Constitutional amendments
page, fetched live 2026-09-28.
"""

import json
from pathlib import Path

from app.pipeline.fetch import ballot_measures_mn as mn

PAGE = json.loads((Path(__file__).parent / "fixtures_mn_constitutional_amendments.json").read_text())["page_2026"]


class TestParsePage:
    def test_real_2026_amendment(self):
        [m] = mn.parse_page(PAGE, 2026)
        assert m["title"] == "Increasing funding to school districts"
        assert m["official_summary"].startswith("Shall the Minnesota Constitution be amended to increase")
        assert m["official_summary"].endswith("effective July 1, 2027?")
        assert m["yes_means"] is None and m["no_means"] is None
        assert m["title_authority"] == "Minnesota Legislature"

    def test_stated_count_must_match(self):
        doubled = PAGE.replace("there will be one proposed", "there will be two proposed")
        assert mn.parse_page(doubled, 2026) is None

    def test_explicit_none_statement_is_empty(self):
        none = PAGE.replace("there will be one proposed constitutional amendment", "there will be no proposed constitutional amendments")
        assert mn.parse_page(none, 2026) == []

    def test_no_count_sentence_is_a_failure(self):
        # The bot manager's challenge page has no count sentence at all.
        assert mn.parse_page("<html><body><p>Please verify you are human</p></body></html>", 2026) is None
        assert mn.parse_page(PAGE, 2028) is None
