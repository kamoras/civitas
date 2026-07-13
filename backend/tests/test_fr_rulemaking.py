"""Tests for the Federal Register rulemaking fetcher.

Tests API response parsing, agency extraction, comment metadata,
deduplication, and error handling without real HTTP requests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.pipeline.fetch.fr_rulemaking import (
    _primary_agency,
    fetch_fr_rulemaking,
)


class TestPrimaryAgency:
    def test_single_agency(self):
        agencies = [{"name": "EPA", "parent_id": None}]
        assert _primary_agency(agencies) == "EPA"

    def test_parent_and_child(self):
        agencies = [
            {"name": "Transportation Department", "parent_id": None},
            {"name": "FAA", "parent_id": 492},
        ]
        assert _primary_agency(agencies) == "Transportation Department"

    def test_only_child_agency(self):
        agencies = [{"name": "FAA", "parent_id": 492}]
        assert _primary_agency(agencies) == "FAA"

    def test_empty_list(self):
        assert _primary_agency([]) == ""

    def test_raw_name_fallback(self):
        agencies = [{"raw_name": "DEPARTMENT OF ENERGY", "parent_id": None}]
        assert _primary_agency(agencies) == "DEPARTMENT OF ENERGY"


SAMPLE_RULE = {
    "document_number": "2026-03594",
    "title": "Appellate Jurisdiction Update",
    "abstract": "MSPB is amending regulations to remove certain appeal rights.",
    "type": "Rule",
    "subtype": None,
    "publication_date": "2026-02-23",
    "comment_url": None,
    "comments_close_on": None,
    "agencies": [
        {"name": "Merit Systems Protection Board", "parent_id": None, "raw_name": "MSPB"},
    ],
    "html_url": "https://www.federalregister.gov/documents/2026/02/23/2026-03594/appellate-jurisdiction-update",
    "body_html_url": "https://www.federalregister.gov/documents/full_text/html/2026/02/23/2026-03594.html",
    "action": "Final rule.",
    "dates": "Effective March 9, 2026.",
    "regulation_id_numbers": [],
}

SAMPLE_PROPOSED_RULE = {
    "document_number": "2026-03595",
    "title": "Employment Authorization Reform for Asylum Applicants",
    "abstract": "DHS proposes to modify regulations governing asylum EADs.",
    "type": "Proposed Rule",
    "subtype": None,
    "publication_date": "2026-02-23",
    "comment_url": "http://www.regulations.gov/commenton/USCIS-2025-0370-0001",
    "comments_close_on": "2026-04-24",
    "agencies": [
        {"name": "Homeland Security Department", "parent_id": None},
    ],
    "html_url": "https://www.federalregister.gov/documents/2026/02/23/2026-03595/employment-authorization-reform",
    "body_html_url": "",
    "action": "Notice of proposed rulemaking.",
    "dates": "Comments must be received by April 24, 2026.",
    "regulation_id_numbers": ["1615-AC97"],
}


def _mock_client(responses_by_type: dict[str, list[dict]]):
    """Build a mock client that returns different results per doc type."""
    call_count = {"n": 0}

    async def mock_get(url, **kwargs):
        params = kwargs.get("params", {})
        fr_type = params.get("conditions[type][]", "")
        page = params.get("page", 1)

        response = MagicMock()
        response.status_code = 200

        results = responses_by_type.get(fr_type, [])
        if page > 1:
            results = []
        response.json.return_value = {"results": results}
        return response

    client = AsyncMock()
    client.get = AsyncMock(side_effect=mock_get)
    return client


class TestFetchFrRulemaking:
    @pytest.mark.asyncio
    async def test_basic_rule_parse(self):
        client = _mock_client({"RULE": [SAMPLE_RULE], "PRORULE": [], "NOTICE": []})
        results = await fetch_fr_rulemaking(client, pages=1)

        assert len(results) == 1
        r = results[0]
        assert r["title"] == "Appellate Jurisdiction Update"
        assert r["doc_type"] == "Final Rule"
        assert r["chamber"] == "Regulatory"
        assert r["agency_name"] == "Merit Systems Protection Board"
        assert r["date"] == "2026-02-23"
        assert r["external_id"] == "fr-reg-2026-03594"
        assert r["comment_url"] is None
        assert r["comments_close_on"] is None
        assert "Final rule." in r["body"]

    @pytest.mark.asyncio
    async def test_proposed_rule_with_comment(self):
        client = _mock_client({"RULE": [], "PRORULE": [SAMPLE_PROPOSED_RULE], "NOTICE": []})
        results = await fetch_fr_rulemaking(client, pages=1)

        assert len(results) == 1
        r = results[0]
        assert r["doc_type"] == "Proposed Rule"
        assert r["comment_url"] == "http://www.regulations.gov/commenton/USCIS-2025-0370-0001"
        assert r["comments_close_on"] == "2026-04-24"
        assert r["agency_name"] == "Homeland Security Department"

    @pytest.mark.asyncio
    async def test_deduplication(self):
        client = _mock_client({
            "RULE": [SAMPLE_RULE, SAMPLE_RULE],
            "PRORULE": [],
            "NOTICE": [],
        })
        results = await fetch_fr_rulemaking(client, pages=1)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_multiple_types(self):
        client = _mock_client({
            "RULE": [SAMPLE_RULE],
            "PRORULE": [SAMPLE_PROPOSED_RULE],
            "NOTICE": [],
        })
        results = await fetch_fr_rulemaking(client, pages=1)
        assert len(results) == 2
        types = {r["doc_type"] for r in results}
        assert "Final Rule" in types
        assert "Proposed Rule" in types

    @pytest.mark.asyncio
    async def test_api_error_handled(self):
        response = MagicMock()
        response.status_code = 500
        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        results = await fetch_fr_rulemaking(client, pages=1)
        assert results == []

    @pytest.mark.asyncio
    async def test_timeout_handled(self):
        import httpx
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        results = await fetch_fr_rulemaking(client, pages=1)
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_abstract_uses_action_as_summary(self):
        doc = {**SAMPLE_RULE, "abstract": "", "action": "Interim final rule."}
        client = _mock_client({"RULE": [doc], "PRORULE": [], "NOTICE": []})
        results = await fetch_fr_rulemaking(client, pages=1)
        assert results[0]["summary"] == "Interim final rule."

    @pytest.mark.asyncio
    async def test_no_document_number_skipped(self):
        doc = {**SAMPLE_RULE, "document_number": ""}
        client = _mock_client({"RULE": [doc], "PRORULE": [], "NOTICE": []})
        results = await fetch_fr_rulemaking(client, pages=1)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_notice_type_label(self):
        notice = {**SAMPLE_RULE, "document_number": "2026-99999", "type": "Notice"}
        client = _mock_client({"RULE": [], "PRORULE": [], "NOTICE": [notice]})
        results = await fetch_fr_rulemaking(client, pages=1)
        assert len(results) == 1
        assert results[0]["doc_type"] == "Notice"
