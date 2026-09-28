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
    asyncio.run(rg.fetch_comments(URL, db=db_session, spend=charged.append))
    asyncio.run(rg.fetch_comments(URL, db=db_session, spend=charged.append))       # page cached
    asyncio.run(rg.fetch_comments(URL, page_number=2, db=db_session, spend=charged.append))  # objectId cached
    assert charged == [2, 1]
    assert sum("/documents/" in c for c in api) == 1


def test_a_refused_budget_sends_nothing(api, db_session):
    def refuse(n):
        raise RuntimeError("spent")
    with pytest.raises(RuntimeError):
        asyncio.run(rg.fetch_comments(URL, db=db_session, spend=refuse))
    assert api == []


def test_unknown_document(api):
    result = asyncio.run(rg.fetch_comments("https://www.regulations.gov/document/NOPE-1"))
    assert result["comments"] == [] and "not found" in result["error"]
