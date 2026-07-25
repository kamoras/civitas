"""Tests for Senate eFD PTR fetching: search-row parsing, date conversion,
the scraping control flow, and the filed-date->disclosure-date fix
(2026-07 platform review).

search_ptr_filings' own browser-launch/close plumbing isn't meaningfully
unit-testable (that's covered by the live verification described in
senate_ptr.py's module docstring instead), but _scrape_via_page — the
actual flow logic (terms gate, form fill, pagination, termination
conditions) — takes a `page` object as a plain argument, so it's exercised
here against a small fake Playwright page rather than a real browser.
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


class _FakeSearchResponse:
    """A page.on("response", ...) event object — the real one is an
    httpx-response-shaped object with an async .json(); .url is a plain
    attribute, matched against SEARCH_DATA_URL by the listener."""

    def __init__(self, payload):
        self.url = senate_ptr.SEARCH_DATA_URL
        self._payload = payload

    async def json(self):
        return self._payload


class _FakeLocator:
    """Stands in for a Playwright Locator. `count` is the element-present
    check most branches in _scrape_via_page gate on; `on_click` lets a
    test simulate the real side effect of a click (firing a response
    event) without needing an actual page."""

    def __init__(self, count=1, on_click=None, attr=None):
        self._count = count
        self._on_click = on_click
        self._attr = attr
        self.filled = None

    async def count(self):
        return self._count

    async def click(self, timeout=None):
        if self._on_click:
            self._on_click()

    async def select_option(self, value, timeout=None):
        if self._on_click:
            self._on_click()

    async def fill(self, value):
        self.filled = value

    async def get_attribute(self, name):
        return self._attr


class _FakePage:
    """Minimal stand-in for a Playwright Page, just enough surface for
    _scrape_via_page: goto/locator/get_by_role/get_by_text/on. Locators
    are looked up by a (kind, key) tuple the test configures up front;
    anything not configured defaults to "present" (count=1) so a test
    only needs to override what it cares about.
    """

    def __init__(self, locators: dict | None = None):
        self._locators = locators or {}
        self._response_cb = None

    async def goto(self, url, wait_until=None):
        pass

    def locator(self, selector):
        return self._locators.get(("locator", selector), _FakeLocator())

    def get_by_role(self, role, name=None):
        return self._locators.get(("role", role, name), _FakeLocator())

    def get_by_text(self, text, exact=None):
        return self._locators.get(("text", text), _FakeLocator())

    def on(self, event, callback):
        if event == "response":
            self._response_cb = callback

    def fire_response(self, response) -> None:
        self._response_cb(response)


class TestScrapeViaPage:
    """_scrape_via_page's control flow: terms gate, form fill, the
    pagination loop's termination conditions. The one thing a real
    browser session provides that these fakes can't (passing Akamai's
    bot-management gate) is covered by the live verification in the
    module docstring instead."""

    @pytest.mark.asyncio
    async def test_single_page_no_terms_gate_no_length_dropdown(self):
        payload = {"recordsTotal": 1, "data": [_search_row("Jane", "Doe")]}
        page = _FakePage({
            ("locator", "#agree_statement"): _FakeLocator(count=0),
            ("role", "combobox", "Show entries"): _FakeLocator(count=0),
        })
        search_btn = _FakeLocator(on_click=lambda: page.fire_response(_FakeSearchResponse(payload)))
        page._locators[("role", "button", "Search Reports")] = search_btn

        filings = await senate_ptr._scrape_via_page(page, "2026-01-01")

        assert len(filings) == 1
        assert filings[0]["first"] == "Jane"

    @pytest.mark.asyncio
    async def test_terms_gate_accepted_when_present(self):
        agree_clicks = []
        payload = {"recordsTotal": 0, "data": []}
        page = _FakePage({
            ("locator", "#agree_statement"): _FakeLocator(count=1, on_click=lambda: agree_clicks.append(1)),
            ("role", "combobox", "Show entries"): _FakeLocator(count=0),
        })
        page._locators[("role", "button", "Search Reports")] = _FakeLocator(
            on_click=lambda: page.fire_response(_FakeSearchResponse(payload)),
        )

        await senate_ptr._scrape_via_page(page, "")

        assert agree_clicks == [1]

    @pytest.mark.asyncio
    async def test_no_search_response_returns_empty(self, monkeypatch):
        # "Search Reports" click never fires a response — page structure
        # changed, or the gate blocked it. Must not hang or raise.
        page = _FakePage({
            ("locator", "#agree_statement"): _FakeLocator(count=0),
        })

        async def _fast_wait(predicate, timeout_s=10.0, poll_s=0.1):
            return predicate()

        monkeypatch.setattr(senate_ptr, "_wait_until", _fast_wait)

        filings = await senate_ptr._scrape_via_page(page, "")

        assert filings == []

    @pytest.mark.asyncio
    async def test_pagination_stops_when_next_disabled(self):
        page1 = {"recordsTotal": 3, "data": [_search_row("A", "One"), _search_row("B", "Two")]}
        page2 = {"recordsTotal": 3, "data": [_search_row("C", "Three")]}
        responses = iter([_FakeSearchResponse(page1), _FakeSearchResponse(page2)])

        page = _FakePage({
            ("locator", "#agree_statement"): _FakeLocator(count=0),
            ("role", "combobox", "Show entries"): _FakeLocator(count=0),
        })
        page._locators[("role", "button", "Search Reports")] = _FakeLocator(
            on_click=lambda: page.fire_response(next(responses)),
        )
        page._locators[("text", "Next")] = _FakeLocator(
            on_click=lambda: page.fire_response(next(responses)),
            attr="paginate_button next disabled",
        )

        filings = await senate_ptr._scrape_via_page(page, "")

        # First page's 2 rows only — "Next" was already disabled, so the
        # loop must never have clicked it (recordsTotal=3 alone would
        # otherwise keep it looping forever without this check).
        assert len(filings) == 2

    @pytest.mark.asyncio
    async def test_pagination_continues_across_pages(self):
        page1 = {"recordsTotal": 3, "data": [_search_row("A", "One"), _search_row("B", "Two")]}
        page2 = {"recordsTotal": 3, "data": [_search_row("C", "Three")]}
        responses = iter([_FakeSearchResponse(page1), _FakeSearchResponse(page2)])

        page = _FakePage({
            ("locator", "#agree_statement"): _FakeLocator(count=0),
            ("role", "combobox", "Show entries"): _FakeLocator(count=0),
        })
        page._locators[("role", "button", "Search Reports")] = _FakeLocator(
            on_click=lambda: page.fire_response(next(responses)),
        )
        page._locators[("text", "Next")] = _FakeLocator(
            on_click=lambda: page.fire_response(next(responses)),
            attr="paginate_button next",  # not disabled
        )

        filings = await senate_ptr._scrape_via_page(page, "")

        assert len(filings) == 3
        assert [f["first"] for f in filings] == ["A", "B", "C"]


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
