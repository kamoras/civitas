"""Tests for federal_register.fetch_recent_significant_rules — the
early-signal source for Federal Register final rules. Filters to OMB's own
"significant under EO 12866" designation rather than any final rule, since
raw final-rule volume (~12/day, confirmed live) is overwhelmingly routine
administrative matters unusable as an early-signal feed."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.pipeline.fetch.federal_register import (
    _is_correction,
    fetch_recent_significant_rules,
)

SAMPLE_RULE = {
    "title": "Process for Authorizing Seasonal Migratory Game Bird Hunting",
    "abstract": "The U.S. Fish and Wildlife Service is changing the process.",
    "document_number": "2026-17733",
    "html_url": "https://www.federalregister.gov/documents/2026/08/31/2026-17733/process",
    "publication_date": "2026-08-31",
    "agencies": [{"name": "Interior Department"}, {"name": "Fish and Wildlife Service"}],
}

CORRECTION_RULE = {
    "title": "Modifications to the Regulations Implementing VEVRAA; Correction",
    "abstract": "This document corrects amendatory instructions.",
    "document_number": "2026-17757",
    "html_url": "https://www.federalregister.gov/documents/2026/08/31/2026-17757/correction",
    "publication_date": "2026-08-31",
    "agencies": [{"name": "Labor Department"}],
}


class TestIsCorrection:
    def test_correction_suffix_matches(self):
        assert _is_correction("Some Rule Title; Correction")

    def test_case_insensitive(self):
        assert _is_correction("Some Rule Title; CORRECTION")

    def test_correction_and_technical_amendment_matches(self):
        """Confirmed live (2026-17336): the real vocabulary is broader than
        the bare word "Correction"."""
        assert _is_correction("Streamlining Probationary and Trial Period Appeals; Correction and Technical Amendment")

    def test_correcting_amendments_matches(self):
        """Confirmed live (2026-17334)."""
        assert _is_correction("Recruitment and Relocation Incentive Waivers; Correcting Amendments")

    def test_ordinary_title_does_not_match(self):
        assert not _is_correction("Process for Authorizing Seasonal Migratory Game Bird Hunting")

    def test_correction_mid_title_without_semicolon_does_not_match(self):
        """A rule that merely discusses corrections in its body, with no
        semicolon-separated correction clause, isn't a correction notice."""
        assert not _is_correction("Correction of prior enforcement guidance")

    def test_correct_not_adjacent_to_semicolon_does_not_match(self):
        """"correct" appearing later in a substantive clause must not be
        mistaken for a correction-notice clause immediately after the
        semicolon."""
        assert not _is_correction("New Labeling Rule; Requiring Correct Nutritional Disclosures")


def _mock_client(results: list[dict]):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"results": results}
    response.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return client


class TestFetchRecentSignificantRules:
    @pytest.mark.asyncio
    async def test_parses_a_rule(self, db_session):
        client = _mock_client([SAMPLE_RULE])
        results = await fetch_recent_significant_rules(client, db_session)
        assert len(results) == 1
        r = results[0]
        assert r["title"] == SAMPLE_RULE["title"]
        assert r["documentNumber"] == "2026-17733"
        assert r["htmlUrl"] == SAMPLE_RULE["html_url"]
        assert r["publicationDate"] == "2026-08-31"
        assert r["agencies"] == ["Interior Department", "Fish and Wildlife Service"]

    @pytest.mark.asyncio
    async def test_correction_is_excluded(self, db_session):
        client = _mock_client([SAMPLE_RULE, CORRECTION_RULE])
        results = await fetch_recent_significant_rules(client, db_session)
        assert len(results) == 1
        assert results[0]["documentNumber"] == "2026-17733"

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self, db_session):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("boom"))
        results = await fetch_recent_significant_rules(client, db_session)
        assert results == []

    @pytest.mark.asyncio
    async def test_result_is_cached(self, db_session):
        client = _mock_client([SAMPLE_RULE])
        await fetch_recent_significant_rules(client, db_session)
        await fetch_recent_significant_rules(client, db_session)
        assert client.get.call_count == 1
