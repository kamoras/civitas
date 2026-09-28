"""
Civitas Public API v1

Open, rate-limited read-only API. No authentication required.
Rate limit: 60 requests / minute per IP (headers: X-RateLimit-*).
Docs: /docs
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.rate_limit import client_ip, limit_client, retry_after
from app.api.response_helpers import (
    CACHE_TTL_CONFIG_S,
    CACHE_TTL_DETAIL_S,
    CACHE_TTL_LIST_S,
    CACHE_TTL_REFERENCE_S,
    CACHE_TTL_SEARCH_S,
    FAILURE_RETRY_S,
    PARTY_QUERY_PATTERN,
)
from app.config_definitions import SCORE_WEIGHTS
from app.database import get_db, off_loop
from app.models import ScoreSnapshot
from app.pipeline.analyze.score_calculator import compute_overall_score
from fastapi import Request

router = APIRouter()

# ---------------------------------------------------------------------------
# Rate limiting — per client, counted in the throttle store every API worker
# process shares (api/throttle.py)
# ---------------------------------------------------------------------------

_RATE_LIMIT = 60
_RATE_PERIOD = 60.0


async def _rate_limit_dep(request: Request) -> None:
    decision = await asyncio.to_thread(
        limit_client, client_ip(request), "public-api", limit=_RATE_LIMIT, period=_RATE_PERIOD,
    )
    request.state.rl_remaining = decision.remaining
    request.state.rl_reset = decision.reset_at
    request.state.rl_counted = decision.counted
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — {_RATE_LIMIT} requests per minute per IP.",
            headers={
                "X-RateLimit-Limit": str(_RATE_LIMIT),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(decision.reset_at),
                "Retry-After": retry_after(decision.reset_at),
                "Access-Control-Allow-Origin": "*",
            },
        )


RateLimit = Annotated[None, Depends(_rate_limit_dep)]

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _rl_headers(request: Request) -> dict:
    headers = {"X-RateLimit-Limit": str(_RATE_LIMIT)}
    # Uncounted (the limiter's store couldn't answer): no count to report,
    # rather than a full quota that describes nothing.
    if getattr(request.state, "rl_counted", True):
        headers["X-RateLimit-Remaining"] = str(getattr(request.state, "rl_remaining", 0))
        headers["X-RateLimit-Reset"] = str(getattr(request.state, "rl_reset", 0))
    return headers


def _pub_json(data, request: Request, max_age: int = CACHE_TTL_LIST_S) -> JSONResponse:
    # private: the caller's browser may reuse it, a shared cache (nginx, a
    # CDN) may not — the X-RateLimit headers are this caller's own counts,
    # and a cached copy would hand them to everyone else.
    return JSONResponse(
        content=data,
        headers={
            "Cache-Control": f"private, max-age={max_age}",
            **_CORS_HEADERS,
            **_rl_headers(request),
        },
    )


# ---------------------------------------------------------------------------
# CORS preflight — must appear before other routes
# ---------------------------------------------------------------------------

@router.options("/{path:path}")
def preflight(path: str) -> Response:
    return Response(
        status_code=204,
        headers={**_CORS_HEADERS, "Access-Control-Max-Age": "3600"},
    )


# ---------------------------------------------------------------------------
# Score helper
# ---------------------------------------------------------------------------

def _overall(scores: dict) -> int:
    # int, not compute_overall_score's 2-decimal float — this is a stable
    # public API response field (api/public/v1/*), so the response shape
    # stays exactly as it was before this delegated to the shared helper.
    return round(compute_overall_score(scores))


# ---------------------------------------------------------------------------
# API index
# ---------------------------------------------------------------------------

@router.get("/")
def api_index(request: Request) -> JSONResponse:
    """Civitas Public API — index of available endpoints."""
    return JSONResponse(
        content={
            "name": "Civitas Public API",
            "version": "v1",
            "rateLimit": f"{_RATE_LIMIT} requests per minute per IP",
            "rateLimitHeaders": ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
            "scoreWeights": SCORE_WEIGHTS,
            "endpoints": {
                "GET /api/public/v1/states": "States with senator and representative counts",
                "GET /api/public/v1/senators": "Senators ranked by score — ?party=D|R|I &state=XX &page=N &per_page=N",
                "GET /api/public/v1/senators/{id}": "Full senator profile",
                "GET /api/public/v1/senators/{id}/history": "Historical score snapshots",
                "GET /api/public/v1/representatives": "Representatives — ?party=D|R|I &state=XX &page=N &per_page=N",
                "GET /api/public/v1/representatives/{id}": "Full representative profile",
                "GET /api/public/v1/representatives/{id}/history": "Historical score snapshots",
                "GET /api/public/v1/search": "Hybrid (semantic + keyword) search over government documents — ?q=text &chamber=senate|house &doc_type=X &politician_id=X &limit=N",
            },
            "docs": "/docs",
            "source": "https://github.com/kamoras/civitas",
        },
        headers=_CORS_HEADERS,
    )


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

@router.get("/states")
def list_states(
    _rl: RateLimit,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """All US states with senator and representative counts."""
    from app.services.senator_service import get_states_with_counts
    from app.services.representative_service import get_rep_states_with_counts

    sen_map = {s["code"]: s for s in [s.model_dump(by_alias=True) for s in get_states_with_counts(db)]}
    rep_map = {s["code"]: s for s in get_rep_states_with_counts(db)}

    all_codes = sorted(set(list(sen_map.keys()) + list(rep_map.keys())))
    result = [
        {
            "code": code,
            "name": (sen_map.get(code) or rep_map.get(code) or {}).get("name", code),
            "senatorCount": sen_map.get(code, {}).get("senatorCount", 0),
            "representativeCount": rep_map.get(code, {}).get("repCount", 0),
        }
        for code in all_codes
    ]
    return _pub_json(result, request, max_age=CACHE_TTL_REFERENCE_S)


# ---------------------------------------------------------------------------
# Senators
# ---------------------------------------------------------------------------

@router.get("/senators")
def list_senators(
    _rl: RateLimit,
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(50, ge=1, le=100, description="Results per page"),
    party: str | None = Query(None, pattern=PARTY_QUERY_PATTERN, description="Party filter: D, R, or I"),
    state: str | None = Query(None, min_length=2, max_length=2, description="Two-letter state code"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """US Senators ranked by overall representation score.

    Scores are the weighted sum defined by ``config_definitions.SCORE_WEIGHTS``:
    funding independence (33%), constituent alignment (33%), and legislative
    effectiveness (34%).
    """
    from app.services.senator_service import get_leaderboard

    all_entries = [e.model_dump(by_alias=True) for e in get_leaderboard(db)]

    if party:
        all_entries = [e for e in all_entries if e.get("party") == party.upper()]
    if state:
        all_entries = [e for e in all_entries if e.get("state", "").upper() == state.upper()]

    total = len(all_entries)
    total_pages = max(1, -(-total // per_page))
    page = max(1, min(page, total_pages))
    page_entries = all_entries[(page - 1) * per_page : page * per_page]

    for entry in page_entries:
        entry["overallScore"] = _overall(entry.get("representationScore", {}))

    return _pub_json(
        {
            "entries": page_entries,
            "total": total,
            "page": page,
            "perPage": per_page,
            "totalPages": total_pages,
        },
        request,
        max_age=CACHE_TTL_LIST_S,
    )


@router.get("/senators/{senator_id}/history")
def get_senator_history(
    senator_id: str,
    _rl: RateLimit,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Historical score snapshots for a senator (oldest → newest)."""
    snapshots = (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.entity_type == "senator", ScoreSnapshot.entity_id == senator_id)
        .order_by(ScoreSnapshot.date)
        .all()
    )
    return _pub_json(
        {
            "senatorId": senator_id,
            "snapshots": [
                {
                    "date": s.date,
                    "overallScore": round(s.overall_score, 1),
                    "scores": {
                        "fundingIndependence": round(s.score_1, 1),
                        "promisePersistence": round(s.score_2, 1),
                        "constituentAlignment": round(s.score_3, 1),
                        # Deprecated alias (the key's name until 2026-09).
                        "independentVoting": round(s.score_3, 1),
                        "fundingDiversity": round(s.score_4, 1),
                        "legislativeEffectiveness": round(s.score_5, 1),
                    },
                }
                for s in snapshots
            ],
        },
        request,
        max_age=CACHE_TTL_CONFIG_S,
    )


@router.get("/senators/{senator_id}")
def get_senator(
    senator_id: str,
    _rl: RateLimit,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Full senator profile: funding, voting record, campaign promises, sponsored bills."""
    from app.services.senator_service import get_senator_by_id

    result = get_senator_by_id(db, senator_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Senator not found")

    data = result.model_dump(by_alias=True)
    data["overallScore"] = _overall(data.get("representationScore", {}))
    return _pub_json(data, request, max_age=CACHE_TTL_DETAIL_S)


# ---------------------------------------------------------------------------
# Representatives
# ---------------------------------------------------------------------------

@router.get("/representatives")
def list_representatives(
    _rl: RateLimit,
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(50, ge=1, le=100, description="Results per page"),
    party: str | None = Query(None, pattern=PARTY_QUERY_PATTERN, description="Party filter: D, R, or I"),
    state: str | None = Query(None, min_length=2, max_length=2, description="Two-letter state code"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """House Representatives.

    When `state` is provided, returns representatives for that state ordered by district.
    Otherwise returns all representatives ranked by score (leaderboard view).
    """
    if state:
        from app.services.representative_service import get_representatives_by_state
        # party is pushed into the query so filtering happens BEFORE
        # pagination — post-filtering the page made entry counts vary per
        # page while total/totalPages described the unfiltered set.
        data = get_representatives_by_state(
            db, state, page=page, per_page=per_page, party=party,
        ).model_dump(by_alias=True)
    else:
        from app.services.representative_service import get_rep_leaderboard
        data = get_rep_leaderboard(db, page=page, per_page=per_page, party=party)

    for entry in data["entries"]:
        entry["overallScore"] = _overall(entry.get("representationScore", {}))

    return _pub_json(data, request, max_age=CACHE_TTL_LIST_S)


@router.get("/representatives/{rep_id}/history")
def get_representative_history(
    rep_id: str,
    _rl: RateLimit,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Historical score snapshots for a representative (oldest → newest)."""
    snapshots = (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.entity_type == "representative", ScoreSnapshot.entity_id == rep_id)
        .order_by(ScoreSnapshot.date)
        .all()
    )
    return _pub_json(
        {
            "representativeId": rep_id,
            "snapshots": [
                {
                    "date": s.date,
                    "overallScore": round(s.overall_score, 1),
                    "scores": {
                        "fundingIndependence": round(s.score_1, 1),
                        "promisePersistence": round(s.score_2, 1),
                        "constituentAlignment": round(s.score_3, 1),
                        # Deprecated alias (the key's name until 2026-09).
                        "independentVoting": round(s.score_3, 1),
                        "fundingDiversity": round(s.score_4, 1),
                        "legislativeEffectiveness": round(s.score_5, 1),
                    },
                }
                for s in snapshots
            ],
        },
        request,
        max_age=CACHE_TTL_CONFIG_S,
    )


@router.get("/representatives/{rep_id}")
def get_representative(
    rep_id: str,
    _rl: RateLimit,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Full representative profile: funding, voting record, campaign promises, sponsored bills."""
    from app.services.representative_service import get_representative_by_id

    rep = get_representative_by_id(db, rep_id)
    if rep is None:
        raise HTTPException(status_code=404, detail="Representative not found")

    result = rep.model_dump(by_alias=True)
    result["overallScore"] = _overall(result.get("representationScore", {}))
    return _pub_json(result, request, max_age=CACHE_TTL_DETAIL_S)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search")
async def search(
    _rl: RateLimit,
    request: Request,
    q: str = Query(..., min_length=2, max_length=200, description="Search query"),
    chamber: str | None = Query(
        None,
        pattern="^(?:[Ss]enate|[Hh]ouse|[Ee]xecutive|[Jj]udicial|[Rr]egulatory)$",
        description="Filter by chamber: senate, house, executive, judicial, or regulatory",
    ),
    doc_type: str | None = Query(
        None,
        description=(
            "Document type filter. Valid values: 'Senate Floor Speech', "
            "'House Floor Speech', 'Executive Order', 'Proclamation', "
            "'Presidential Memorandum', 'Supreme Court Opinion', 'Final Rule', "
            "'Proposed Rule', 'Notice'."
        ),
    ),
    politician_id: str | None = Query(None, description="Filter by politician ID (exact match)"),
    limit: int = Query(20, ge=1, le=50, description="Max results"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Search over government activity documents — floor speeches,
    presidential actions, Supreme Court opinions, and Federal Register
    rulemaking (not bill text or lobbying records).

    The same hybrid engine as the site's Explore page (semantic kNN + BM25F
    keyword, fused with recency and citation authority), so a query for an
    identifier — "Executive Order 14110", a docket number — finds it here
    too; the embedding alone can't tell two such numbers apart."""
    from app.api.explore import VALID_DOC_TYPES, _CHAMBER_CANONICAL
    from app.pipeline.lexical_index import HIGHLIGHT_END, HIGHLIGHT_START
    from app.services.explore_search import hybrid_search

    if doc_type is not None and doc_type not in VALID_DOC_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown doc_type. Valid values: {sorted(VALID_DOC_TYPES)}",
        )
    # Normalize chamber to the stored casing so a lowercase filter matches.
    canonical_chamber = _CHAMBER_CANONICAL.get(chamber.lower()) if chamber else None

    # Off the loop on a session of its own (database.off_loop).
    outcome = await off_loop(db, lambda session: hybrid_search(
        session,
        q,
        limit=limit,
        doc_type=doc_type,
        chamber=canonical_chamber,
        politician_id=politician_id,
    ))
    if not outcome["indexReady"]:
        return _pub_json(
            {"query": q, "results": [], "count": 0, "indexEmpty": True},
            request, max_age=0,
        )

    results = outcome["results"]
    for result in results:
        # The keyword channel marks matched terms with control characters
        # for the site's renderer; a public client gets plain text.
        result["snippet"] = (result.get("snippet") or "").replace(HIGHLIGHT_START, "").replace(HIGHLIGHT_END, "")
    # Keyword channel only (the vector index missing or mid-rebuild): a
    # partial answer, kept no longer than a failed fetch is.
    max_age = FAILURE_RETRY_S if outcome["semanticUnavailable"] else CACHE_TTL_SEARCH_S
    return _pub_json({
        "query": q, "results": results, "count": len(results),
        # True when the keyword channel alone answered: a partial ranking,
        # said so rather than presented as the whole one.
        "semanticUnavailable": bool(outcome["semanticUnavailable"]),
    }, request, max_age=max_age)
