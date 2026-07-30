"""Tests for GET /explore's own contract — validation, 503, response shape.

The ranking itself is covered by tests/test_explore_search.py; these are
about what the endpoint promises callers. `search_explore` is called
directly rather than through an ASGI TestClient, matching the pattern in
test_explore_api.py: `_rl` is `Annotated[None, Depends(...)]`, so passing
None bypasses the rate-limit dependency exactly as FastAPI would after
resolving it.
"""

import json

import pytest
from fastapi import HTTPException

from app.api.explore import VALID_DOC_TYPES, search_explore
from app.models import ExploreDocument
from app.pipeline.lexical_index import ensure_lexical_index
from app.services import explore_search


@pytest.fixture()
def indexed_db(db_session):
    assert ensure_lexical_index(db_session.get_bind()), "FTS5 unavailable"
    return db_session


@pytest.fixture(autouse=True)
def _no_vector_index(monkeypatch):
    """Stand in for the semantic channel being unavailable.

    Almost everything here is about the endpoint's own behaviour, and the
    keyword channel alone exercises all of it — which is itself a property
    worth having: these requests used to 503 outright in this state.
    """
    monkeypatch.setattr(
        explore_search, "search_explore_documents",
        lambda *a, **k: None,
    )


@pytest.fixture()
def vector_index_ready(monkeypatch):
    """Semantic channel up and returning no hits, overriding the fixture above."""
    monkeypatch.setattr(
        explore_search, "search_explore_documents",
        lambda *a, **k: [],
    )


def _add(db, **overrides) -> ExploreDocument:
    defaults = {
        "doc_type": "Final Rule", "source": "Federal Register",
        "title": "Untitled", "summary": "", "body": "",
        "date": "2026-01-01", "chamber": "Regulatory",
    }
    doc = ExploreDocument(**{**defaults, **overrides})
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


async def _search(db, q: str = "wildfire", **overrides):
    """Call the endpoint with every parameter supplied explicitly.

    Calling the coroutine directly skips FastAPI's dependency resolution, so
    an omitted argument arrives as the `Query(...)` marker object rather than
    its default — which would silently make `doc_type` a truthy non-string.
    """
    params = {
        "doc_type": None, "chamber": None, "commentable": False,
        "sort": "relevance", "limit": 20, "politician_id": None,
    }
    params.update(overrides)
    return await search_explore(None, q=q, db=db, **params)


def _body(response) -> dict:
    return json.loads(response.body)


class TestValidation:
    async def test_unknown_doc_type_is_rejected(self, indexed_db):
        # Documented as a 422 since the filter was added, but never actually
        # enforced: an unknown value was an exact-match miss that returned
        # zero results for a reason the caller could not see.
        with pytest.raises(HTTPException) as excinfo:
            await _search(indexed_db, "wildfire", doc_type="Blog Post")
        assert excinfo.value.status_code == 422

    async def test_known_doc_type_is_accepted(self, indexed_db):
        doc = _add(indexed_db, title="wildfire rule", doc_type="Final Rule")
        response = await _search(indexed_db, "wildfire", doc_type="Final Rule")
        assert [r["id"] for r in _body(response)["results"]] == [doc.id]

    async def test_every_valid_doc_type_passes_validation(self, indexed_db):
        # VALID_DOC_TYPES has to stay in step with what the pipeline actually
        # writes: a stale entry here is a filter the UI can offer and the
        # index can never satisfy.
        for doc_type in sorted(VALID_DOC_TYPES):
            _add(indexed_db, title=f"wildfire and {doc_type}", doc_type=doc_type)
        for doc_type in sorted(VALID_DOC_TYPES):
            response = await _search(indexed_db, "wildfire", doc_type=doc_type)
            assert response.status_code == 200
            assert [r["docType"] for r in _body(response)["results"]] == [doc_type]

    async def test_unknown_sort_is_rejected(self, indexed_db):
        with pytest.raises(HTTPException) as excinfo:
            await _search(indexed_db, "wildfire", sort="popularity")
        assert excinfo.value.status_code == 422


class TestChamberNormalisation:
    async def test_lowercase_chamber_still_matches(self, indexed_db):
        doc = _add(indexed_db, title="wildfire remarks", chamber="Senate",
                   doc_type="Senate Floor Speech")
        response = await _search(indexed_db, "wildfire", chamber="senate")
        assert [r["id"] for r in _body(response)["results"]] == [doc.id]


class TestIndexNotReady:
    async def test_no_keyword_match_while_the_vector_index_is_down_is_still_503(
        self, indexed_db
    ):
        # Not "no results". With half the engine unavailable the endpoint
        # genuinely cannot say the corpus has no match, and "still indexing,
        # check back" is the honest answer — the same contract the
        # semantic-only implementation had.
        _add(indexed_db, title="grazing permits")
        response = await _search(indexed_db, "wildfire")
        assert response.status_code == 503
        assert _body(response)["indexEmpty"] is True

    async def test_503_only_when_neither_channel_can_answer(self, indexed_db):
        response = await _search(indexed_db, "wildfire")
        assert response.status_code == 503
        assert _body(response)["indexEmpty"] is True
        assert response.headers["cache-control"] == "no-store"

    async def test_keyword_results_are_served_while_the_vector_index_rebuilds(
        self, indexed_db
    ):
        # A vector reindex takes minutes on the Pi. It used to take the whole
        # feature down for that whole window.
        doc = _add(indexed_db, title="wildfire response rule")
        response = await _search(indexed_db, "wildfire")
        assert response.status_code == 200
        assert [r["id"] for r in _body(response)["results"]] == [doc.id]


class TestResponseShape:
    async def test_result_carries_the_new_ranking_signals(self, indexed_db):
        _add(indexed_db, title="wildfire response rule", cited_by_count=3,
             authority=0.01, url="https://example.gov/x")
        body = _body(await _search(indexed_db, "wildfire"))
        result = body["results"][0]
        assert result["matchedBy"] == ["keyword"]
        assert result["citedByCount"] == 3
        assert result["duplicateCount"] == 0
        assert result["distance"] is None
        assert result["url"] == "https://example.gov/x"

    async def test_channel_counts_are_reported(self, indexed_db):
        _add(indexed_db, title="wildfire response rule")
        body = _body(await _search(indexed_db, "wildfire"))
        assert body["channels"] == {"semantic": 0, "keyword": 1}

    async def test_snippet_marks_matched_terms(self, indexed_db):
        _add(indexed_db, title="A Rule",
             body="the agency proposes new wildfire suppression funding")
        body = _body(await _search(indexed_db, "wildfire"))
        assert "\x02wildfire\x03" in body["results"][0]["snippet"]

    async def test_empty_result_set_is_a_200_not_a_503(
        self, indexed_db, vector_index_ready
    ):
        _add(indexed_db, title="grazing permits")
        response = await _search(indexed_db, "wildfire")
        assert response.status_code == 200
        assert _body(response)["count"] == 0

    async def test_json_is_serialisable_with_every_field_populated(self, indexed_db):
        # A stray non-JSON value in the ranker's output (a numpy float, a
        # datetime) fails here rather than in production.
        _add(indexed_db, title="wildfire rule", agency_name="Forest Service",
             comment_url="https://regulations.gov/x", comments_close_on="2099-01-01",
             politician_id="S001", politician_name="Sen. Smith", cited_by_count=2)
        body = _body(await _search(indexed_db, "wildfire"))
        assert body["results"][0]["agencyName"] == "Forest Service"


class TestFiltersAndSort:
    async def test_commentable_returns_only_open_comment_periods(self, indexed_db):
        open_doc = _add(indexed_db, title="wildfire proposed rule",
                        comment_url="https://regulations.gov/x",
                        comments_close_on="2099-01-01")
        _add(indexed_db, title="wildfire final rule")
        body = _body(await _search(indexed_db, "wildfire", commentable=True))
        assert [r["id"] for r in body["results"]] == [open_doc.id]

    async def test_sort_date_returns_the_newest_matching_not_the_newest_of_the_page(
        self, indexed_db
    ):
        newest = _add(indexed_db, title="wildfire notice newest", date="2026-07-01",
                      body="the most recent wildfire document in the corpus")
        for i in range(40):
            _add(indexed_db, title=f"wildfire notice {i}", date="2019-01-01",
                 body=f"an older wildfire document numbered {i}")
        body = _body(await _search(indexed_db, "wildfire", sort="date", limit=5))
        assert body["results"][0]["id"] == newest.id

    async def test_limit_is_respected(self, indexed_db):
        for i in range(8):
            _add(indexed_db, title=f"wildfire notice {i}", agency_name=f"Agency {i}",
                 body=f"wildfire document numbered {i}")
        body = _body(await _search(indexed_db, "wildfire", limit=3))
        assert body["count"] == 3


class TestDegradedMode:
    async def test_partial_results_are_labelled_as_partial(self, indexed_db):
        # The vector index is down, so these results came from the keyword
        # channel alone. Serving them silently would present half an answer
        # as a whole one, and the reader has no other way to tell.
        _add(indexed_db, title="wildfire response rule")
        body = _body(await _search(indexed_db, "wildfire"))
        assert body["semanticUnavailable"] is True

    async def test_healthy_index_is_not_labelled_partial(
        self, indexed_db, vector_index_ready
    ):
        # `channels.semantic == 0` is not the same thing: a filtered query
        # can retrieve zero vectors from a perfectly healthy index, and
        # conflating the two would claim a rebuild whenever a filter came
        # up empty on the semantic side.
        _add(indexed_db, title="wildfire response rule")
        body = _body(await _search(indexed_db, "wildfire"))
        assert body["semanticUnavailable"] is False
        assert body["channels"]["semantic"] == 0
        assert body["count"] == 1
