"""Explore API — semantic search over government activity documents."""

import asyncio
import json
import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import check_pipeline_token
from app.api.public import RateLimit
from app.api.rate_limit import UpstreamRouteLimit, WriteRateLimit, spend_upstream
from app.api.response_helpers import FAILURE_RETRY_S, retry_soon_json
from app.database import get_db, off_loop
from app.models import ExploreDocument
from app.services.explore_search import hybrid_search
from app.time_utils import comment_period_today

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/explore")

# Canonical chamber metadata values as written by explore_pipeline. The
# sqlite-vec metadata filter is an exact string comparison, so a lowercase
# "senate" matched nothing — user input is mapped through this before
# querying. None of the four non-legislative chambers was reachable at all
# before this map existed.
_CHAMBER_CANONICAL = {
    "senate": "Senate", "house": "House", "executive": "Executive",
    "judicial": "Judicial", "regulatory": "Regulatory",
}

# Real doc_type values in the index (explore_pipeline). An unknown value is
# an exact-match miss that returns zero results for a reason the caller
# can't see, so it is rejected with 422 instead.
VALID_DOC_TYPES = {
    "Senate Floor Speech", "House Floor Speech", "Executive Order",
    "Proclamation", "Presidential Memorandum", "Supreme Court Opinion",
    "Final Rule", "Proposed Rule", "Notice",
}

VALID_SORTS = {"relevance", "date"}


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
            f"public, max-age={FAILURE_RETRY_S}" if outcome["semanticUnavailable"]
            else "public, max-age=60, stale-while-revalidate=60"
        )},
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
        today_str = comment_period_today()
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
    _rl: UpstreamRouteLimit,
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

    doc = db.query(ExploreDocument).filter(ExploreDocument.id == doc_id).first()
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


# One generation per document at a time, and at most _MAX_GENERATIONS in
# all, across every API worker process (api/throttle.py claims: a
# per-process record let each worker start its own). A generation finishes
# whether or not its reader stays, so the total is capped: the device has
# one LLM, and a client starting and abandoning generations across
# documents must not queue up work it will never read.
#
# Each claim is held for the whole generation and given back, by its token,
# when it ends (_Generation) — never one another generation made after it
# lapsed. A generation is stopped at _SUMMARY_GENERATION_LIMIT_S, inside the
# claim period, which bounds how long a claim outlives a process that died
# mid-generation.
_SUMMARY_BUCKET = "explore-summary"
_SLOT_BUCKET = "explore-summary-slot"
# A document whose output couldn't be used, or whose generation ran out of
# time, is not generated again for a while: the same prompt at temperature 0
# comes out the same way, and takes as long. (A generation that failed —
# the LLM unreachable, say — may be tried again at once.)
_UNUSABLE_BUCKET = "explore-summary-unusable"
_UNUSABLE_FOR_S = 30 * 60.0
_MAX_GENERATIONS = 2
_SUMMARY_GENERATION_LIMIT_S = 240.0
_SUMMARY_CLAIM_S = 300.0
_BUSY_RETRY_AFTER_S = 30
_HELD_RETRY_AFTER_S = 10
# How often a stream waiting on the LLM sends an SSE comment: nginx drops a
# proxied response that sends nothing for proxy_read_timeout (120s), which
# a busy LLM's prompt processing can exceed before the first delta.
_KEEPALIVE_S = 15.0
_SUMMARY_CACHE_KEY_VERSION = 4  # bump alongside explore_document_summary_prompt's promptVersion
# Generations under way in this process, held so the event loop doesn't
# collect a task whose reader has gone, and so shutdown can stop them.
_generations: set[asyncio.Task] = set()


async def stop_generations() -> None:
    """Cancel the generations under way (lifespan shutdown): each gives
    its claim back as it stops, so the document isn't held off."""
    tasks = list(_generations)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# A stream is read as it is written: nginx buffers proxied responses by
# default (and the /api/ catch-all turns buffering on for its cache), which
# would hold every delta until the generation ended. X-Accel-Buffering is
# nginx's per-response off switch; no-cache keeps any cache out of it.
_STREAM_HEADERS = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/{doc_id}/summary")
async def get_explore_document_summary(
    doc_id: int,
    _rl: WriteRateLimit,
    db: Session = Depends(get_db),
):
    """Stream an AI summary of a government document as it generates.

    One generation per document at a time and a few in all (_Generation's
    claims); the per-IP WriteRateLimit dependency stops a caller fanning
    out across many doc_ids (2026-07 audit).

    Streams Server-Sent Events, each `data:` line a JSON object:
    {"delta": "<text chunk>"} while generating, then a final
    {"done": true, "summary": ..., "keyPoints": [...], "impact": ...}
    once the full text is parsed (also what a cache hit returns
    immediately, as a single event, with no intermediate deltas).
    """
    from app.api import throttle
    from app.pipeline.analyze.ollama_client import get_cached_llm_result, set_cached_llm_result, stream_llm
    from app.pipeline.analyze.prompts import explore_document_summary_prompt, parse_explore_document_summary

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
    cache_key = {"doc_id": doc_id, "v": _SUMMARY_CACHE_KEY_VERSION}

    # A summary already made costs nothing to hand out: no cooldown.
    cached = await asyncio.to_thread(get_cached_llm_result, prompt["promptVersion"], cache_key)
    if cached is not None:
        async def cached_stream():
            yield _sse({"done": True, **cached})

        return StreamingResponse(cached_stream(), media_type="text/event-stream", headers=_STREAM_HEADERS)

    # Claimed, checked and generated in a task of its own, which the
    # request only waits on: a request cancelled mid-claim (a disconnect)
    # can't leave a claim behind that nothing will give back.
    generation = _Generation(doc_id, prompt, cache_key, get_cached_llm_result, set_cached_llm_result, stream_llm,
                             parse_explore_document_summary)
    task = asyncio.create_task(generation.run())
    _generations.add(task)
    task.add_done_callback(_generations.discard)
    outcome = await asyncio.shield(generation.outcome)

    if outcome == "unavailable":
        raise HTTPException(
            status_code=503,
            detail="Summaries are unavailable right now; please try again shortly.",
            headers={"Retry-After": str(throttle.UNAVAILABLE_RETRY_AFTER_S)},
        )
    if outcome == "held":
        # Another reader's generation of this document: cached when it
        # ends, so a retry soon is usually served straight from the cache.
        raise HTTPException(
            status_code=429,
            detail="This summary is being written; please try again shortly.",
            headers={"Retry-After": str(_HELD_RETRY_AFTER_S)},
        )
    if outcome == "busy":
        raise HTTPException(
            status_code=503,
            detail="Summaries are busy right now; please try again shortly.",
            headers={"Retry-After": str(_BUSY_RETRY_AFTER_S)},
        )
    if outcome == "unusable":
        # Nothing is being written and nothing will come of retrying soon:
        # the answer, not a refusal to wait out.
        async def nothing_usable():
            yield _sse({"done": True, "summary": "", "keyPoints": [], "impact": ""})

        return StreamingResponse(nothing_usable(), media_type="text/event-stream", headers=_STREAM_HEADERS)
    if isinstance(outcome, dict):  # made by another request while this one waited
        async def made_meanwhile():
            yield _sse({"done": True, **outcome})

        return StreamingResponse(made_meanwhile(), media_type="text/event-stream", headers=_STREAM_HEADERS)

    async def event_stream():
        while True:
            try:
                event = await asyncio.wait_for(generation.events.get(), _KEEPALIVE_S)
            except TimeoutError:
                yield ": waiting\n\n"
                continue
            if event is None:
                return
            yield event

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_STREAM_HEADERS)


class _Generation:
    """One summary generation: its claims, the generation, and the claims
    given back. `outcome` settles once the claims are decided — "go" (the
    events follow on `events`, None last), "held" (another generation of
    the document is under way), "busy" (the cap is reached), "unusable"
    (its last output couldn't be used, recently), "unavailable"
    (the claim store can't answer: fails closed, since the claims are what
    stand between repeated POSTs and the device's one LLM), or the summary
    itself when another request made it meanwhile."""

    def __init__(self, doc_id, prompt, cache_key, get_cached, set_cached, stream, parse):
        self.doc_id = doc_id
        self.prompt = prompt
        self.cache_key = cache_key
        self._get_cached = get_cached
        self._set_cached = set_cached
        self._stream = stream
        self._parse = parse
        self.outcome: asyncio.Future = asyncio.get_running_loop().create_future()
        self.events: asyncio.Queue[str | None] = asyncio.Queue()
        # (bucket, key, token) for each claim held.
        self._held: list[tuple[str, str, float]] = []

    def _settle(self, outcome) -> None:
        if not self.outcome.done():
            self.outcome.set_result(outcome)

    async def _claim(self, bucket: str, keys: list[str]) -> bool:
        """The first free one of `keys` (Unavailable when the store can't
        answer)."""
        from app.api import throttle

        held = await throttle.run(throttle.hold, bucket, keys, period=_SUMMARY_CLAIM_S)
        if held is None:
            return False
        self._held.append((bucket, *held))
        return True

    async def _give_back(self) -> None:
        from app.api import throttle

        for bucket, key, token in self._held:
            try:
                await throttle.run(throttle.release, bucket, key, token=token)
            except Exception:
                logger.warning("Explore summary claim %s/%s not given back", bucket, key, exc_info=True)
        self._held.clear()

    async def run(self) -> None:
        from app.api import throttle

        try:
            try:
                if await throttle.run(throttle.held, _UNUSABLE_BUCKET, str(self.doc_id), period=_UNUSABLE_FOR_S):
                    self._settle("unusable")
                    return
                if not await self._claim(_SUMMARY_BUCKET, [str(self.doc_id)]):
                    self._settle("held")
                    return
                if not await self._claim(_SLOT_BUCKET, [str(slot) for slot in range(_MAX_GENERATIONS)]):
                    self._settle("busy")
                    return
            except throttle.Unavailable:
                self._settle("unavailable")
                return
            # Checked again now the claim is won: a generation that just
            # finished elsewhere cached it and gave the claim back.
            made = await asyncio.to_thread(self._get_cached, self.prompt["promptVersion"], self.cache_key)
            if made is not None:
                self._settle(made)
                return
            self._settle("go")
            await self._generate()
        finally:
            self._settle("unavailable")  # a no-op once settled
            await self._give_back()  # a no-op once given back
            self.events.put_nowait(None)

    async def _generate(self) -> None:
        """Stream the generation onto `events`; the last one is sent only
        once the summary is cached and the claims are given back, so a
        reader asking again the moment it arrives is served or may start
        one."""
        from app.api import throttle
        from app.pipeline.analyze.ollama_client import StreamCutOff

        text = ""
        finished = at_limit = timed_out = False
        try:
            async with asyncio.timeout(_SUMMARY_GENERATION_LIMIT_S):
                async for delta in self._stream(
                    system_prompt=self.prompt["systemPrompt"],
                    user_prompt=self.prompt["userPrompt"],
                    max_tokens=512,
                ):
                    text += delta
                    self.events.put_nowait(_sse({"delta": delta}))
            finished = True
        except StreamCutOff:
            # At the token limit: as far as it will ever get (the same
            # prompt stops at the same place).
            at_limit = True
        except TimeoutError:
            logger.warning("Explore doc summary for doc_id=%s ran out of time", self.doc_id)
            timed_out = True
        except Exception:
            logger.exception("Explore doc summary streaming failed for doc_id=%s", self.doc_id)

        # Anything but a natural end stopped mid-sentence: the section it was
        # writing is dropped, for its reader as for the cache.
        parsed = self._parse(text, cut_off=not finished) if text else {"summary": "", "keyPoints": [], "impact": ""}
        # A generation that ended, naturally or at its limit, is the
        # document's summary. One that failed or ran out of time is shown to
        # its reader but not kept; one that ran out of time (or made nothing
        # usable) holds the document off for a while (_UNUSABLE_BUCKET).
        if (finished or at_limit) and parsed["summary"]:
            await asyncio.to_thread(self._set_cached, self.prompt["promptVersion"], self.cache_key, parsed)
        elif finished or at_limit or timed_out:
            try:
                await throttle.run(throttle.hold, _UNUSABLE_BUCKET, [str(self.doc_id)], period=_UNUSABLE_FOR_S)
            except Exception:
                logger.warning("Explore summary for doc_id=%s not marked unusable", self.doc_id, exc_info=True)
        await self._give_back()
        self.events.put_nowait(_sse({"done": True, **parsed}))


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
