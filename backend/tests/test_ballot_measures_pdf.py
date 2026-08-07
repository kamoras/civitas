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
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda page: [])
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
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda page: [])
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
    monkeypatch.setitem(pdf.STRATEGIES, "fake_strategy", lambda page: [])

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
        lambda page: [{"number": "1", "title": "T", "origin": None, "official_summary": "S",
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
