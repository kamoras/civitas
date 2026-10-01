"""Explore API — semantic search over government activity documents."""

import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import check_pipeline_token

from app.api import throttle
from app.api.rate_limit import (
    PublicReadLimit, UpstreamRouteLimit, WriteRateLimit, client_ip, limit_client, retry_after, spend_upstream,
)
from app.api.response_helpers import (
    EXPLORE_CHAMBERS,
    EXPLORE_DOC_TYPES,
    RETRY_SOON_CACHE_CONTROL,
    retry_soon_json,
)
from app.database import get_db, off_loop
from app.models import ExploreDocument
from app.services.explore_search import browse_documents, hybrid_search
from app.time_utils import comment_period_today

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/explore")

# Shared with the public API's search (api/response_helpers.py).
_CHAMBER_CANONICAL = EXPLORE_CHAMBERS
VALID_DOC_TYPES = frozenset(EXPLORE_DOC_TYPES)

VALID_SORTS = {"relevance", "date"}


@router.get("")
async def search_explore(
    _rl: PublicReadLimit,
    q: str | None = Query(
        None, min_length=2, max_length=200,
        description="Search query. Optional with politician_id: that member's documents, newest first",
    ),
    doc_type: str | None = Query(None, description="Filter by document type"),
    chamber: str | None = Query(None, description="Filter by chamber"),
    commentable: bool = Query(False, description="Only show documents open for comment"),
    sort: str = Query("relevance", description="Sort order: relevance or date"),
    limit: int = Query(20, ge=1, le=50),
    politician_id: str | None = Query(None, description="Filter by politician ID (exact match)"),
    db: Session = Depends(get_db),
):
    """Hybrid search over government activity documents.

    Combines semantic (sentence-transformer kNN) and keyword (BM25F)
    retrieval with recency and citation-graph authority, fused by weighted
    reciprocal rank fusion. See `services/explore_search.py` for the
    ranking itself and `config_definitions` for the weights.

    `sort=date` orders the whole filtered candidate pool by date, not the
    relevance page — "newest matching document", not "newest of the twenty
    most similar".
    """
    if doc_type is not None and doc_type not in VALID_DOC_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown doc_type. Valid values: {sorted(VALID_DOC_TYPES)}",
        )
    if sort not in VALID_SORTS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sort. Valid values: {sorted(VALID_SORTS)}",
        )

    canonical_chamber = (
        _CHAMBER_CANONICAL.get(chamber.lower(), chamber) if chamber else None
    )

    if q is None:
        # A profile's "view all documents" link: the member's whole record,
        # not a search of it.
        if not politician_id:
            raise HTTPException(status_code=422, detail="q is required without politician_id")
        outcome = await off_loop(db, lambda session: browse_documents(
            session, politician_id, limit=limit, doc_type=doc_type,
            chamber=canonical_chamber, commentable=commentable,
        ))
        return JSONResponse(
            content={"query": "", "results": outcome["results"], "count": outcome["count"],
                     "semanticUnavailable": False, "channels": outcome["channels"]},
            headers={"Cache-Control": "public, max-age=60, stale-while-revalidate=60"},
        )

    # Off the loop on a session of its own (database.off_loop): the request's
    # is closed under a thread still using it if the request is cancelled.
    outcome = await off_loop(db, lambda session: hybrid_search(
        session,
        q,
        limit=limit,
        doc_type=doc_type,
        chamber=canonical_chamber,
        politician_id=politician_id,
        commentable=commentable,
        sort=sort,
    ))

    # indexReady is False only when neither channel could answer: the
    # semantic index is missing or mid-rebuild AND the keyword index
    # returned nothing. Surface that distinctly so the UI can say "still
    # indexing" instead of the misleading "no matches" (the SQL-based stats
    # header may simultaneously report thousands of documents, which a
    # vector-store reset does not touch).
    if not outcome["indexReady"]:
        return JSONResponse(
            status_code=503,
            content={"query": q, "results": [], "count": 0, "indexEmpty": True},
            headers={"Cache-Control": "no-store"},
        )

    return JSONResponse(
        content={
            "query": q,
            "results": outcome["results"],
            "count": outcome["count"],
            # True when these results came from the keyword channel alone
            # because the vector index is missing or mid-rebuild. The page
            # says so rather than presenting a partial answer as a whole one.
            "semanticUnavailable": outcome["semanticUnavailable"],
            "channels": outcome["channels"],
        },
        # A partial answer (keyword channel only) is kept only as long as a
        # failed fetch is (response_helpers.FAILURE_RETRY_S): the index is
        # back within a rebuild, and a whole answer shouldn't wait out a
        # success's lifetime behind it.
        headers={"Cache-Control": (
            RETRY_SOON_CACHE_CONTROL if outcome["semanticUnavailable"]
            else "public, max-age=60, stale-while-revalidate=60"
        )},
    )


def _explore_counts(db: Session) -> tuple[int, dict[str, int], dict[str, int], int]:
    """(total, by type, by chamber, open for comment), in one pass over the
    table — on a worker thread (explore_stats' off_loop)."""
    from sqlalchemy import case, func

    open_now = case(
        (
            (ExploreDocument.comment_url.isnot(None))
            & (ExploreDocument.comment_url != "")
            & (ExploreDocument.comments_close_on >= comment_period_today()),
            1,
        ),
        else_=0,
    )
    rows = (
        db.query(ExploreDocument.doc_type, ExploreDocument.chamber, func.count(), func.sum(open_now))
        .group_by(ExploreDocument.doc_type, ExploreDocument.chamber)
        .all()
    )
    total = open_for_comment = 0
    type_counts: dict[str, int] = {}
    chamber_counts: dict[str, int] = {}
    for doc_type, chamber, count, open_count in rows:
        total += count
        open_for_comment += open_count or 0
        type_counts[doc_type] = type_counts.get(doc_type, 0) + count
        if chamber:
            chamber_counts[chamber] = chamber_counts.get(chamber, 0) + count
    return total, type_counts, chamber_counts, open_for_comment


@router.get("/stats")
async def explore_stats(db: Session = Depends(get_db)):
    """Return counts of explore documents by type and chamber."""
    total, type_counts, chamber_counts, open_for_comment = await off_loop(db, _explore_counts)
    return JSONResponse(
        content={
            "totalDocuments": total,
            "byType": type_counts,
            "byChamber": chamber_counts,
            "openForComment": open_for_comment,
        },
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=300"},
    )


async def _load_document(db: Session, doc_id: int) -> ExploreDocument | None:
    """The document, read off the event loop on a session of its own
    (off_loop) and detached from it, its columns loaded."""
    def read(session):
        doc = session.query(ExploreDocument).filter(ExploreDocument.id == doc_id).first()
        if doc is not None:
            session.expunge(doc)
        return doc

    return await off_loop(db, read)


@router.get("/{doc_id}")
async def get_explore_document(doc_id: int, db: Session = Depends(get_db)):
    """Return full details for a single explore document."""
    doc = await _load_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return JSONResponse(
        content={
            "id": doc.id,
            "title": doc.title,
            "summary": doc.summary,
            "body": doc.body,
            "date": doc.date,
            "docType": doc.doc_type,
            "source": doc.source,
            "url": doc.url or "",
            "politicianName": doc.politician_name or "",
            "politicianId": doc.politician_id or "",
            "chamber": doc.chamber or "",
            "agencyName": doc.agency_name or "",
            "commentUrl": doc.comment_url or "",
            "commentsCloseOn": doc.comments_close_on or "",
        },
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=300"},
    )


@router.get("/{doc_id}/comments")
async def get_document_comments(
    _rl: UpstreamRouteLimit,
    doc_id: int,
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(25, ge=1, le=25),
    db: Session = Depends(get_db),
):
    """Fetch public comments for a regulatory document from regulations.gov."""
    doc = await _load_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.comment_url:
        return JSONResponse(content={
            "comments": [],
            "totalElements": 0,
            "message": "This document does not accept public comments.",
        })

    from app.pipeline.fetch.regulations_gov import fetch_comments
    result = await fetch_comments(
        comment_url=doc.comment_url,
        page_size=page_size,
        page_number=page,
        db=db,
        spend=spend_upstream,
    )
    # Fetched live: a retryable error (a rate limit, a timeout) is this
    # moment's, not the document's, and no cache may keep it. A permanent
    # one (no such document, a refused id) is the same answer next time,
    # cached like a good one so a repeat spends nothing. Answered 200 either way — the page
    # shows the message in place of the list.
    if result.get("retryable"):
        return retry_soon_json(result)
    # Cached for the middleware's default lifetime (api/cache_headers.py).
    return JSONResponse(content=result)


class CommentSubmission(BaseModel):
    comment: str = Field(..., min_length=10, max_length=5000, description="Comment text")
    name: str = Field("Anonymous", max_length=100, description="Your name")
    organization: str = Field("", max_length=200, description="Organization (optional)")
    dry_run: bool = Field(False, description="Validate without submitting")


@router.post("/{doc_id}/comments")
async def post_document_comment(
    doc_id: int,
    _rl: WriteRateLimit,
    submission: CommentSubmission | None = Body(None),
    db: Session = Depends(get_db),
):
    """Submit a public comment on a regulatory document via regulations.gov.

    Send the comment as a JSON body, never a query string: a URL lands in
    nginx's and uvicorn's access logs, which would put every commenter's name
    and full comment text in the container logs (the query transport, kept
    one release for frontend tasks still rolling, was removed 2026-09). Set
    ``dry_run`` to validate everything without actually submitting.
    """
    if submission is None:
        raise HTTPException(status_code=422, detail="Comment text is required")
    comment = submission.comment
    name = submission.name
    organization = submission.organization
    dry_run = submission.dry_run

    doc = await _load_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.comment_url:
        raise HTTPException(status_code=400, detail="This document does not accept public comments")

    if doc.comments_close_on:
        if doc.comments_close_on < comment_period_today():
            raise HTTPException(status_code=400, detail="The comment period for this document has closed")

    from app.pipeline.fetch.regulations_gov import submit_comment, _extract_document_id

    if dry_run:
        reg_doc_id = _extract_document_id(doc.comment_url)
        return JSONResponse(content={
            "success": True,
            "dryRun": True,
            "message": "Validation passed. Comment would be submitted to regulations.gov.",
            "payload": {
                "commentOnDocumentId": reg_doc_id,
                "comment": comment.strip()[:80] + ("..." if len(comment.strip()) > 80 else ""),
                "commentLength": len(comment.strip()),
                "submitterName": name.strip() or "Anonymous",
                "organization": organization.strip() or None,
                "targetUrl": doc.comment_url,
                "commentsCloseOn": doc.comments_close_on,
            },
        })

    result = await submit_comment(
        comment_url=doc.comment_url,
        comment_text=comment,
        submitter_name=name,
        organization=organization,
    )

    status_code = 201 if result.get("success") else 400
    return JSONResponse(content=result, status_code=status_code)


# Every summary request not served from the cache: a page waiting out a
# generation asks every 10-60s, well inside this; a client looping on the
# endpoint is held to one a second.
_SUMMARY_REQUESTS_BUCKET = "explore-summary-requests"
_SUMMARY_REQUESTS_PER_MINUTE = 60

# A stream is read as it is written: nginx buffers proxied responses by
# default, which would hold every delta until the generation ended.
# X-Accel-Buffering is nginx's per-response off switch; no-cache keeps any
# cache out of it.
_STREAM_HEADERS = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}


@router.get("/{doc_id}/cached-summary")
async def get_cached_explore_summary(doc_id: int, db: Session = Depends(get_db)):
    """A summary already made, served by the API (and nginx's cache) — a
    read, so it never waits on the pipeline process that makes them: 200
    with it, 204 when none has been made (the page then asks
    `POST .../summary`, which the pipeline process streams), 404 for no such
    document."""
    from fastapi import Response

    from app.services import explore_summary

    doc = await _load_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        _prompt, _key, cached = await explore_summary.lookup(doc_id, doc)
    except explore_summary.Refusal as refusal:
        # Unreadable, not absent: never kept (an error has no Cache-Control
        # nginx would store); the page goes on to ask the pipeline.
        raise refusal.error() from None
    if cached is None:
        # Not made yet — and may be made any moment: never kept.
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    # Kept briefly, and never served stale while refreshing (no
    # stale-while-revalidate): this URL names the document, not the text
    # summarised, so a document changed in place (a body backfilled, a data
    # reset reusing its id) must stop being answered with the old text's
    # summary soon.
    return JSONResponse(content={"done": True, **cached}, headers={"Cache-Control": "public, max-age=30"})


@router.post("/{doc_id}/summary")
async def get_explore_document_summary(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Stream an AI summary of a government document as it generates.

    Served by the pipeline process (nginx routes it there): the generation
    is background work that outlives its request, and every reader of a
    text shares one (services/explore_summary.py).

    Streams Server-Sent Events, each `data:` line a JSON object:
    {"delta": "<text chunk>"} while generating, then a final
    {"done": true, "summary": ..., "keyPoints": [...], "impact": ...}
    once the full text is parsed (also what a cache hit returns
    immediately, as a single event, with no intermediate deltas).
    """
    from app.background import writers_allowed
    from app.services import explore_summary

    if not writers_allowed():
        # nginx sends this route to the pipeline service; one that reached
        # the read-only API anyway is refused rather than generated here —
        # as a wait, like the pipeline being down, not a failure.
        raise HTTPException(
            status_code=503,
            detail="Summaries are served by the pipeline service; please try again shortly.",
            headers={"Retry-After": str(explore_summary.BUSY_RETRY_AFTER_S), **explore_summary.WAIT_OUT},
        )

    doc = await _load_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        prompt, key, made = await explore_summary.lookup(doc_id, doc)
    except explore_summary.Refusal as refusal:
        raise refusal.error() from None
    if made is not None:  # a summary already made is never limited
        return StreamingResponse(explore_summary.once({"done": True, **made}), media_type="text/event-stream",
                                 headers=_STREAM_HEADERS)

    ip = client_ip(request)

    async def limit():
        # A refused request isn't counted, so it is a wait too.
        counted = await throttle.run(limit_client, ip, _SUMMARY_REQUESTS_BUCKET,
                                     limit=_SUMMARY_REQUESTS_PER_MINUTE, period=60.0)
        if not counted.allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many summary requests; please try again shortly.",
                headers={"Retry-After": retry_after(counted.reset_at), **explore_summary.WAIT_OUT},
            )

    try:
        stream = await explore_summary.request(doc_id, prompt, key, ip, limit=limit)
    except explore_summary.Refusal as refusal:
        raise refusal.error() from None
    return StreamingResponse(stream, media_type="text/event-stream", headers=_STREAM_HEADERS)


@router.post("/pipeline/trigger")
async def trigger_explore_pipeline(authorization: str | None = Header(default=None)):
    """Trigger the explore document ingestion pipeline."""
    check_pipeline_token(authorization)

    from app.api.pipeline_runner import run_pipeline_in_thread

    run_pipeline_in_thread(_run_explore_pipeline, name="explore-pipeline", error_label="Explore pipeline run failed")
    return {"status": "started"}


async def _run_explore_pipeline():
    from app.pipeline import lease
    from app.pipeline.explore_pipeline import run_explore_pipeline
    try:
        async with lease.job_async(lease.EXPLORE) as held:
            if not held:
                return
            result = await run_explore_pipeline(days_back=60)
        logger.info("Explore pipeline result: %s", result)
    except Exception as e:
        logger.error("Explore pipeline background task failed: %s", e)
