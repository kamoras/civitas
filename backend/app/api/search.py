"""Site search: suggestions as a reader types, and the /search page's
results (app/site_search.py).

Suggestions are lexical, with no model: they are asked for on every
keystroke. The results page also ranks Explore's documents by meaning
(services/explore_search.hybrid_search), which encodes the query with the
sentence-transformer already loaded for Explore: the one model use on a
read request (AGENTS.md, "Read path must stay lightweight"). It runs only
when no page is clearly meant, never for a direct jump.

Private caching only: the reader's own browser may keep an answer for a few
minutes, nginx keeps none, since a cached copy on its disk would keep the
query (AGENTS.md §8). The /search page asks for results server-side, so
nginx limits that page's requests per address instead (`location = /search`,
the same rate as Explore search's misses).
"""

import logging

from fastapi import APIRouter, Depends, Query
from app.api.response_helpers import JSONResponse
from sqlalchemy.orm import Session

from app import site_search
from app.database import get_db
from app.services.explore_search import hybrid_search

router = APIRouter()

PRIVATE_CACHE = "private, max-age=300"
# Explore's documents fused into a results page: the hybrid ranker's first
# page, as many as Explore itself showed.
DOCUMENTS = 30
logger = logging.getLogger(__name__)


def _answer(data: dict) -> JSONResponse:
    return JSONResponse(content=data, headers={"Cache-Control": PRIVATE_CACHE})


def _unavailable(q: str) -> JSONResponse:
    # Before the pipeline process has built the index (a fresh install):
    # the page says so and points to Explore, rather than reading as "no
    # results". Not cached: it changes within minutes.
    return JSONResponse(
        content={"query": q, "unavailable": True, "direct": None, "items": [], "groups": [],
                 "kinds": [], "alternatives": [], "near": []},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/search/suggest")
def suggest(
    q: str = Query("", max_length=site_search.QUERY_MAX),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Up to eight matches for what has been typed so far, best first, each
    with its kind, label, one line of context and the page's address, and
    `direct`: the page a search for exactly this goes straight to, or null
    when the matches are a list to choose from."""
    try:
        return _answer(site_search.suggest(q, db))
    except site_search.SearchUnavailable:
        return _unavailable(q)


@router.get("/search")
def search(
    q: str = Query("", max_length=site_search.QUERY_MAX),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """`direct` (as above), or every match and Explore document ranked
    together in `items` (each with its kind), with `kinds`, each kind's
    count; `alternatives`, the close calls beside the top match; `near`,
    the closest names by spelling when nothing matched."""

    def documents(words: str) -> dict | None:
        try:
            out = hybrid_search(db, words, limit=DOCUMENTS)
        except Exception:  # noqa: BLE001 - the matches still answer
            logger.exception("search: the documents' half failed")
            db.rollback()
            return None
        return out if out["indexReady"] else None

    try:
        return _answer(site_search.results(q, db, documents=documents))
    except site_search.SearchUnavailable:
        return _unavailable(q)
