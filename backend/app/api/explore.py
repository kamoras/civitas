"""Explore API — semantic search over government activity documents."""

import asyncio
import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.public import RateLimit
from app.api.rate_limit import WriteRateLimit
from app.config import settings
from app.database import get_db
from app.models import ExploreDocument
from app.pipeline.vector_store import search_explore_documents

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/explore")

# Canonical chamber metadata values as stored in ChromaDB (explore_pipeline
# writes these exact strings). ChromaDB `where` equality is case-sensitive,
# so a lowercase "senate" filter matched nothing — user input is mapped
# through this before querying. None of the four non-legislative chambers
# was reachable at all before this map existed.
_CHAMBER_CANONICAL = {
    "senate": "Senate", "house": "House", "executive": "Executive",
    "judicial": "Judicial", "regulatory": "Regulatory",
}

# Real doc_type values in the index (explore_pipeline). Used to validate the
# public API's doc_type filter — an unknown value is an exact-match miss
# that silently returns zero results, so it's rejected with 422 instead.
VALID_DOC_TYPES = {
    "Senate Floor Speech", "House Floor Speech", "Executive Order",
    "Proclamation", "Presidential Memorandum", "Supreme Court Opinion",
    "Final Rule", "Proposed Rule", "Notice",
}


@router.get("")
async def search_explore(
    _rl: RateLimit,
    q: str = Query(..., min_length=2, max_length=200, description="Search query"),
    doc_type: str | None = Query(None, description="Filter by document type"),
    chamber: str | None = Query(None, description="Filter by chamber"),
    commentable: bool = Query(False, description="Only show documents open for comment"),
    sort: str = Query("relevance", description="Sort order: relevance or date"),
    limit: int = Query(20, ge=1, le=50),
    politician_id: str | None = Query(None, description="Filter by politician ID (exact match)"),
    db: Session = Depends(get_db),
):
    """Semantic search over government activity documents.

    Returns matching documents ranked by relevance (default) or date.
    """
    # Normalize the chamber filter to the canonical stored casing so a
    # lowercase "senate" actually matches (ChromaDB equality is
    # case-sensitive). "commentable" only ever applies to Regulatory docs,
    # so scope the vector query to that chamber rather than 3x-oversampling
    # and hoping enough regulatory hits survive the post-filter.
    effective_chamber = chamber
    if commentable and not chamber:
        effective_chamber = "Regulatory"
    canonical_chamber = (
        _CHAMBER_CANONICAL.get(effective_chamber.lower(), effective_chamber)
        if effective_chamber else None
    )

    results = await asyncio.to_thread(
        search_explore_documents,
        query=q,
        n_results=limit,
        doc_type=doc_type,
        chamber=canonical_chamber,
        politician_id=politician_id,
    )

    # None (not []) means the index doesn't exist yet — e.g. after an admin
    # reset, before the next pipeline run. Surface that distinctly so the
    # UI can say "still indexing" instead of the misleading "no matches"
    # (the SQL-based stats header may simultaneously report thousands of
    # documents, which reset only the vector store).
    if results is None:
        return JSONResponse(
            status_code=503,
            content={"query": q, "results": [], "count": 0, "indexEmpty": True},
            headers={"Cache-Control": "no-store"},
        )

    doc_ids = [r["id"] for r in results if r.get("id")]
    doc_map: dict = {}
    if doc_ids:
        docs = (
            db.query(
                ExploreDocument.id,
                ExploreDocument.url,
                ExploreDocument.summary,
                ExploreDocument.agency_name,
                ExploreDocument.comment_url,
                ExploreDocument.comments_close_on,
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
                result["commentUrl"] = doc.comment_url or ""
                result["commentsCloseOn"] = doc.comments_close_on or ""

    # Drop vector hits with no surviving DB row — after a partial reset
    # (DB cleared, Chroma reset swallowed) these render as snippet-only
    # cards whose "view details" link 404s.
    results = [r for r in results if r.get("id") in doc_map]

    if commentable:
        from datetime import date as date_type
        today_str = date_type.today().isoformat()
        results = [
            r for r in results
            if r.get("commentUrl") and r.get("commentsCloseOn", "") >= today_str
        ]

    if sort == "date":
        results.sort(key=lambda r: r.get("date", ""), reverse=True)

    return JSONResponse(
        content={"query": q, "results": results, "count": len(results)},
        headers={"Cache-Control": "public, max-age=60, stale-while-revalidate=60"},
    )


@router.get("/stats")
async def explore_stats(db: Session = Depends(get_db)):
    """Return counts of explore documents by type and chamber."""
    total = db.query(ExploreDocument).count()

    type_counts: dict[str, int] = {}
    chamber_counts: dict[str, int] = {}

    if total > 0:
        from sqlalchemy import func
        type_rows = (
            db.query(ExploreDocument.doc_type, func.count())
            .group_by(ExploreDocument.doc_type)
            .all()
        )
        for doc_type, count in type_rows:
            type_counts[doc_type] = count

        chamber_rows = (
            db.query(ExploreDocument.chamber, func.count())
            .group_by(ExploreDocument.chamber)
            .all()
        )
        for chamber, count in chamber_rows:
            if chamber:
                chamber_counts[chamber] = count

    open_for_comment = 0
    if total > 0:
        from datetime import date as date_type
        today_str = date_type.today().isoformat()
        open_for_comment = (
            db.query(ExploreDocument)
            .filter(
                ExploreDocument.comment_url.isnot(None),
                ExploreDocument.comment_url != "",
                ExploreDocument.comments_close_on >= today_str,
            )
            .count()
        )

    return JSONResponse(
        content={
            "totalDocuments": total,
            "byType": type_counts,
            "byChamber": chamber_counts,
            "openForComment": open_for_comment,
        },
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=300"},
    )


@router.get("/{doc_id}")
async def get_explore_document(doc_id: int, db: Session = Depends(get_db)):
    """Return full details for a single explore document."""
    doc = db.query(ExploreDocument).filter(ExploreDocument.id == doc_id).first()
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
    doc_id: int,
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(25, ge=1, le=25),
    db: Session = Depends(get_db),
):
    """Fetch public comments for a regulatory document from regulations.gov."""
    doc = db.query(ExploreDocument).filter(ExploreDocument.id == doc_id).first()
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
    )
    return JSONResponse(content=result)


@router.post("/{doc_id}/comments")
async def post_document_comment(
    doc_id: int,
    _rl: WriteRateLimit,
    db: Session = Depends(get_db),
    comment: str = Query(..., min_length=10, max_length=5000, description="Comment text"),
    name: str = Query("Anonymous", max_length=100, description="Your name"),
    organization: str = Query("", max_length=200, description="Organization (optional)"),
    dry_run: bool = Query(False, description="Validate without submitting"),
):
    """Submit a public comment on a regulatory document via regulations.gov.

    Pass dry_run=true to validate everything without actually submitting.
    """
    doc = db.query(ExploreDocument).filter(ExploreDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.comment_url:
        raise HTTPException(status_code=400, detail="This document does not accept public comments")

    if doc.comments_close_on:
        from datetime import date as date_type
        if doc.comments_close_on < date_type.today().isoformat():
            raise HTTPException(status_code=400, detail="The comment period for this document has closed")

    from app.pipeline.fetch.regulations_gov import submit_comment, _extract_document_object_id

    if dry_run:
        reg_doc_id = _extract_document_object_id(doc.comment_url)
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


_summary_timestamps: dict[int, float] = {}
_SUMMARY_COOLDOWN = 30.0

@router.post("/{doc_id}/summary")
async def get_explore_document_summary(
    doc_id: int,
    _rl: WriteRateLimit,
    db: Session = Depends(get_db),
):
    """Generate an AI summary of a government document.

    The per-doc cooldown below only stops repeated requests for the SAME
    document — it doesn't stop a caller from fanning out across many
    doc_ids to trigger unlimited LLM inference (2026-07 audit). The
    per-IP WriteRateLimit dependency closes that gap.
    """
    import time
    now = time.monotonic()
    last = _summary_timestamps.get(doc_id, 0)
    if now - last < _SUMMARY_COOLDOWN:
        raise HTTPException(status_code=429, detail="Please wait before requesting another summary")
    _summary_timestamps[doc_id] = now

    if len(_summary_timestamps) > 500:
        cutoff = now - _SUMMARY_COOLDOWN * 2
        for k in [k for k, ts in _summary_timestamps.items() if ts < cutoff]:
            del _summary_timestamps[k]

    from app.pipeline.analyze.ollama_client import call_llm
    from app.pipeline.analyze.prompts import explore_document_summary_prompt

    doc = db.query(ExploreDocument).filter(ExploreDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_dict = {
        "title": doc.title,
        "body": doc.body,
        "doc_type": doc.doc_type,
        "chamber": doc.chamber or "",
        "politician_name": doc.politician_name or "",
        "date": doc.date,
    }
    prompt = explore_document_summary_prompt(doc_dict)

    import asyncio
    result = await asyncio.to_thread(
        call_llm,
        prompt_version=prompt["promptVersion"],
        system_prompt=prompt["systemPrompt"],
        user_prompt=prompt["userPrompt"],
        cache_key={"doc_id": doc_id, "v": 3},
        # call_llm caches only when BOTH cache_key and db_session are set —
        # omitting db_session silently disabled the versioned cache, so
        # every view past the 30s cooldown re-ran a full ~512-token
        # generation and serialized on the single LLM backend.
        db_session=db,
        max_tokens=512,
    )

    if not result or not isinstance(result, dict):
        return {
            "summary": "",
            "keyPoints": [],
            "impact": "",
        }

    return {
        "summary": result.get("summary", ""),
        "keyPoints": result.get("keyPoints", result.get("key_points", [])),
        "impact": result.get("impact", ""),
    }


@router.post("/pipeline/trigger")
async def trigger_explore_pipeline(
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Trigger the explore document ingestion pipeline."""
    if not settings.PIPELINE_TRIGGER_TOKEN:
        raise HTTPException(status_code=503, detail="Pipeline trigger token not configured")
    expected = f"Bearer {settings.PIPELINE_TRIGGER_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=403, detail="Invalid token")

    background_tasks.add_task(_run_explore_pipeline)
    return {"status": "started"}


async def _run_explore_pipeline():
    from app.pipeline.explore_pipeline import run_explore_pipeline
    try:
        result = await run_explore_pipeline(days_back=60)
        logger.info("Explore pipeline result: %s", result)
    except Exception as e:
        logger.error("Explore pipeline background task failed: %s", e)
