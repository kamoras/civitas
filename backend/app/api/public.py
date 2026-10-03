"""
Civitas Public API v1

Open, read-only, no key or account. Rate-limited per IP (rate_limit.
PUBLIC_READ_LIMIT, reported in X-RateLimit-* headers).

Documented at /developers, from the spec at /api/public/v1/openapi.json,
which FastAPI generates from these routes and their response schemas
(app/schemas.py, "Public API v1") — so the documentation describes
whatever code is running, and tests/test_public_api_contract.py fails
when a response stops matching it. The MCP server (api/public_mcp.py)
turns the same spec into tools.

Conventions every route keeps, so a caller learns them once:
- Lists are pages: {entries, total, page, perPage, totalPages}.
- A member list's `rank` is the member's place in their whole chamber,
  whatever filters are applied.
- An unknown id is a 404, including on /history (never an empty 200).
- `siteUrl` links each record to its page on Civitas.
"""

from typing import Literal

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.api.rate_limit import PUBLIC_READ_LIMIT, PublicReadLimit
from app.api.response_helpers import (
    CACHE_TTL_CONFIG_S,
    CACHE_TTL_DETAIL_S,
    CACHE_TTL_LIST_S,
    CACHE_TTL_REFERENCE_S,
    CACHE_TTL_SEARCH_S,
    EXPLORE_CHAMBERS,
    EXPLORE_DOC_TYPES,
    FAILURE_RETRY_S,
)
from app.api.visits import record_api_request
from app.broadcast import SITE_URL
from app.config_definitions import SCORE_WEIGHTS
from app.database import get_db, off_loop
from app.models import Representative, ScoreSnapshot, Senator
from app.pipeline.lexical_index import HIGHLIGHT_END, HIGHLIGHT_START
from app.schemas import (
    PublicApiIndexSchema,
    PublicHistorySchema,
    PublicRepresentativePageSchema,
    PublicRepresentativeProfileSchema,
    PublicSearchResponseSchema,
    PublicSenatorPageSchema,
    PublicSenatorProfileSchema,
    PublicStateSchema,
)
from app.services.explore_search import hybrid_search
from app.services.pagination import paginate_bounds
from app.services.representative_service import (
    get_rep_leaderboard,
    get_rep_states_with_counts,
    get_representative_by_id,
)
from app.services.senator_service import get_leaderboard, get_senator_by_id, get_states_with_counts

# Where api/router.py mounts this router.
PREFIX = "/api/public/v1"

# Set by the MCP server on the requests its tool calls make (public_mcp.py),
# so usage counts tell the two channels apart. nginx clears it on every
# request from outside, so a caller can't claim to be MCP.
CHANNEL_HEADER = "X-Civitas-Channel"


class _CountedRoute(APIRoute):
    """Counts each documented endpoint's requests by outcome
    (visits.record_api_request -> ApiRequestCount), including the 404s,
    422s and 429s raised before the handler returns. Undocumented routes
    (the CORS preflight, the spec itself) are not API use and aren't
    counted."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()
        if not self.include_in_schema:
            return handler
        endpoint = self.name

        async def counted(request: Request) -> Response:
            channel = "mcp" if request.headers.get(CHANNEL_HEADER) == "mcp" else "http"
            try:
                response = await handler(request)
            except HTTPException as exc:
                record_api_request(endpoint, channel, exc.status_code)
                raise
            except RequestValidationError:
                record_api_request(endpoint, channel, 422)
                raise
            except Exception:
                record_api_request(endpoint, channel, 500)
                raise
            record_api_request(endpoint, channel, response.status_code)
            return response

        return counted


router = APIRouter(route_class=_CountedRoute)

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version",
}

_NOT_FOUND = {404: {"description": "No member with that id"}}

# A page size larger than any chamber, for reading one whole.
_WHOLE_CHAMBER = 10_000

Party = Literal["D", "R", "I"]
Chamber = Literal[tuple(EXPLORE_CHAMBERS)]  # type: ignore[valid-type]
DocType = Literal[EXPLORE_DOC_TYPES]  # type: ignore[valid-type]

_PAGE = Query(1, ge=1, description="Page number, from 1")
_PER_PAGE = Query(50, ge=1, le=100, description="Results per page")
_PARTY = Query(None, description="Only this party")
_STATE = Query(None, pattern="^[A-Za-z]{2}$", description="Only this state (two-letter code, e.g. GA)")


def _rl_headers(request: Request) -> dict:
    headers = {"X-RateLimit-Limit": str(PUBLIC_READ_LIMIT)}
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


def _member_url(member_id: str) -> str:
    return f"{SITE_URL}/politicians/{member_id}"


def _page(rows: list[dict], party: str | None, state: str | None, page: int, per_page: int) -> dict:
    """Filter ranked rows, then paginate; `rank` stays the chamber-wide one."""
    if party:
        rows = [r for r in rows if r["party"] == party]
    if state:
        rows = [r for r in rows if r["state"] == state.upper()]
    total_pages, page = paginate_bounds(len(rows), page, per_page)
    entries = rows[(page - 1) * per_page : page * per_page]
    for row in entries:
        row["siteUrl"] = _member_url(row["id"])
    return {
        "entries": entries,
        "total": len(rows),
        "page": page,
        "perPage": per_page,
        "totalPages": total_pages,
    }


def _history(db: Session, entity_type: str, model, member_id: str, request: Request) -> JSONResponse:
    if db.get(model, member_id) is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND[404]["description"])
    snapshots = (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.entity_type == entity_type, ScoreSnapshot.entity_id == member_id)
        .order_by(ScoreSnapshot.date)
        .all()
    )
    return _pub_json(
        {
            "id": member_id,
            "snapshots": [
                {
                    "date": s.date,
                    "overall": round(s.overall_score, 1),
                    "fundingIndependence": round(s.score_1, 1),
                    "promisePersistence": round(s.score_2, 1),
                    "constituentAlignment": round(s.score_3, 1),
                    "fundingDiversity": round(s.score_4, 1),
                    "legislativeEffectiveness": round(s.score_5, 1),
                }
                for s in snapshots
            ],
        },
        request,
        max_age=CACHE_TTL_CONFIG_S,
    )


_PREFLIGHT_HEADERS = {**_CORS_HEADERS, "Access-Control-Max-Age": "3600"}


@router.options("/{path:path}", include_in_schema=False)
def preflight(path: str) -> Response:
    return Response(status_code=204, headers=_PREFLIGHT_HEADERS)


class PublicApiPreflight:
    """Answers CORS preflights for this API before the site's CORSMiddleware
    sees them (main.py adds this outside it).

    The site's CORSMiddleware allows the site's own origins only, and it
    answers every preflight itself, before routing: one from any other
    origin got 400 "Disallowed CORS origin" and the route above never ran.
    A simple GET from a page still worked (the route adds the open headers
    to its own answer), so this went unnoticed; a POST of JSON (every MCP
    call from a browser-based client) or a request with an Mcp-* header is
    preflighted, and was refused for every origin but the site's — while
    the spec said CORS is open to every origin. Here, under PREFIX, it is.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] == "http" and scope["method"] == "OPTIONS"
            and scope["path"].startswith(PREFIX + "/")
            and any(k == b"access-control-request-method" for k, _ in scope["headers"])
        ):
            await send({
                "type": "http.response.start", "status": 204,
                "headers": [(k.lower().encode(), v.encode()) for k, v in _PREFLIGHT_HEADERS.items()],
            })
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Spec and index
# ---------------------------------------------------------------------------

_spec: dict | None = None


def openapi_spec_dict() -> dict:
    """OpenAPI 3 description of this API alone — none of the site's internal
    endpoints, which change with the pages they serve. Built once per process
    from the running routes, so it changes exactly when the code does."""
    global _spec
    if _spec is None:
        # A throwaway app holding only this router: FastAPI's own generator,
        # scoped to the public routes, without walking the real app's tree.
        spec_app = FastAPI(
            title="Civitas Public API",
            version="v1",
            # Operation ids are the route functions' names (list_senators,
            # get_senator...): the MCP tool names, and readable method names
            # for any client generated from the spec.
            generate_unique_id_function=lambda route: route.name,
            # Group order and introductions for /developers and generated clients.
            openapi_tags=[
                {"name": "Senators", "description": "The 100 serving senators: rankings, full records and score history."},
                {"name": "Representatives", "description": "The serving House members: rankings, full records and score history."},
                {"name": "Search", "description": "The government documents behind the records, searched the way the site's Explore page does."},
                {"name": "Reference", "description": "What the API offers, and the states it covers."},
            ],
            summary="Open, read-only access to Civitas's scores, member records and document search.",
            description=(
                f"No key or account. {PUBLIC_READ_LIMIT} requests per minute per IP, reported in the "
                "X-RateLimit-Limit, X-RateLimit-Remaining and X-RateLimit-Reset headers; over it, "
                "429 with Retry-After. CORS is open to every origin. Scores run 0-100, higher is a "
                f"better representative; how each is computed: {SITE_URL}/about/scores."
            ),
        )
        spec_app.include_router(router, prefix=PREFIX)
        _spec = spec_app.openapi()
        # Machine-readable, for /developers to state rather than repeat.
        _spec["info"]["x-rate-limit-per-minute"] = PUBLIC_READ_LIMIT
    return _spec


@router.get("/openapi.json", include_in_schema=False)
def openapi_spec() -> JSONResponse:
    return JSONResponse(
        openapi_spec_dict(),
        headers={"Cache-Control": f"public, max-age={CACHE_TTL_CONFIG_S}", **_CORS_HEADERS},
    )


@router.get("/", response_model=PublicApiIndexSchema, tags=["Reference"], summary="What this API offers")
def api_index() -> JSONResponse:
    """Every endpoint with a one-line summary, the rate limit and where the
    documentation is. Built from the same spec, so it lists what exists."""
    endpoints = {
        f"{method.upper()} {path}": op.get("summary", "")
        for path, ops in openapi_spec_dict()["paths"].items()
        for method, op in ops.items()
    }
    return JSONResponse(
        content={
            "name": "Civitas Public API",
            "version": "v1",
            "rateLimit": f"{PUBLIC_READ_LIMIT} requests per minute per IP",
            "scoreWeights": SCORE_WEIGHTS,
            "endpoints": endpoints,
            "docs": f"{SITE_URL}/developers",
            "openapi": f"{SITE_URL}{PREFIX}/openapi.json",
            "mcp": f"{SITE_URL}{PREFIX}/mcp",
            "source": "https://github.com/kamoras/civitas",
        },
        headers=_CORS_HEADERS,
    )


@router.get("/states", response_model=list[PublicStateSchema], tags=["Reference"],
            summary="States and how many members each has")
def list_states(
    _rl: PublicReadLimit,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Every state with senators or representatives, with its member counts."""
    senate = {s.code: s for s in get_states_with_counts(db)}
    house = {s["code"]: s for s in get_rep_states_with_counts(db)}
    result = [
        {
            "code": code,
            "name": senate[code].name if code in senate else house[code]["name"],
            "senatorCount": senate[code].senator_count if code in senate else 0,
            "representativeCount": house[code]["repCount"] if code in house else 0,
        }
        for code in sorted(senate.keys() | house.keys())
    ]
    return _pub_json(result, request, max_age=CACHE_TTL_REFERENCE_S)


# ---------------------------------------------------------------------------
# Senators
# ---------------------------------------------------------------------------

def _senate_rows(db: Session) -> list[dict]:
    """Every serving senator, best score first, with a competition rank."""
    rows = [e.model_dump(by_alias=True) for e in get_leaderboard(db)]
    for i, row in enumerate(rows):
        tied = i and row["representationScore"]["overall"] == rows[i - 1]["representationScore"]["overall"]
        row["rank"] = rows[i - 1]["rank"] if tied else i + 1
    return rows


@router.get("/senators", response_model=PublicSenatorPageSchema, tags=["Senators"],
            summary="Serving senators, ranked by score")
def list_senators(
    _rl: PublicReadLimit,
    request: Request,
    page: int = _PAGE,
    per_page: int = _PER_PAGE,
    party: Party | None = _PARTY,
    state: str | None = _STATE,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Serving senators, best representation score first.

    `representationScore.overall` is the weighted score (weights in the
    index's `scoreWeights`); `rank` is the senator's place among all 100,
    whatever the filters."""
    return _pub_json(_page(_senate_rows(db), party, state, page, per_page), request)


@router.get("/senators/{senator_id}", response_model=PublicSenatorProfileSchema, responses=_NOT_FOUND,
            tags=["Senators"], summary="One senator's full record")
def get_senator(
    _rl: PublicReadLimit,
    request: Request,
    senator_id: str = Path(description="The senator's id, as in their Civitas URL (e.g. jon-ossoff)"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Scores, funding and top donors, voting record, lobbying matches,
    sponsored bills and contact details."""
    senator = get_senator_by_id(db, senator_id)
    if senator is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND[404]["description"])
    return _pub_json(
        {**senator.model_dump(by_alias=True), "siteUrl": _member_url(senator_id)},
        request, max_age=CACHE_TTL_DETAIL_S,
    )


@router.get("/senators/{senator_id}/history", response_model=PublicHistorySchema, responses=_NOT_FOUND,
            tags=["Senators"], summary="A senator's scores over time")
def get_senator_history(
    _rl: PublicReadLimit,
    request: Request,
    senator_id: str = Path(description="The senator's id"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """One snapshot per scoring run, oldest first."""
    return _history(db, "senator", Senator, senator_id, request)


# ---------------------------------------------------------------------------
# Representatives
# ---------------------------------------------------------------------------

@router.get("/representatives", response_model=PublicRepresentativePageSchema, tags=["Representatives"],
            summary="Serving representatives, ranked by score")
def list_representatives(
    _rl: PublicReadLimit,
    request: Request,
    page: int = _PAGE,
    per_page: int = _PER_PAGE,
    party: Party | None = _PARTY,
    state: str | None = _STATE,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Serving representatives, best representation score first.

    `rank` is the member's place in the whole House, whatever the filters.
    Filter by `state` for a delegation; `district` is 0 for an at-large
    seat."""
    rows = get_rep_leaderboard(db, page=1, per_page=_WHOLE_CHAMBER)["entries"]
    return _pub_json(_page(rows, party, state, page, per_page), request)


@router.get("/representatives/{rep_id}", response_model=PublicRepresentativeProfileSchema,
            responses=_NOT_FOUND, tags=["Representatives"], summary="One representative's full record")
def get_representative(
    _rl: PublicReadLimit,
    request: Request,
    rep_id: str = Path(description="The representative's id, as in their Civitas URL (e.g. joe-neguse)"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Scores, funding and top donors, voting record, lobbying matches,
    sponsored bills and contact details."""
    rep = get_representative_by_id(db, rep_id)
    if rep is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND[404]["description"])
    return _pub_json(
        {**rep.model_dump(by_alias=True), "siteUrl": _member_url(rep_id)},
        request, max_age=CACHE_TTL_DETAIL_S,
    )


@router.get("/representatives/{rep_id}/history", response_model=PublicHistorySchema, responses=_NOT_FOUND,
            tags=["Representatives"], summary="A representative's scores over time")
def get_representative_history(
    _rl: PublicReadLimit,
    request: Request,
    rep_id: str = Path(description="The representative's id"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """One snapshot per scoring run, oldest first."""
    return _history(db, "representative", Representative, rep_id, request)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search", response_model=PublicSearchResponseSchema, tags=["Search"],
            summary="Search floor speeches, presidential actions, opinions and rules")
async def search_documents(
    _rl: PublicReadLimit,
    request: Request,
    q: str = Query(min_length=2, max_length=200, description="What to look for: words, a topic or an "
                   "identifier such as \"Executive Order 14110\""),
    chamber: Chamber | None = Query(None, description="Only documents from this branch"),
    doc_type: DocType | None = Query(None, description="Only this kind of document"),
    politician_id: str | None = Query(None, description="Only documents by this member (their id)"),
    limit: int = Query(20, ge=1, le=50, description="How many results"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Floor speeches, presidential actions, Supreme Court opinions and
    Federal Register rules (not bill text or lobbying records), best match
    first.

    The same engine as the site's Explore page: meaning and exact words
    together, weighed with recency and how often other documents cite
    each one — so an identifier or docket number is found too."""
    # Off the loop on a session of its own (database.off_loop).
    outcome = await off_loop(db, lambda session: hybrid_search(
        session, q, limit=limit, doc_type=doc_type,
        chamber=EXPLORE_CHAMBERS[chamber] if chamber else None,
        politician_id=politician_id,
    ))
    if not outcome["indexReady"]:
        return _pub_json(
            {"query": q, "results": [], "count": 0, "partial": False, "indexBuilding": True},
            request, max_age=0,
        )

    results = outcome["results"]
    for result in results:
        # The keyword channel marks matched terms with control characters
        # for the site's renderer; a public client gets plain text.
        result["snippet"] = (result.get("snippet") or "").replace(HIGHLIGHT_START, "").replace(HIGHLIGHT_END, "")
        result["siteUrl"] = f"{SITE_URL}/explore/{result['id']}"
    # Keyword channel only (the vector index missing or mid-rebuild): a
    # partial answer, kept no longer than a failed fetch is.
    partial = bool(outcome["semanticUnavailable"])
    return _pub_json(
        {"query": q, "results": results, "count": len(results), "partial": partial, "indexBuilding": False},
        request, max_age=FAILURE_RETRY_S if partial else CACHE_TTL_SEARCH_S,
    )
