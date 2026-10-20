"""Site search: suggestions as a reader types, and the /search page's
results (app/site_search.py).

Private caching only: the reader's own browser may keep an answer for a few
minutes, nginx keeps none, since a cached copy on its disk would keep the
query (AGENTS.md §8). Misses reach the backend through the catch-all /api/
location and its api-misses rate limit, like every other uncached read.
"""

from fastapi import APIRouter, Depends, Query
from app.api.response_helpers import JSONResponse
from sqlalchemy.orm import Session

from app import site_search
from app.database import get_db

router = APIRouter()

PRIVATE_CACHE = "private, max-age=300"


def _answer(data: dict) -> JSONResponse:
    return JSONResponse(content=data, headers={"Cache-Control": PRIVATE_CACHE})


def _unavailable(q: str) -> JSONResponse:
    # Before the pipeline process has built the index (a fresh install):
    # the page says so and points to Explore, rather than reading as "no
    # results". Not cached: it changes within minutes.
    return JSONResponse(
        content={"query": q, "unavailable": True, "direct": None, "items": [], "groups": [],
                 "alternatives": [], "near": [], "explore": {"count": None, "url": "/explore"}},
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
    """Every match, grouped by kind with counts; Explore's own count for the
    words; `direct` (as above); `alternatives`, the close calls beside the
    top match; `near`, the closest names by spelling when nothing matched."""
    try:
        return _answer(site_search.results(q, db))
    except site_search.SearchUnavailable:
        return _unavailable(q)
