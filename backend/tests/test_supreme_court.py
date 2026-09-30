"""Tests for the Supreme Court opinion fetcher.

Tests the Oyez API response parsing, date conversion, HTML stripping,
and deduplication logic without making real HTTP requests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.pipeline.fetch.supreme_court import (
    _strip_html,
    _unix_to_date,
    fetch_scotus_cases,
)


@pytest.mark.parametrize("raw, expected", [
    pytest.param("<p>Hello <b>world</b></p>", "Hello world", id="removes_tags"),
    pytest.param("", "", id="empty"),
    pytest.param(None, "", id="none"),
    pytest.param("No tags here", "No tags here", id="plain_text_unchanged"),
    pytest.param("  <p>text</p>  ", "text", id="strips_whitespace"),
])
def test_strip_html(raw, expected):
    assert _strip_html(raw) == expected


@pytest.mark.parametrize("ts, expected", [
    pytest.param(1740117600, "2025-02-21", id="valid_timestamp"),
    pytest.param(None, "", id="none_returns_empty"),
    pytest.param(0, "1970-01-01", id="zero_returns_date"),
])
def test_unix_to_date(ts, expected):
    assert _unix_to_date(ts) == expected


class TestFetchScotusCases:
    SAMPLE_CASE = {
        "ID": 63649,
        "name": "Williams v. Reed",
        "docket_number": "23-191",
        "timeline": [
            {"event": "Granted", "dates": [1705039200]},
            {"event": "Argued", "dates": [1728277200]},
            {"event": "Decided", "dates": [1740117600]},
        ],
        "question": "<p>Does a Section 1983 claim require exhaustion?</p>",
        "citation": {"volume": "604", "page": "123", "year": "2025"},
        "term": "2024",
        "description": "The Court held that exhaustion is not required.",
        "justia_url": "https://supreme.justia.com/cases/federal/us/604/23-191/",
    }

    def _mock_client(self, cases: list[dict]):
        """Build a mock httpx.AsyncClient that returns the given cases."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = cases

        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        return client

    @pytest.mark.asyncio
    async def test_basic_parse(self):
        client = self._mock_client([self.SAMPLE_CASE])
        results = await fetch_scotus_cases(client, terms=["2024"])
        assert len(results) == 1
        r = results[0]
        assert r["title"] == "Williams v. Reed (No. 23-191)"
        assert r["date"] == "2025-02-21"
        assert r["doc_type"] == "Supreme Court Opinion"
        assert r["chamber"] == "Judicial"
        assert "exhaustion" in r["summary"].lower()
        assert "604 U.S. 123" in r["body"]
        assert r["url"] == "https://www.supremecourt.gov/docket/docketfiles/html/public/23-191.html"
        assert r["external_id"] == "scotus-2024-23-191"

    @pytest.mark.asyncio
    async def test_skips_undecided_cases(self):
        undecided = {
            **self.SAMPLE_CASE,
            "timeline": [{"event": "Granted", "dates": [1705039200]}],
        }
        client = self._mock_client([undecided])
        results = await fetch_scotus_cases(client, terms=["2024"])
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_deduplication(self):
        client = self._mock_client([self.SAMPLE_CASE, self.SAMPLE_CASE])
        results = await fetch_scotus_cases(client, terms=["2024"])
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_html_stripped_from_question(self):
        client = self._mock_client([self.SAMPLE_CASE])
        results = await fetch_scotus_cases(client, terms=["2024"])
        assert "<p>" not in results[0]["body"]
        assert "</p>" not in results[0]["body"]

    @pytest.mark.asyncio
    async def test_missing_citation_handled(self):
        case = {**self.SAMPLE_CASE, "citation": {}}
        client = self._mock_client([case])
        results = await fetch_scotus_cases(client, terms=["2024"])
        assert len(results) == 1
        assert "U.S." not in results[0]["body"]

    @pytest.mark.asyncio
    async def test_api_error_handled(self):
        response = MagicMock()
        response.status_code = 500
        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        results = await fetch_scotus_cases(client, terms=["2024"])
        assert results == []

    @pytest.mark.asyncio
    async def test_multiple_terms(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [self.SAMPLE_CASE]

        case_2023 = {
            **self.SAMPLE_CASE,
            "docket_number": "22-448",
            "name": "CFPB v. CFSA",
        }
        response_2023 = MagicMock()
        response_2023.status_code = 200
        response_2023.json.return_value = [case_2023]

        client = AsyncMock()
        client.get = AsyncMock(side_effect=[response, response_2023])
        results = await fetch_scotus_cases(client, terms=["2024", "2023"])
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_no_description_uses_question_as_summary(self):
        case = {**self.SAMPLE_CASE, "description": None}
        client = self._mock_client([case])
        results = await fetch_scotus_cases(client, terms=["2024"])
        assert "1983" in results[0]["summary"]
