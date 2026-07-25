"""Tests for Senate eFD PTR fetching: search-row parsing, date conversion,
and the filed-date->disclosure-date fix (2026-07 platform review).

search_ptr_filings itself drives a real headless browser (see its module
docstring — efdsearch.senate.gov's search endpoint is behind Akamai
bot-management no plain HTTP client gets past, confirmed live 2026-07-25)
and isn't meaningfully unit-testable via mocks; what's tested here are the
pure building blocks it depends on (row parsing, date formatting), which
carry the actual logic that could break silently.
"""

import json

import pytest

from app.pipeline.fetch import senate_ptr


def _search_row(first, last, path="/search/view/ptr/abc123/", filed="7/1/2026", office="Senator"):
    """Column order confirmed live 2026-07-25: [first, last, office,
    link_html, filed_date] — see _parse_search_row's own docstring."""
    return [first, last, office, f'<a href="{path}">PTR</a>', filed]


class TestParseSearchRow:
    def test_happy_path(self):
        row = _search_row("Jane", "Doe", path="/search/view/ptr/abc123/", filed="7/1/2026")
        parsed = senate_ptr._parse_search_row(row)
        assert parsed == {
            "last": "Doe",
            "first": "Jane",
            "filed_date": "2026-07-01",
            "report_url": "https://efdsearch.senate.gov/search/view/ptr/abc123/",
            "is_paper": False,
        }

    def test_paper_filing_flagged(self):
        row = _search_row("Jane", "Doe", path="/search/view/paper/abc123/")
        parsed = senate_ptr._parse_search_row(row)
        assert parsed["is_paper"] is True

    def test_no_link_returns_none(self):
        row = ["Jane", "Doe", "Senator", "no link here", "7/1/2026"]
        assert senate_ptr._parse_search_row(row) is None

    def test_short_row_returns_none(self):
        assert senate_ptr._parse_search_row(["Jane", "Doe"]) is None


class TestIsoToUsDate:
    def test_converts(self):
        assert senate_ptr._iso_to_us_date("2026-07-01") == "07/01/2026"

    def test_empty_passes_through(self):
        assert senate_ptr._iso_to_us_date("") == ""


class _FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class TestFiledDateBecomesDisclosureDate:
    @pytest.mark.asyncio
    async def test_electronic_filing_uses_filed_date(self, monkeypatch):
        """The eFD HTML transactions table has no notification-date column,
        so the parser falls back to disclosure_date = transaction_date —
        which scored every electronic Senate trade as disclosed in 0 days.
        The search result's filed date is the real disclosure date."""
        html = """
        <html><body><table>
          <tr><th>Transaction Date</th><th>Owner</th><th>Asset Name</th>
              <th>Asset Type</th><th>Type</th><th>Amount</th></tr>
          <tr><td>6/1/2026</td><td>SP</td><td>Apple Inc. (AAPL)</td>
              <td>Stock</td><td>Purchase</td><td>$1,001 - $15,000</td></tr>
        </table></body></html>
        """

        async def fake_request(client, method, url, **kwargs):
            return _FakeResponse(text=html)

        monkeypatch.setattr(senate_ptr, "_request_with_retry", fake_request)
        monkeypatch.setattr(senate_ptr, "api_cache_get", lambda *a, **k: None)
        stored = {}
        monkeypatch.setattr(
            senate_ptr, "api_cache_set",
            lambda db, tier, key, value: stored.update({key: value}),
        )

        filing = {
            "last": "Doe", "first": "Jane",
            "filed_date": "2026-07-01",
            "report_url": "https://efdsearch.senate.gov/search/view/ptr/abc123/",
            "is_paper": False,
        }
        rows = await senate_ptr.fetch_and_parse_ptr(None, None, filing)
        assert len(rows) == 1
        assert rows[0].transaction_date == "2026-06-01"
        assert rows[0].disclosure_date == "2026-07-01"
        # The cached copy must carry the corrected date too — it feeds
        # the 30-day replay path.
        cached_rows = json.loads(json.dumps(stored["ptr-parsed-abc123"]))
        assert cached_rows[0]["disclosure_date"] == "2026-07-01"
