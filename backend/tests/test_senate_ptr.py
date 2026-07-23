"""Tests for Senate eFD PTR fetching: search pagination and the
filed-date→disclosure-date fix (2026-07 platform review)."""

import json

import pytest

from app.pipeline.fetch import senate_ptr


class _FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _search_row(last, first, path="/search/view/ptr/abc123/", filed="7/1/2026"):
    return [f'<a href="{path}">PTR</a>', last, first, "Senator", filed]


class TestSearchPagination:
    @pytest.mark.asyncio
    async def test_full_page_triggers_next_page(self, monkeypatch):
        """A window with more than 100 filings must paginate — a single
        start=0 page silently truncated the result set, and the caller's
        incremental anchor then skipped the dropped filings forever."""
        pages = [
            {"data": [_search_row("Alpha", "A", path=f"/search/view/ptr/a{i}/") for i in range(100)]},
            {"data": [_search_row("Beta", "B", path="/search/view/ptr/b1/")]},
        ]
        calls = []

        async def fake_request(client, method, url, **kwargs):
            calls.append(kwargs.get("data", {}).get("start"))
            return _FakeResponse(pages[len(calls) - 1])

        monkeypatch.setattr(senate_ptr, "_request_with_retry", fake_request)
        filings = await senate_ptr.search_ptr_filings(None, None, "2026-01-01", "tok")
        assert len(filings) == 101
        assert calls == ["0", "100"]

    @pytest.mark.asyncio
    async def test_short_page_stops(self, monkeypatch):
        async def fake_request(client, method, url, **kwargs):
            return _FakeResponse({"data": [_search_row("Solo", "S")]})

        monkeypatch.setattr(senate_ptr, "_request_with_retry", fake_request)
        filings = await senate_ptr.search_ptr_filings(None, None, "2026-01-01", "tok")
        assert len(filings) == 1
        assert filings[0]["filed_date"] == "2026-07-01"


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
