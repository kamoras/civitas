"""Tests for the generic ballot-measure PDF pipeline stage: fetch, cache,
and normalize any registered state's PDF into the shape
election_pipeline._upsert_measure expects. Per-state page-parsing logic
is tested in each state's own module (test_ballot_measures_<st>.py) —
these tests exercise the shared
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
    assert measure["source_url"] == "https://example.com/vig.pdf"


def test_official_title_is_only_ever_one_the_strategy_supplies():
    """The regression: official_title defaulted to `title`, so a label
    this codebase composed ("Proposition 3", "Question 1: Citizen
    Initiative") rendered as "OFFICIAL BALLOT TITLE — Drafted by <office>"."""
    label_only = {
        "number": "3", "title": "Proposition 3", "origin": None, "official_summary": "S",
        "fiscal_impact": None, "yes_means": None, "no_means": None, "title_authority": "A Legislature",
    }
    measure = pdf._to_measure("TX", label_only, "2026-11-03", "u")
    assert measure["official_title"] is None
    assert measure["title"] == "Proposition 3"
    assert measure["title_authority"] == "A Legislature"
    real = pdf._to_measure("FL", {**label_only, "official_title": "BUDGET STABILIZATION FUND"}, "2026-11-03", "u")
    assert real["official_title"] == "BUDGET STABILIZATION FUND"


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
    api_cache_set(db_session, pdf.CACHE_TIER, "ZZ-2026", [{"id": "ZZ-x"}])

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
        return "https://example.com/resolved-2026.pdf", True

    monkeypatch.setattr(pdf, "discover_pdf_url_checked", fake_discover)

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


# ── multi-document strategies (a state whose measures are several
# separate documents, not one combined guide — see ballot_measures_va.py
# and MULTI_DOCUMENT_STRATEGIES) ────────────────────────────────────────


def test_is_configured_true_via_multi_document_strategies(monkeypatch):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_multi"))
    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", lambda client, year: None)
    assert pdf.is_configured("ZZ") is True


@pytest.mark.asyncio
async def test_fetch_dispatches_to_a_multi_document_strategy_and_caches(monkeypatch, db_session):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_multi"))

    parsed = {"number": "1", "title": "T", "origin": None, "official_summary": "S",
              "fiscal_impact": None, "yes_means": None, "no_means": None}

    async def fake_multi(client, year):
        return [(parsed, "https://example.com/q1.pdf")]

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", fake_multi)

    result = await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03")
    assert result == [pdf._to_measure("ZZ", parsed, "2026-11-03", "https://example.com/q1.pdf")]

    # Second call must hit the cache, not call the strategy again.
    async def fail_multi(client, year):
        raise AssertionError("should not fetch — cache hit")

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", fail_multi)
    cached_result = await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03")
    assert cached_result == result


@pytest.mark.asyncio
async def test_fetch_returns_none_when_multi_document_strategy_returns_none(monkeypatch, db_session):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_multi"))

    async def fake_multi(client, year):
        return None

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", fake_multi)

    result = await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_multi_document_strategy_raises(monkeypatch, db_session):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_multi"))

    async def fake_multi(client, year):
        raise RuntimeError("network error")

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", fake_multi)

    result = await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_discovery_finds_nothing(monkeypatch, db_session):
    monkeypatch.setattr(pdf, "source_for_state", lambda state: {
        "landing_page_url": "https://example.com/blue-book",
        "source_name": "Example", "strategy": "fake_strategy",
    })
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda pages: [])

    async def fake_discover(*a, **kw):
        return None, True

    monkeypatch.setattr(pdf, "discover_pdf_url_checked", fake_discover)

    async def fail_get(*a, **kw):
        raise AssertionError("should not fetch a PDF — nothing was discovered")

    client = SimpleNamespace(get=fail_get)
    result = await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03")
    assert result is None


def test_to_measure_carries_the_drafters_and_an_explicit_official_title():
    # Drafters are what the card's "Drafted by ..." line renders; a
    # strategy whose `title` is a label (Oklahoma's register subject line)
    # passes official_title=None, or nothing at all.
    parsed = {
        "number": "845", "title": "Judicial Nominating Commission", "official_title": None,
        "origin": None, "official_summary": None, "fiscal_impact": "F",
        "yes_means": None, "no_means": None,
        "title_authority": "A Drafter", "fiscal_authority": "A Fiscal Office",
    }
    measure = pdf._to_measure("OK", parsed, "2026-11-03", "https://example.com/845.pdf")
    assert measure["official_title"] is None
    assert measure["title"] == "Judicial Nominating Commission"
    assert measure["title_authority"] == "A Drafter"
    assert measure["fiscal_authority"] == "A Fiscal Office"


def test_every_registered_state_resolves_to_a_strategy():
    # A typo'd strategy key silently leaves a state unread (not yet
    # covered); every entry in the bundled registry must resolve.
    from app.pipeline.fetch import ballot_measure_pdf_sources as sources

    sources.invalidate_cache()
    states = sources.configured_states()
    assert {
        "AL", "AR", "FL", "KY", "MD", "NC", "SC", "TN", "TX", "WV",
        "AK", "HI", "ID", "MT", "NM", "WA", "WY",
        "IL", "IN", "KS", "MI", "MN", "ND", "NE", "OK", "SD",
    } <= states
    for state in states:
        assert pdf.is_configured(state), state
        assert sources.source_for_state(state)["source_name"], state


# ── one answer per state: never two measures with one id ─────────────


def _one(number, **kw):
    return {"number": number, "title": "T", "origin": None, "official_summary": "S",
            "fiscal_impact": None, "yes_means": None, "no_means": None, **kw}


@pytest.mark.asyncio
async def test_two_measures_sharing_an_id_refuse_the_states_answer(monkeypatch, db_session):
    """Two measures with one id would be one row — the second upsert
    overwrites the first and the page shows one (Kentucky's unnumbered
    amendments were all keyed on their shared heading). The state's whole
    answer is refused, for every state, whatever the reader."""
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_multi"))

    async def fake_multi(client, year):
        return [(_one("", id_key="HEADING"), "u1"), (_one("", id_key="HEADING"), "u2")]

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", fake_multi)
    assert await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03") is None

    async def distinct(client, year):
        return [(_one("", id_key="A"), "u1"), (_one("", id_key="B"), "u2")]

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", distinct)
    measures = await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03")
    assert [m["id"] for m in measures] == ["ZZ-2026-11-03-A", "ZZ-2026-11-03-B"]


# ── cache: a confirmed none is re-checked, not pinned ─────────────────


@pytest.mark.asyncio
async def test_a_confirmed_none_is_cached_only_briefly(monkeypatch, db_session):
    """The regression: [] was cached wrapped as {"measures": []} — a
    non-empty dict — so api_cache_set's short EMPTY_RESPONSE_TTL_HOURS
    never applied and a confirmed none was pinned for 72h. The next
    nightly run must ask again."""
    from datetime import timedelta

    from app.models import ApiCache
    from app.pipeline.cache import EMPTY_RESPONSE_TTL_HOURS
    from app.time_utils import utcnow

    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_multi"))
    calls = []

    async def none_this_year(client, year):
        calls.append(year)
        return []

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", none_this_year)
    assert await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03") == []
    entry = db_session.query(ApiCache).filter(ApiCache.tier == pdf.CACHE_TIER).one()
    # Backdated so it expires EMPTY_RESPONSE_TTL_HOURS after it was written.
    age = utcnow() - entry.cached_at
    assert age > timedelta(hours=pdf.CACHE_TTL_HOURS - EMPTY_RESPONSE_TTL_HOURS - 1)

    entry.cached_at -= timedelta(hours=EMPTY_RESPONSE_TTL_HOURS + 1)
    db_session.commit()
    assert await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03") == []
    assert calls == [2026, 2026]


@pytest.mark.asyncio
async def test_a_pre_fix_cache_entry_is_never_served(monkeypatch, db_session):
    """Entries written before this change sat under the old tier as
    {"measures": [...]}, unchecked and with labels as official titles;
    serving them for up to 72h after a deploy would undo the fixes."""
    from app.pipeline.cache import api_cache_set

    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_multi"))
    api_cache_set(db_session, "ballot_measure_pdf", "ZZ-2026", {"measures": [{"id": "ZZ-stale"}]})
    calls = []

    async def fresh(client, year):
        calls.append(year)
        return [(_one("1"), "u")]

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", fresh)
    [m] = await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03")
    assert m["id"] == "ZZ-2026-11-03-1" and calls == [2026]
    assert pdf.CACHE_TIER != "ballot_measure_pdf"


@pytest.mark.asyncio
async def test_two_measures_sharing_a_number_refuse_the_states_answer(monkeypatch, db_session):
    """Different ids, same non-empty number: BallotMeasure is unique on
    (state, election_date, number), so the second insert would fail and
    the state would publish one of the two."""
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source("fake_multi"))

    async def same_number(client, year):
        return [(_one("1", id_key="A"), "u1"), (_one("1", id_key="B"), "u2")]

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", same_number)
    assert await pdf.fetch_state_measures_pdf(None, db_session, "ZZ", 2026, "2026-11-03") is None


@pytest.mark.asyncio
async def test_a_crawl_that_ran_out_of_budget_is_not_complete():
    """The regression: the crawl stopped at max_pages with links still
    queued and reported (None, True) — "read everything, nothing there" —
    so a document linked on a page it never opened read as not published."""
    async def get(url, timeout=None):
        n = int(url.rsplit("/", 1)[-1] or 0)
        body = f'<a href="https://example.com/ballot/{n + 1}">ballot {n + 1}</a>'
        if n == 10:
            body = '<a href="https://example.com/2026-ballot-guide.pdf">2026 ballot guide</a>'
        return SimpleNamespace(text=body, raise_for_status=lambda: None)

    client = SimpleNamespace(get=get)
    url, complete = await pdf.discover_pdf_url_checked(
        client, "https://example.com/ballot/0", 2026, max_pages=8, max_depth=20,
    )
    assert (url, complete) == (None, False)
    url, complete = await pdf.discover_pdf_url_checked(
        client, "https://example.com/ballot/0", 2026, max_pages=20, max_depth=20,
    )
    assert url == "https://example.com/2026-ballot-guide.pdf" and complete


# ── a document not posted yet vs a failure (single-document sources) ─


def _status_client(status):
    import httpx

    class Response:
        status_code = status
        content = b""

        def raise_for_status(self):
            raise httpx.HTTPStatusError(str(status), request=None, response=self)

    async def get(*a, **kw):
        return Response()

    return SimpleNamespace(get=get)


@pytest.mark.asyncio
async def test_a_404_is_not_yet_published_only_where_the_registry_says_so(monkeypatch, db_session):
    """Alaska posts its sample ballots ~50 days out: until then the
    address 404s, and that used to be an ingest failure paging nightly."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda pages: [])
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source(absent_until_published=True))
    with pytest.raises(NotYetPublished):
        await pdf.fetch_state_measures_pdf(_status_client(404), db_session, "ZZ", 2026, "2026-11-03")
    # Any other status is still a failure...
    assert await pdf.fetch_state_measures_pdf(_status_client(503), db_session, "ZZ", 2026, "2026-11-03") is None
    # ... and so is a 404 for a source that isn't known to post late.
    monkeypatch.setattr(pdf, "source_for_state", lambda state: _fake_source())
    assert await pdf.fetch_state_measures_pdf(_status_client(404), db_session, "ZZ", 2026, "2026-11-03") is None


@pytest.mark.asyncio
async def test_an_undiscovered_document_is_not_yet_published_only_after_a_complete_crawl(monkeypatch, db_session):
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda pages: [])
    monkeypatch.setattr(pdf, "source_for_state", lambda state: {
        "landing_page_url": "https://example.com/announcements", "source_name": "Example",
        "strategy": "fake_strategy", "absent_until_published": True,
    })

    async def page_without_the_link(url, timeout=None):
        return SimpleNamespace(text="<a href='/other.pdf'>Something else</a>", raise_for_status=lambda: None)

    with pytest.raises(NotYetPublished):
        await pdf.fetch_state_measures_pdf(
            SimpleNamespace(get=page_without_the_link), db_session, "ZZ", 2026, "2026-11-03",
        )

    async def unreachable(url, timeout=None):
        raise RuntimeError("connection reset")

    # A landing page that couldn't be read is an outage, not an absence.
    assert await pdf.fetch_state_measures_pdf(
        SimpleNamespace(get=unreachable), db_session, "ZZ", 2026, "2026-11-03",
    ) is None


def test_the_late_posting_sources_are_flagged():
    from app.pipeline.fetch import ballot_measure_pdf_sources as sources

    sources.invalidate_cache()
    flagged = {st for st in sources.configured_states() if (sources.source_for_state(st) or {}).get("absent_until_published")}
    assert flagged == {"AK", "CO", "MA", "VT", "WY"}
    # Of those, only the ones that publish for every general hit the
    # expected-by cutoff.
    can_be_none = {st for st in flagged if sources.source_for_state(st).get("absence_can_mean_none")}
    assert can_be_none == {"VT", "WY"}
