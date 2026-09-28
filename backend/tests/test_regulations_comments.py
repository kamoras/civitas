"""Public comments on a rulemaking, from Regulations.gov."""

import asyncio

import httpx
import pytest

from app.pipeline.fetch import regulations_gov as rg

URL = "https://www.regulations.gov/commenton/EPA-HQ-OAR-2021-0208-0001"


@pytest.fixture
def api(monkeypatch):
    """A fake Regulations.gov. The comments listing answers only when
    filtered on the document's objectId, as the real API does."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/v4/documents/EPA-HQ-OAR-2021-0208-0001":
            return httpx.Response(200, json={"data": {"attributes": {"objectId": "0900006483a6cba3"}}})
        if request.url.path == "/v4/comments":
            if request.url.params.get("filter[commentOnId]") != "0900006483a6cba3":
                return httpx.Response(200, json={"data": [], "meta": {"totalElements": 0}})
            return httpx.Response(200, json={
                "data": [{"id": "EPA-1", "attributes": {"title": "Comment", "comment": "Please reconsider.",
                                                         "firstName": "Pat"}}],
                "meta": {"totalElements": 1},
            })
        return httpx.Response(404)

    monkeypatch.setattr(rg.settings, "DATA_GOV_API_KEY", "k", raising=False)
    monkeypatch.setattr(rg, "make_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return calls


def test_comments_are_listed_by_the_documents_object_id(api):
    # Filtering on the documentId from the URL matched nothing: every
    # document read as having no public comments.
    result = asyncio.run(rg.fetch_comments(URL))
    assert result["totalElements"] == 1
    assert result["comments"][0]["body"] == "Please reconsider."


def test_object_id_and_pages_are_cached(api, db_session):
    charged = []
    asyncio.run(rg.fetch_comments(URL, db=db_session, spend=_charging(charged)))
    asyncio.run(rg.fetch_comments(URL, db=db_session, spend=_charging(charged)))       # page cached
    asyncio.run(rg.fetch_comments(URL, page_number=2, db=db_session, spend=_charging(charged)))  # objectId cached
    assert charged == [2, 1]
    assert sum("/documents/" in c for c in api) == 1


def test_a_refused_budget_sends_nothing(api, db_session):
    async def refuse(n):
        raise RuntimeError("spent")
    with pytest.raises(RuntimeError):
        asyncio.run(rg.fetch_comments(URL, db=db_session, spend=refuse))
    assert api == []


def test_unknown_document(api):
    result = asyncio.run(rg.fetch_comments("https://www.regulations.gov/document/NOPE-1"))
    assert result["comments"] == [] and "not found" in result["error"]


def test_an_unknown_document_is_remembered(api, db_session):
    # A 404 is the answer about the document: asking again would only
    # spend the shared budget.
    charged = []
    first = asyncio.run(rg.fetch_comments("https://www.regulations.gov/document/NOPE-1", db=db_session,
                                          spend=_charging(charged)))
    again = asyncio.run(rg.fetch_comments("https://www.regulations.gov/document/NOPE-1", db=db_session,
                                          spend=_charging(charged)))
    assert first["retryable"] is False and again["error"] == first["error"]
    assert charged == [2]
    assert sum("/documents/" in c for c in api) == 1


@pytest.mark.parametrize("status,error", [(429, "Rate limit reached"), (503, "API error: 503")])
def test_a_failed_lookup_is_not_taken_for_an_unknown_document(monkeypatch, db_session, status, error):
    def handler(request):
        return httpx.Response(status)

    monkeypatch.setattr(rg.settings, "DATA_GOV_API_KEY", "k", raising=False)
    monkeypatch.setattr(rg, "make_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = asyncio.run(rg.fetch_comments(URL, db=db_session))
    assert result["error"] == error and result["retryable"] is True
    # Not remembered as missing: the next call asks again.
    monkeypatch.setattr(rg, "make_async_client", lambda **kw: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"attributes": {"objectId": "x"}}})
                                      if "/documents/" in r.url.path else httpx.Response(200, json={"data": [], "meta": {}}))))
    assert "error" not in asyncio.run(rg.fetch_comments(URL, db=db_session))


def test_an_unknown_document_is_asked_about_again_after_a_few_hours(api, db_session, monkeypatch):
    # Published before Regulations.gov indexed it: found once it has, not a
    # year later.
    from datetime import timedelta

    from app.models import ApiCache

    asyncio.run(rg.fetch_comments("https://www.regulations.gov/document/NOPE-1", db=db_session))
    row = db_session.query(ApiCache).filter(ApiCache.cache_key == "objectid-missing-NOPE-1").one()
    row.cached_at = row.cached_at - timedelta(hours=rg._NOT_FOUND_CACHE_HOURS + 1)
    db_session.commit()
    before = sum("/documents/" in c for c in api)
    asyncio.run(rg.fetch_comments("https://www.regulations.gov/document/NOPE-1", db=db_session))
    assert sum("/documents/" in c for c in api) == before + 1


@pytest.mark.parametrize("status,retryable,remembered", [
    (400, False, False), (401, True, False), (403, True, False), (410, False, True), (500, True, False),
    (503, True, False),
])
def test_a_failed_lookup_is_classified_by_what_asking_again_could_do(monkeypatch, db_session, status, retryable,
                                                                    remembered):
    from app.models import ApiCache

    monkeypatch.setattr(rg.settings, "DATA_GOV_API_KEY", "k", raising=False)
    monkeypatch.setattr(rg, "make_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status))))
    result = asyncio.run(rg.fetch_comments(URL, db=db_session))
    assert result["retryable"] is retryable
    missing = db_session.query(ApiCache).filter(ApiCache.cache_key.like("objectid-missing-%")).count()
    assert bool(missing) is remembered


def test_no_key_configured_is_never_cached(monkeypatch):
    monkeypatch.setattr(rg.settings, "DATA_GOV_API_KEY", "", raising=False)
    assert asyncio.run(rg.fetch_comments(URL))["retryable"] is True


def _charging(charged: list):
    """An async spend callback (as rate_limit.spend_upstream is) recording
    what it was charged."""
    async def spend(n):
        charged.append(n)
    return spend
