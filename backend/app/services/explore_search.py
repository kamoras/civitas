"""Hybrid ranking for the Explore federal-document search.

Until now this was one signal: cosine similarity between the query
embedding and one 384-dimensional vector per document. That is a good
signal and a lonely one. General web search has never been a single
similarity score — it is several independent retrievers combined with
query-independent evidence about which documents matter, and this module
is that arrangement scaled to a corpus of federal documents on a Pi.

Four rankers, fused:

  semantic   sentence-transformer kNN over `vec_explore` (unchanged; this
             is what the feature already did). Strong on paraphrase and
             topical queries, weak on rare terms and identifiers.
  keyword    BM25F over the FTS5 index (`pipeline/lexical_index.py`).
             Exactly the inverse: it is the only channel that can find
             "Executive Order 14110" or a docket number.
  freshness  document date. A five-year-old proposed rule and this
             morning's final rule are not equally useful answers to the
             same question, and nothing in a cosine distance knows that.
  authority  PageRank over the citation graph between these documents
             (`pipeline/analyze/document_authority.py`) — the link-analysis
             idea, applied to the one link structure federal documents
             actually have.

Fusion is weighted reciprocal rank fusion; the reasoning for using rank
fusion rather than score blending, and all four weights, are in
`config_definitions` under "Explore search ranking".

Two things happen after fusion that are ranking decisions in their own
right. Near-duplicate documents are collapsed to their best-ranked
representative — this corpus is known to accumulate byte-identical rows
(a 2026-07 audit found 1,758, 31% of the table, from a hash-seed bug),
and even with that fixed the Congressional Record legitimately reprints
text. And no single member or agency may occupy more than
`EXPLORE_SOURCE_DIVERSITY_CAP` of the first results before the remainder
are demoted below other sources; they are moved, never dropped, so a
member-scoped search still returns everything it found.

Read-path cost: the keyword channel, both priors, deduplication and
diversity are all SQL and arithmetic. The one heavy step is encoding the
query, which this path already did before any of this existed.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date as date_type

from sqlalchemy import text

from app.config_definitions import (
    EXPLORE_CANDIDATE_POOL,
    EXPLORE_FUSION_WEIGHTS,
    EXPLORE_MAX_CANDIDATE_POOL,
    EXPLORE_RRF_K,
    EXPLORE_SOURCE_DIVERSITY_CAP,
)
from app.pipeline.lexical_index import search_lexical
from app.pipeline.vector_store import search_explore_documents

logger = logging.getLogger(__name__)

# Documents whose normalised text is shorter than this are never treated as
# duplicates of each other. Without the floor, every body-less row in the
# corpus fingerprints to the same empty string and the whole set collapses
# into a single result — which is what a naive content hash does to a
# corpus where "body not backfilled yet" is a normal state.
MIN_FINGERPRINT_CHARS = 80

_NON_WORD_RE = re.compile(r"[^0-9a-z]+")


def _fingerprint(doc_id: int, title: str, body: str) -> str:
    """Content fingerprint used to collapse near-duplicates.

    Normalises away the formatting differences that make byte-comparison
    useless (case, punctuation, whitespace runs) and hashes the opening of
    the document, where re-ingested duplicates are identical and genuinely
    distinct documents diverge. Falls back to the document's own id — a
    fingerprint that can collide with nothing — when there isn't enough
    text to judge.
    """
    normalized = _NON_WORD_RE.sub(" ", f"{title} {body}".lower()).strip()
    if len(normalized) < MIN_FINGERPRINT_CHARS:
        return f"id:{doc_id}"
    return hashlib.sha1(normalized[:400].encode()).hexdigest()


def _competition_ranks(ordered: list[tuple[int, object]]) -> dict[int, int]:
    """Standard competition ranking (1, 2, 2, 4) over a sorted list.

    Ties must share a rank or the priors invent an ordering they have no
    evidence for: with a sparse citation graph most documents have the
    identical authority score, and ranking them 1..n by whatever order the
    sort happened to produce would turn "no signal" into a strong and
    arbitrary one.
    """
    ranks: dict[int, int] = {}
    previous_key: object = object()
    previous_rank = 0
    for position, (doc_id, key) in enumerate(ordered, start=1):
        if key != previous_key:
            previous_rank = position
            previous_key = key
        ranks[doc_id] = previous_rank
    return ranks


def _rrf(rank: int | None, weight: float) -> float:
    """One ranker's contribution. A ranker that didn't rank this document
    contributes nothing — the property that lets authority sit in the sum
    without penalising documents no one can cite."""
    if rank is None or weight == 0:
        return 0.0
    return weight / (EXPLORE_RRF_K + rank)


# Bound on bind parameters per hydration statement. Both channels can each
# return a full pool, so the union is up to 2 × EXPLORE_MAX_CANDIDATE_POOL —
# past SQLite's historical 999-variable ceiling (raised to 32766 in 3.32,
# but that is a compile-time limit and not something a read path should
# quietly depend on).
_HYDRATE_CHUNK = 400


def _hydrate(db, doc_ids: list[int]) -> dict[int, dict]:
    """One query per chunk for every column the ranker and the response need.

    `substr(body, 1, 400)` rather than `body`: the fingerprint only reads
    the opening, and selecting whole bodies for a 600-document candidate
    pool would pull megabytes of rule text through the read path to throw
    almost all of it away.
    """
    if not doc_ids:
        return {}

    rows = []
    for start in range(0, len(doc_ids), _HYDRATE_CHUNK):
        chunk = doc_ids[start:start + _HYDRATE_CHUNK]
        placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
        params = {f"id{i}": doc_id for i, doc_id in enumerate(chunk)}
        rows.extend(db.execute(text(
            f"""SELECT id, title, date, doc_type, source, politician_name,
                       politician_id, chamber, agency_name, url, summary,
                       comment_url, comments_close_on, cited_by_count, authority,
                       substr(coalesce(body, ''), 1, 400) AS body_head
                FROM explore_documents WHERE id IN ({placeholders})"""
        ), params).fetchall())

    return {
        int(row.id): {
            "id": int(row.id),
            "title": row.title or "",
            "date": row.date or "",
            "docType": row.doc_type or "",
            "source": row.source or "",
            "politicianName": row.politician_name or "",
            "politicianId": row.politician_id or "",
            "chamber": row.chamber or "",
            "agencyName": row.agency_name or "",
            "url": row.url or "",
            "summary": row.summary or "",
            "commentUrl": row.comment_url or "",
            "commentsCloseOn": row.comments_close_on or "",
            "citedByCount": int(row.cited_by_count or 0),
            # Raw PageRank. Internal: the ranker orders by it, but a
            # stationary probability means nothing to a reader, so the
            # response exposes the citation count instead.
            "_authority": float(row.authority or 0.0),
            "_bodyHead": row.body_head or "",
        }
        for row in rows
    }


def _diversity_key(doc: dict) -> str | None:
    """What counts as "the same source" for crowding control.

    A member for floor speeches and opinions, an agency for rulemaking.
    Documents with neither — most presidential actions — are left
    uncapped rather than lumped together under their `source`, which
    would treat the entire Congressional Record as one crowded site.
    """
    return doc.get("politicianId") or doc.get("agencyName") or None


def _apply_diversity(ranked: list[dict], cap: int) -> list[dict]:
    """Demote (never drop) results past `cap` from one member or agency."""
    if cap <= 0:
        return ranked
    seen: dict[str, int] = {}
    kept: list[dict] = []
    demoted: list[dict] = []
    for doc in ranked:
        key = _diversity_key(doc)
        if key is None:
            kept.append(doc)
            continue
        seen[key] = seen.get(key, 0) + 1
        (kept if seen[key] <= cap else demoted).append(doc)
    return kept + demoted


def hybrid_search(
    db,
    query: str,
    *,
    limit: int = 20,
    doc_type: str | None = None,
    chamber: str | None = None,
    politician_id: str | None = None,
    commentable: bool = False,
    sort: str = "relevance",
) -> dict:
    """Run both retrieval channels, fuse, rank, and return a page.

    Returns `{"results": [...], "count": n, "indexReady": bool,
    "semanticUnavailable": bool, "channels": {...}}`.

    `indexReady` is False only when *neither* channel could answer — the
    semantic index is mid-rebuild and the keyword index is absent or
    matched nothing. That is a real improvement on its own: a reindex used
    to take the whole feature down for the minutes it ran, and now takes
    only the semantic half of it down.

    `semanticUnavailable` says which half. It is not `channels.semantic ==
    0`: a filtered query can legitimately retrieve zero vectors while the
    index is perfectly healthy, and conflating the two would tell readers
    the engine was rebuilding whenever a doc_type filter came up empty.
    """
    pool = min(max(limit * 8, EXPLORE_CANDIDATE_POOL), EXPLORE_MAX_CANDIDATE_POOL)
    today = date_type.today().isoformat()
    commentable_after = today if commentable else None

    # `commentable` documents are Federal Register rulemaking and nothing
    # else, so scoping the semantic channel to that chamber asks the index
    # a question it can answer precisely, instead of retrieving a general
    # pool and hoping enough regulatory documents survive the filter.
    effective_chamber = chamber or ("Regulatory" if commentable else None)

    try:
        semantic = search_explore_documents(
            query=query, n_results=pool, doc_type=doc_type,
            chamber=effective_chamber, politician_id=politician_id,
        )
    except Exception:
        logger.exception("Semantic channel failed for %r", query)
        semantic = None

    keyword = search_lexical(
        db, query, limit=pool, doc_type=doc_type, chamber=effective_chamber,
        politician_id=politician_id, commentable_after=commentable_after,
    )

    # None (not []) from the semantic channel means the vector index is
    # missing or mid-rebuild, which is different from "it found nothing".
    # The distinction has to survive to the response: a page served on the
    # keyword channel alone is a partial answer, and telling the reader so
    # is the difference between honest degradation and quiet degradation.
    semantic_unavailable = semantic is None

    if semantic_unavailable and not keyword:
        return {
            "results": [], "count": 0, "indexReady": False,
            "semanticUnavailable": True,
            "channels": {"semantic": 0, "keyword": 0},
        }

    semantic = semantic or []

    candidate_ids: list[int] = []
    seen_ids: set[int] = set()
    for hit in semantic:
        if hit["id"] not in seen_ids:
            seen_ids.add(hit["id"])
            candidate_ids.append(hit["id"])
    for hit in keyword:
        if hit["id"] not in seen_ids:
            seen_ids.add(hit["id"])
            candidate_ids.append(hit["id"])

    hydrated = _hydrate(db, candidate_ids)

    # Vector hits whose row is gone (a partial reset clears the app DB but
    # not vectors.db) would otherwise render as snippet-only cards whose
    # detail link 404s.
    candidates = [hydrated[i] for i in candidate_ids if i in hydrated]

    if commentable:
        candidates = [
            doc for doc in candidates
            if doc["commentUrl"] and doc["commentsCloseOn"] >= today
        ]
    if not candidates:
        return {
            "results": [], "count": 0, "indexReady": True,
            "semanticUnavailable": semantic_unavailable,
            "channels": {"semantic": len(semantic), "keyword": len(keyword)},
        }

    live_ids = {doc["id"] for doc in candidates}
    semantic_rank = {
        hit["id"]: position
        for position, hit in enumerate(
            (h for h in semantic if h["id"] in live_ids), start=1)
    }
    semantic_distance = {hit["id"]: hit.get("distance") for hit in semantic}
    keyword_rank = {
        hit["id"]: position
        for position, hit in enumerate(
            (h for h in keyword if h["id"] in live_ids), start=1)
    }
    keyword_snippet = {hit["id"]: hit.get("snippet", "") for hit in keyword}

    freshness_rank = _competition_ranks(sorted(
        [(doc["id"], doc["date"]) for doc in candidates],
        key=lambda pair: pair[1], reverse=True,
    ))
    # `cited_by_count > 0` is the eligibility gate; the *ordering* is the
    # PageRank score, which is the whole reason for computing one. Ranking
    # by the raw count instead would say a document cited five times by
    # routine notices outranks one cited three times by the orders everything
    # else in the corpus points at — exactly the distinction link analysis
    # exists to draw.
    cited = [doc for doc in candidates if doc["citedByCount"] > 0]
    authority_rank = _competition_ranks(sorted(
        [(doc["id"], doc["_authority"]) for doc in cited],
        key=lambda pair: pair[1], reverse=True,
    ))

    for doc in candidates:
        doc_id = doc["id"]
        doc["_score"] = (
            _rrf(semantic_rank.get(doc_id), EXPLORE_FUSION_WEIGHTS["semantic"])
            + _rrf(keyword_rank.get(doc_id), EXPLORE_FUSION_WEIGHTS["keyword"])
            + _rrf(freshness_rank.get(doc_id), EXPLORE_FUSION_WEIGHTS["freshness"])
            + _rrf(authority_rank.get(doc_id), EXPLORE_FUSION_WEIGHTS["authority"])
        )
        matched: list[str] = []
        if doc_id in semantic_rank:
            matched.append("semantic")
        if doc_id in keyword_rank:
            matched.append("keyword")
        doc["matchedBy"] = matched
        doc["distance"] = semantic_distance.get(doc_id)
        # The keyword channel's snippet shows the query terms in context and
        # marks them; the semantic channel has no matched terms to point at,
        # so those results keep the document's own summary.
        doc["snippet"] = keyword_snippet.get(doc_id) or doc["summary"]

    candidates.sort(key=lambda doc: (-doc["_score"], doc["id"]))

    # Collapse near-duplicates *after* fusion so the survivor is the copy
    # the rankers liked best, not whichever row was inserted first.
    representatives: list[dict] = []
    by_fingerprint: dict[str, dict] = {}
    for doc in candidates:
        doc.pop("_authority", None)
        key = _fingerprint(doc["id"], doc["title"], doc.pop("_bodyHead", ""))
        existing = by_fingerprint.get(key)
        if existing is None:
            doc["duplicateCount"] = 0
            by_fingerprint[key] = doc
            representatives.append(doc)
        else:
            existing["duplicateCount"] += 1

    if sort == "date":
        # Sorted over the whole filtered candidate pool, not over the page.
        # The previous implementation sorted the twenty results it had
        # already chosen by relevance, so "newest" meant "newest of the
        # twenty most similar" — reliably not the newest matching document.
        representatives.sort(key=lambda doc: (doc["date"], doc["_score"]), reverse=True)
        results = representatives[:limit]
    else:
        results = _apply_diversity(representatives, EXPLORE_SOURCE_DIVERSITY_CAP)[:limit]

    for doc in results:
        doc.pop("_score", None)

    return {
        "results": results,
        "count": len(results),
        "indexReady": True,
        "semanticUnavailable": semantic_unavailable,
        "channels": {"semantic": len(semantic), "keyword": len(keyword)},
    }
