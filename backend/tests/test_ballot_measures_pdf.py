"""Tests for the generic ballot-measure PDF pipeline stage: fetch, cache,
and normalize any registered state's PDF into the shape
election_pipeline._upsert_measure expects. Per-state page-parsing logic
(e.g. California's) is tested in its own module
(test_ballot_measures_ca.py) — these tests exercise the shared
fetch/cache/dispatch code and use a fake strategy rather than a real PDF.
"""

from types import SimpleNamespace

import pytest

from app.pipeline.fetch import ballot_measures_pdf as pdf


def _fake_source(strategy="fake_strategy", **overrides):
    source = {
        "url_pattern": "https://example.com/{year}/ballot.pdf",
        "source_name": "Example State Elections Office",
        "strategy": strategy,
    }
    source.update(overrides)
    return source


def test_is_configured_false_without_a_registered_source(monkeypatch):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: None)
    assert pdf.is_configured("ZZ") is False


def test_is_configured_false_when_strategy_key_is_unregistered(monkeypatch):
    """A source entry pointing at a strategy that was never registered in
    STRATEGIES is a config bug (typo, or the code got reverted) — it must
    not silently fall through to guessing at the page format."""
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("does_not_exist"))
    assert pdf.is_configured("ZZ") is False


def test_is_configured_true_with_a_real_registered_strategy(monkeypatch):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_strategy"))
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda pages: [])
    assert pdf.is_configured("ZZ") is True


def test_to_measure_produces_upsert_ready_shape():
    parsed = {
        "number": "2", "title": "T", "origin": "the Legislature",
        "official_summary": "S", "fiscal_impact": "F",
        "yes_means": "Y", "no_means": "N",
    }
    measure = pdf._to_measure("CA", parsed, "2026-11-03", "https://example.com/vig.pdf")
    assert measure["id"] == "CA-2026-11-03-2"
    assert measure["state"] == "CA"
    assert measure["election_date"] == "2026-11-03"
    assert measure["official_title"] == "T"
    assert measure["source_url"] == "https://example.com/vig.pdf"


def test_to_measure_falls_back_to_proposition_number_when_title_missing():
    parsed = {
        "number": "7", "title": "", "origin": None, "official_summary": "S",
        "fiscal_impact": None, "yes_means": None, "no_means": None,
    }
    measure = pdf._to_measure("ZZ", parsed, "2026-11-03", "https://example.com/vig.pdf")
    assert measure["title"] == "Proposition 7"


@pytest.mark.asyncio
async def test_fetch_returns_none_for_an_unregistered_state(db_session):
    result = await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_cached_result_without_a_fetch(monkeypatch, db_session):
    from app.pipeline.cache import api_cache_set

    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source())
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda pages: [])
    api_cache_set(db_session, "ballot_measure_pdf", "ZZ-2026", {"measures": [{"id": "ZZ-x"}]})

    async def fail_get(*a, **kw):
        raise AssertionError("should not fetch — cache hit")

    client = SimpleNamespace(get=fail_get)
    result = await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03")
    assert result == [{"id": "ZZ-x"}]


@pytest.mark.asyncio
async def test_fetch_returns_none_on_http_failure(monkeypatch, db_session):
    import httpx

    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source())
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda pages: [])

    class FakeResponse:
        status_code = 403

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403", request=None, response=self)

    async def fake_get(*a, **kw):
        return FakeResponse()

    client = SimpleNamespace(get=fake_get)
    result = await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_dispatches_to_the_registered_strategy_and_caches(monkeypatch, db_session):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source())
    fake_page = object()

    class FakePdf:
        pages = [fake_page]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pdf.pdfplumber, "open", lambda buf: FakePdf())
    monkeypatch.setitem(
        pdf.STRATEGIES, "fake_strategy",
        lambda pages: [{"number": "1", "title": "T", "origin": None, "official_summary": "S",
                        "fiscal_impact": None, "yes_means": None, "no_means": None}],
    )

    class FakeResponse:
        content = b"%PDF-fake"

        def raise_for_status(self):
            pass

    async def fake_get(*a, **kw):
        return FakeResponse()

    client = SimpleNamespace(get=fake_get)
    result = await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03")
    assert result == [pdf._to_measure(
        "ZZ", {"number": "1", "title": "T", "origin": None, "official_summary": "S",
               "fiscal_impact": None, "yes_means": None, "no_means": None},
        "2026-11-03", "https://example.com/2026/ballot.pdf",
    )]

    # Second call must hit the cache, not fetch again.
    async def fail_get(*a, **kw):
        raise AssertionError("should not fetch — cache hit")

    client2 = SimpleNamespace(get=fail_get)
    cached_result = await pdf.fetch_state_measures_pdf(client2, db_session, "ZZ", 2026, "2026-11-03")
    assert cached_result == result


# ── landing-page discovery (evergreen states whose filename changes each
# cycle but whose landing page, and its link text, doesn't) ────────────


_CO_LANDING_HTML = """
<a href="https://content.leg.colorado.gov/sites/default/files/2024-blue-book-english-accessible.pdf" class="link-primary">Blue Book 2024</a>
<a href="https://content.leg.colorado.gov/sites/default/files/images/blue_book_2022_english_for_web.pdf" class="link-primary">Blue Book 2022</a>
<a href="https://content.leg.colorado.gov/sites/default/files/2024-primary-summary.pdf" class="link-primary">2024 Primary Summary</a>
"""


@pytest.mark.asyncio
async def test_discover_pdf_url_matches_year_and_keyword():
    async def fake_get(url, timeout=None):
        return SimpleNamespace(text=_CO_LANDING_HTML, raise_for_status=lambda: None)

    client = SimpleNamespace(get=fake_get)
    url = await pdf.discover_pdf_url(
        client, "https://example.com/blue-book", 2024, keyword="blue",
    )
    assert url == "https://content.leg.colorado.gov/sites/default/files/2024-blue-book-english-accessible.pdf"


@pytest.mark.asyncio
async def test_discover_pdf_url_excludes_primary_by_default():
    """A link matching the year but not the keyword (or matching
    "primary") must not win — real failure this guards against: without
    the keyword filter, 2024's primary-summary link would tie on year
    alone."""
    async def fake_get(url, timeout=None):
        return SimpleNamespace(
            text='<a href="https://x.com/2024-primary-summary.pdf">2024 Primary Summary</a>',
            raise_for_status=lambda: None,
        )

    client = SimpleNamespace(get=fake_get)
    url = await pdf.discover_pdf_url(client, "https://example.com", 2024)
    assert url is None


@pytest.mark.asyncio
async def test_discover_pdf_url_follows_a_link_to_find_the_pdf():
    """A start page without a direct PDF match but with a link whose text
    matches generic election vocabulary ("Ballot Information") must be
    followed — this is what lets a human hand over a coarse starting
    page (a state's homepage) rather than the exact listing page, since
    no state's site layout is guaranteed stable either."""
    pages = {
        "https://example.com/": (
            '<a href="https://example.com/elections/ballot-guide">Ballot Information</a>'
        ),
        "https://example.com/elections/ballot-guide": _CO_LANDING_HTML,
    }

    async def fake_get(url, timeout=None):
        return SimpleNamespace(text=pages[url], raise_for_status=lambda: None)

    client = SimpleNamespace(get=fake_get)
    url = await pdf.discover_pdf_url(client, "https://example.com/", 2024, keyword="blue")
    assert url == "https://content.leg.colorado.gov/sites/default/files/2024-blue-book-english-accessible.pdf"


@pytest.mark.asyncio
async def test_discover_pdf_url_never_follows_a_link_off_the_starting_domain():
    """A link to an outside site (a news article, Ballotpedia, a
    different state) must never be followed — real risk this guards
    against: picking up the wrong state's or a third party's document."""
    pages = {
        "https://example.com/": (
            '<a href="https://other-site.com/ballot-guide-2024.pdf">Ballot Guide 2024</a>'
        ),
    }

    async def fake_get(url, timeout=None):
        if url not in pages:
            raise AssertionError(f"must never fetch off-domain url: {url}")
        return SimpleNamespace(text=pages[url], raise_for_status=lambda: None)

    client = SimpleNamespace(get=fake_get)
    # The PDF link itself is off-domain but still matches year/keyword —
    # confirm it's still returned (a matching link found ON the starting
    # page is fine even if it points elsewhere; what must never happen is
    # CRAWLING onto another domain to look for more links).
    url = await pdf.discover_pdf_url(client, "https://example.com/", 2024, keyword="ballot")
    assert url == "https://other-site.com/ballot-guide-2024.pdf"


@pytest.mark.asyncio
async def test_discover_pdf_url_does_not_follow_unrelated_links():
    """A link with no election-vocabulary signal in its text/href must
    not be followed — otherwise a bounded crawl degrades into crawling
    an entire government website looking for anything."""
    pages = {
        "https://example.com/": (
            '<a href="https://example.com/about-the-governor">About the Governor</a>'
        ),
    }
    fetched = []

    async def fake_get(url, timeout=None):
        fetched.append(url)
        return SimpleNamespace(text=pages.get(url, ""), raise_for_status=lambda: None)

    client = SimpleNamespace(get=fake_get)
    url = await pdf.discover_pdf_url(client, "https://example.com/", 2024)
    assert url is None
    assert fetched == ["https://example.com/"]


@pytest.mark.asyncio
async def test_discover_pdf_url_respects_max_depth():
    """A chain of election-flavored links longer than max_depth must not
    be fully traversed — the bound exists so a real crawl can't run
    away indefinitely."""
    pages = {
        "https://example.com/a": '<a href="https://example.com/b">Ballot Measures</a>',
        "https://example.com/b": '<a href="https://example.com/c">Ballot Guide</a>',
        "https://example.com/c": '<a href="https://example.com/d.pdf">Ballot Guide 2024</a>',
    }

    async def fake_get(url, timeout=None):
        return SimpleNamespace(text=pages.get(url, ""), raise_for_status=lambda: None)

    client = SimpleNamespace(get=fake_get)
    url = await pdf.discover_pdf_url(client, "https://example.com/a", 2024, keyword="ballot", max_depth=1)
    assert url is None


@pytest.mark.asyncio
async def test_discover_pdf_url_returns_none_on_fetch_failure():
    async def fail_get(*a, **kw):
        raise Exception("network error")

    client = SimpleNamespace(get=fail_get)
    url = await pdf.discover_pdf_url(client, "https://example.com", 2024)
    assert url is None


@pytest.mark.asyncio
async def test_fetch_uses_discovery_when_source_has_a_landing_page(monkeypatch, db_session):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: {
        "landing_page_url": "https://example.com/blue-book", "keyword": "blue",
        "source_name": "Example", "strategy": "fake_strategy",
    })
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda pages: [])

    async def fake_discover(client, landing_page_url, year, keyword=None):
        assert landing_page_url == "https://example.com/blue-book"
        assert keyword == "blue"
        return "https://example.com/resolved-2026.pdf"

    monkeypatch.setattr(pdf, "discover_pdf_url", fake_discover)

    class FakePdf:
        pages = []
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(pdf.pdfplumber, "open", lambda buf: FakePdf())

    fetched_urls = []
    async def fake_get(url, timeout=None):
        fetched_urls.append(url)
        return SimpleNamespace(content=b"%PDF-fake", raise_for_status=lambda: None)

    client = SimpleNamespace(get=fake_get)
    result = await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03")
    assert result == []
    assert fetched_urls == ["https://example.com/resolved-2026.pdf"]


@pytest.mark.asyncio
async def test_fetch_returns_none_when_discovery_finds_nothing(monkeypatch, db_session):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: {
        "landing_page_url": "https://example.com/blue-book",
        "source_name": "Example", "strategy": "fake_strategy",
    })
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda pages: [])

    async def fake_discover(*a, **kw):
        return None

    monkeypatch.setattr(pdf, "discover_pdf_url", fake_discover)

    async def fail_get(*a, **kw):
        raise AssertionError("should not fetch a PDF — nothing was discovered")

    client = SimpleNamespace(get=fail_get)
    result = await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03")
    assert result is None
