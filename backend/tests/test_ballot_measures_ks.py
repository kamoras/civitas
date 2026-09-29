"""Tests for Kansas's ballot-measure strategy (ballot_measures_ks.py).

fixtures_ks_proposed_amendments.json is REAL — the content column of
sos.ks.gov/elections/proposed-constitutional-amendments.html as fetched
live 2026-09-28 (the November 3, 2026 general's citizenship amendment)
and as captured by the Wayback Machine on 2026-07-10 (the August 4,
2026 primary's judicial-election amendment, on the same URL).
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_ks as ks
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

FIXTURE = json.loads((Path(__file__).parent / "fixtures_ks_proposed_amendments.json").read_text())


class TestParsePage:
    def test_real_2026_general_amendment(self):
        [m] = ks.parse_page(FIXTURE["general_2026"], 2026)
        assert m["number"] == "1"
        assert m["title"] == "Constitutional Amendment 1"
        assert m["origin"] == "Kansas Legislature"
        assert m["official_summary"] == (
            "This amendment would clarify that only a person who is a citizen of the "
            "United States is eligible to vote in this state."
        )
        # Kansas's own framing, whole sentences, verbatim.
        assert m["yes_means"] == (
            "A vote for this proposition would clarify that only a person who is a "
            "citizen of the United States is eligible to vote in this state."
        )
        assert m["no_means"].startswith("A vote against this proposition would make no change")
        assert m["no_means"].endswith("would remain the same.")
        assert m["fiscal_impact"] is None

    def test_primary_page_is_not_yet_this_years_general(self):
        """Same URL in July 2026 carried the August primary's amendment:
        November's isn't posted yet. Not yet covered — it used to be None,
        an ingest-failure page every night until the rewrite."""
        with pytest.raises(NotYetPublished):
            ks.parse_page(FIXTURE["primary_2026"], 2026)

    def test_other_year_is_not_yet_published_never_empty(self):
        with pytest.raises(NotYetPublished):
            ks.parse_page(FIXTURE["general_2026"], 2028)

    def test_a_page_without_the_introducing_sentence_is_a_failure(self):
        broken = FIXTURE["general_2026"].replace("The following constitutional amendment will be voted on", "Voters will decide")
        assert broken != FIXTURE["general_2026"]
        assert ks.parse_page(broken, 2026) is None

    def test_the_drafter_of_the_quoted_text_is_named(self):
        [m] = ks.parse_page(FIXTURE["general_2026"], 2026)
        assert m["title_authority"] == "Kansas Legislature"
        assert m.get("official_title") is None

    def test_missing_against_line_refuses_the_page(self):
        broken = FIXTURE["general_2026"].replace("A vote against this proposition", "A vote on this")
        assert ks.parse_page(broken, 2026) is None


class TestFetch:
    async def test_not_yet_published_reaches_the_pipeline(self, monkeypatch):
        """parse_page's NotYetPublished must not be swallowed by the fetch's
        catch-all into None (ingest_failed)."""
        async def primary_page(*a, **kw):
            return FIXTURE["primary_2026"]

        monkeypatch.setattr(ks, "fetch_text_with_retry", primary_page)
        with pytest.raises(NotYetPublished):
            await ks.fetch_measures(None, 2026)
