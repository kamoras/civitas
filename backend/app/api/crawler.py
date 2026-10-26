"""GET /api/crawler: what Civitas's crawler is, what it reads, how fast and
when, for /about/crawler. Every figure is read from the code (app/crawler.py)."""

from fastapi import APIRouter

from app import crawler
from app.api.response_helpers import CACHE_TTL_REFERENCE_S, JSONResponse, cached_json

router = APIRouter()


@router.get("/crawler")
def crawler_facts() -> JSONResponse:
    return cached_json(crawler.summary(), CACHE_TTL_REFERENCE_S)
