"""
Civitas Public API v1

Open, rate-limited read-only API. No authentication required.
Rate limit: 60 requests / minute per IP (headers: X-RateLimit-*).
Docs: /docs
"""

import asyncio
import threading
from collections import defaultdict, deque
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.rate_limit import client_ip
from app.config_definitions import SCORE_WEIGHTS
from app.database import get_db
from app.models import ExploreDocument, ScoreSnapshot
from fastapi import Request

router = APIRouter()

# ---------------------------------------------------------------------------
# Rate limiting — simple per-IP sliding window, no external dependencies
# ---------------------------------------------------------------------------

_RATE_LIMIT = 60
_RATE_PERIOD = 60.0

_rl_lock = threading.Lock()
_rl_window: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(ip: str) -> tuple[bool, int, int]:
    """Return (allowed, remaining, reset_epoch)."""
    now = time()
    cutoff = now - _RATE_PERIOD
    reset_at = int(now + _RATE_PERIOD)
    with _rl_lock:
        dq = _rl_window[ip]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _RATE_LIMIT:
            return False, 0, reset_at
        dq.append(now)
        return True, _RATE_LIMIT - len(dq), reset_at


def _rate_limit_dep(request: Request) -> None:
    ip = client_ip(request)
    allowed, remaining, reset_at = _check_rate_limit(ip)
    request.state.rl_remaining = remaining
    request.state.rl_reset = reset_at
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — {_RATE_LIMIT} requests per minute per IP.",
            headers={
                "X-RateLimit-Limit": str(_RATE_LIMIT),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_at),
                "Retry-After": "60",
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
    return {
        "X-RateLimit-Limit": str(_RATE_LIMIT),
        "X-RateLimit-Remaining": str(getattr(request.state, "rl_remaining", 0)),
        "X-RateLimit-Reset": str(getattr(request.state, "rl_reset", 0)),
    }


def _pub_json(data, request: Request, max_age: int = 300) -> JSONResponse:
    return JSONResponse(
        content=data,
        headers={
            "Cache-Control": f"public, max-age={max_age}",
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
    return round(sum(scores.get(key, 0) * weight for key, weight in SCORE_WEIGHTS.items()))


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
                "GET /api/public/v1/search": "Semantic search — ?q=text &chamber=senate|house &doc_type=X &politician_id=X &limit=N",
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
    return _pub_json(result, request, max_age=600)


# ---------------------------------------------------------------------------
# Senators
# ---------------------------------------------------------------------------

@router.get("/senators")
def list_senators(
    _rl: RateLimit,
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(50, ge=1, le=100, description="Results per page"),
    party: str | None = Query(None, pattern="^[DRI]$", description="Party filter: D, R, or I"),
    state: str | None = Query(None, min_length=2, max_length=2, description="Two-letter state code"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """US Senators ranked by overall representation score.

    Scores are computed from: funding independence (25%), promise persistence (20%),
    independent voting (20%), funding diversity (15%), legislative effectiveness (20%).
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
        max_age=300,
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
                        "independentVoting": round(s.score_3, 1),
                        "fundingDiversity": round(s.score_4, 1),
                        "legislativeEffectiveness": round(s.score_5, 1),
                    },
                }
                for s in snapshots
            ],
        },
        request,
        max_age=3600,
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
    return _pub_json(data, request, max_age=120)


# ---------------------------------------------------------------------------
# Representatives
# ---------------------------------------------------------------------------

@router.get("/representatives")
def list_representatives(
    _rl: RateLimit,
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(50, ge=1, le=100, description="Results per page"),
    party: str | None = Query(None, pattern="^[DRI]$", description="Party filter: D, R, or I"),
    state: str | None = Query(None, min_length=2, max_length=2, description="Two-letter state code"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """House Representatives.

    When `state` is provided, returns representatives for that state ordered by district.
    Otherwise returns all representatives ranked by score (leaderboard view).
    """
    if state:
        from app.services.representative_service import get_representatives_by_state
        data = get_representatives_by_state(db, state, page=page, per_page=per_page)
        if party:
            data["entries"] = [e for e in data["entries"] if e.get("party") == party.upper()]
    else:
        from app.services.representative_service import get_rep_leaderboard
        data = get_rep_leaderboard(db, page=page, per_page=per_page, party=party)

    for entry in data["entries"]:
        entry["overallScore"] = _overall(entry.get("representationScore", {}))

    return _pub_json(data, request, max_age=300)


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
                        "independentVoting": round(s.score_3, 1),
                        "fundingDiversity": round(s.score_4, 1),
                        "legislativeEffectiveness": round(s.score_5, 1),
                    },
                }
                for s in snapshots
            ],
        },
        request,
        max_age=3600,
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

    result = get_representative_by_id(db, rep_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Representative not found")

    result["overallScore"] = _overall(result.get("representationScore", {}))
    return _pub_json(result, request, max_age=120)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search")
async def search(
    _rl: RateLimit,
    request: Request,
    q: str = Query(..., min_length=2, max_length=200, description="Search query"),
    chamber: str | None = Query(None, pattern="^(senate|house)$", description="Filter by chamber"),
    doc_type: str | None = Query(None, description="Document type filter (e.g. bill, lobbying, federal_register)"),
    politician_id: str | None = Query(None, description="Filter by politician ID"),
    limit: int = Query(20, ge=1, le=50, description="Max results"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Semantic search over federal documents, bills, and lobbying records."""
    from app.pipeline.vector_store import search_explore_documents

    results = await asyncio.to_thread(
        search_explore_documents,
        query=q,
        n_results=limit,
        doc_type=doc_type,
        chamber=chamber,
    )

    doc_ids = [r["id"] for r in results if r.get("id")]
    if doc_ids:
        docs = (
            db.query(
                ExploreDocument.id,
                ExploreDocument.url,
                ExploreDocument.summary,
                ExploreDocument.agency_name,
                ExploreDocument.politician_id,
            )
            .filter(ExploreDocument.id.in_(doc_ids))
            .all()
        )
        doc_map = {d.id: d for d in docs}
        for result in results:
            doc = doc_map.get(result.get("id"))
            if doc:
                result["url"] = doc.url or ""
                result["summary"] = doc.summary or result.get("snippet", "")
                result["agencyName"] = doc.agency_name or ""

    if politician_id:
        matching_ids = {
            row[0]
            for row in db.query(ExploreDocument.id)
            .filter(ExploreDocument.id.in_(doc_ids), ExploreDocument.politician_id == politician_id)
            .all()
        }
        results = [r for r in results if r.get("id") in matching_ids]

    return _pub_json({"query": q, "results": results[:limit], "count": len(results[:limit])}, request, max_age=60)
