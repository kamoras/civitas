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
right. Duplicate documents — identical text — are collapsed to their best-ranked
representative — this corpus is known to accumulate byte-identical rows
(a 2026-07 audit found 1,758, 31% of the table, from a hash-seed bug),
and even with that fixed the Congressional Record legitimately reprints
text (a recurring notice that only opens the same way is not a copy; see
_collapse_duplicates). And no single member or agency may occupy more than
a measured cap of the first results before the remainder
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

from sqlalchemy import text

from app import comment_periods
from app.models import ExploreDocument
from app.pipeline.analyze.document_authority import (
    declared_identifiers,
    extract_citations,
    is_identifier_query,
    query_record_identifiers,
)
from app.pipeline.explore_ranking import (
    candidate_pool,
    fingerprint_shape,
    fusion_weights,
    rrf_k,
    source_diversity_cap,
)
from app.pipeline.lexical_index import search_lexical
from app.pipeline.vector_store import search_explore_documents
from app.services.explore_coverage import not_held
from app.services.explore_keys import public_key
from app.time_utils import comment_period_today

logger = logging.getLogger(__name__)

_NON_WORD_RE = re.compile(r"[^0-9a-z]+")

# A query that is nothing but a Federal Register document number
# ("2026-15603"), the form the FR prints at the head of every document.
# Bare, it carries no "FR Doc." stamp for the citation parser to key on.
_BARE_FR_DOC_RE = re.compile(r"\s*(\d{4}-\d{4,6})\s*")


def _fingerprint(doc_id: int, title: str, body: str) -> str:
    """Content fingerprint used to collapse near-duplicates.

    Normalises away the formatting differences that make byte-comparison
    useless (case, punctuation, whitespace runs) and hashes the opening of
    the document, where re-ingested duplicates are identical and genuinely
    distinct documents diverge. Falls back to the document's own id — a
    fingerprint that can collide with nothing — when there isn't enough
    text to judge, which is a normal state here ("body not backfilled
    yet"), and which a naive content hash collapses into a single result.

    Both lengths are read off the corpus's own prefix-collision curve by
    scripts/calibrate_explore_ranking.py: the prefix at which genuinely
    distinct documents stop colliding, and the length below which no prefix
    separates them at all.
    """
    prefix_chars, min_chars = fingerprint_shape()
    normalized = _NON_WORD_RE.sub(" ", f"{title} {body}".lower()).strip()
    if len(normalized) < min_chars:
        return f"id:{doc_id}"
    return hashlib.sha1(normalized[:prefix_chars].encode()).hexdigest()


def _collapse_duplicates(db, ranked: list[dict]) -> list[dict]:
    """The best-ranked copy of each document text, with how many copies it
    stands for (`duplicateCount`); `ranked` is best first.

    Only an identical text is a copy. The opening fingerprint alone is not
    enough: a recurring Federal Register notice — the monthly antidumping
    review notice, the weekly bank-holding-company list, each OFAC sanctions
    action — opens with the same title and boilerplate every time, and on
    the 2026-10-08 corpus (9,458 documents) the fingerprint hid 249 distinct
    documents while catching 5 true copies. Even a 4,000-character prefix
    of the whole text still hid 2. So the fingerprint plus the body's length
    only groups candidates, and a candidate joins a group's copy only when
    its whole normalised text matches; with the length in the key the full
    texts are read only for the rare candidates that share both (7 groups
    in that corpus, all distinct documents)."""
    by_key: dict[tuple[str, int], list[dict]] = {}
    texts: dict[int, str] = {}

    def full_text(doc_id: int) -> str:
        if doc_id not in texts:
            row = db.execute(
                text("SELECT title, coalesce(body, '') AS body FROM explore_documents WHERE id = :id"),
                {"id": doc_id},
            ).fetchone()
            texts[doc_id] = _NON_WORD_RE.sub(" ", f"{row.title} {row.body}".lower()).strip() if row else ""
        return texts[doc_id]

    representatives: list[dict] = []
    for doc in ranked:
        doc.pop("_authority", None)
        key = (_fingerprint(doc["id"], doc["title"], doc.pop("_bodyHead", "")), doc.pop("_bodyLength", 0))
        group = by_key.setdefault(key, [])
        copy_of = next((rep for rep in group if full_text(rep["id"]) == full_text(doc["id"])), None)
        if copy_of is None:
            doc["duplicateCount"] = 0
            group.append(doc)
            representatives.append(doc)
        else:
            copy_of["duplicateCount"] += 1
    return representatives


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


def _rrf(rank: int | None, weight: float, k: float) -> float:
    """One ranker's contribution (K: explore_ranking.rrf_k). A ranker that
    didn't rank this document contributes nothing — the property that lets
    authority sit in the sum without penalising documents no one can cite."""
    if rank is None or weight == 0:
        return 0.0
    return weight / (k + rank)


def _hydrate_chunk(db) -> int:
    """Bind parameters per hydration statement.

    Both channels can each return a full candidate pool, so the union can
    exceed SQLite's variable ceiling. That ceiling is a compile-time limit
    that varies by build — 999 historically, 32766 since 3.32 — so it is
    asked of the running connection rather than assumed. Half of it, to
    leave room for the filter parameters in the same statement.
    """
    import sqlite3

    try:
        raw = db.connection().connection.driver_connection
        return max(1, raw.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER) // 2)
    except Exception:
        # Every SQLite ever shipped accepts at least 999.
        return 999 // 2


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
    chunk_size = _hydrate_chunk(db)
    for start in range(0, len(doc_ids), chunk_size):
        chunk = doc_ids[start:start + chunk_size]
        placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
        params = {f"id{i}": doc_id for i, doc_id in enumerate(chunk)}
        rows.extend(db.execute(text(
            f"""SELECT id, external_id, title, date, doc_type, source, politician_name,
                       politician_id, chamber, agency_name, agency_slug, url, summary,
                       comment_url, comments_close_on, cited_by_count, authority,
                       substr(coalesce(body, ''), 1, 400) AS body_head,
                       length(coalesce(body, '')) AS body_length
                FROM explore_documents WHERE id IN ({placeholders})"""
        ), params).fetchall())

    return {
        int(row.id): {
            "id": int(row.id),
            # The permanent address to link (services/explore_keys.py).
            "key": public_key(row.external_id, int(row.id)),
            "title": row.title or "",
            "date": row.date or "",
            "docType": row.doc_type or "",
            "source": row.source or "",
            "politicianName": row.politician_name or "",
            "politicianId": row.politician_id or "",
            "chamber": row.chamber or "",
            "agencyName": row.agency_name or "",
            "agencySlug": row.agency_slug or "",
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
            "_bodyLength": int(row.body_length or 0),
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


def _query_identifiers(query: str) -> set[str]:
    """Canonical identifiers a query names ("91 FR 48248" -> fr:91-48248).

    The citation parser is case-sensitive where the published format is
    ("FR", "RIN"), so a document's prose "89 fr more" is no citation; a
    query is typed in any case, and upper-casing it first reads "91 fr
    48248" or "rin 2120-aa66" as the citation it can only be."""
    found = extract_citations((query or "").upper()) | query_record_identifiers(query)
    bare = _BARE_FR_DOC_RE.fullmatch(query or "")
    if bare:
        found.add(f"frdoc:{bare.group(1)}")
    return found


def _identifier_only(query: str) -> bool:
    """A query that is nothing but identifiers, a bare FR document number
    included: a lookup, not words to match."""
    return is_identifier_query(query) or bool(_BARE_FR_DOC_RE.fullmatch(query or ""))


# How each identifier is written in a document's text, as FTS5 phrases: the
# keyword channel's query for an identifier-only search, so it returns the
# documents that cite the identifier and not every document sharing a word
# with it ("EO 14148" matched thirty other orders on "EO"). A Record
# citation or granule appears in no document's text, so it has none.
def _identifier_phrases(identifiers: set[str]) -> str:
    phrases: list[str] = []
    for ident in sorted(identifiers):
        namespace, _, value = ident.partition(":")
        if namespace == "eo":
            phrases += [f"Executive Order {value}", f"EO {value}", f"E.O. {value}"]
        elif namespace == "proc":
            phrases.append(f"Proclamation {value}")
        elif namespace == "fr":
            volume, _, page = value.partition("-")
            phrases.append(f"{volume} FR {page}")
        elif namespace in ("frdoc", "rin", "docket"):
            phrases.append(value)
    return " ".join(f'"{p}"' for p in phrases)


def _identifier_matches(
    db, query: str, *, limit: int, doc_type: str | None,
    chamber: str | None, politician_id: str | None,
    agency_slugs: list[str] | None = None,
) -> list[int]:
    """Documents *known by* an identifier the query names — the document
    whose own FR citation, FR document number, RIN or executive-order
    number it is, not every document that cites it.

    A partial ranker (AGENTS.md §3b): only documents that carry identifiers
    can match, and a match is exact, so it says nothing about any other
    document. Neither retrieval channel could make this lookup: the keyword
    index holds title, summary and body, where a document's own citation
    never appears (the FR prints it in the page header, not the text), and
    the encoder has no meaning for a serial number. On a known-item probe
    of 80 documents their fusion put the target first for 3% of FR
    citations (2026-10).

    The SQL is a coarse prefilter on the identifier's value; the match
    itself is `declared_identifiers`, the same definition the citation
    graph uses, so a document is found by exactly the names it is cited by.
    """
    wanted = _query_identifiers(query)
    if not wanted:
        return []
    # A Record citation's value ("172-H5836") is not in the row as written:
    # its page ("H5836") is, in the granule the external id carries.
    needles = sorted({ident.split(":", 1)[1].split("-", 1)[-1] if ident.startswith("crec:")
                      else ident.split(":", 1)[1] for ident in wanted})
    clauses = " OR ".join(
        f"identifiers LIKE :n{i} OR external_id LIKE :n{i} OR title LIKE :n{i} OR comment_url LIKE :n{i}"
        for i in range(len(needles)))
    params: dict = {f"n{i}": f"%{needle}%" for i, needle in enumerate(needles)}
    sql = (f"SELECT id, external_id, title, doc_type, identifiers, comment_url "
           f"FROM explore_documents WHERE ({clauses})")
    if doc_type:
        sql += " AND doc_type = :doc_type"
        params["doc_type"] = doc_type
    if chamber:
        sql += " AND chamber = :chamber"
        params["chamber"] = chamber
    if politician_id:
        sql += " AND politician_id = :politician_id"
        params["politician_id"] = politician_id
    if agency_slugs:
        sql += " AND agency_slug IN (" + ", ".join(f":a{i}" for i in range(len(agency_slugs))) + ")"
        params.update({f"a{i}": s for i, s in enumerate(agency_slugs)})
    rows = db.execute(text(sql + " ORDER BY id"), params).fetchall()
    return [
        int(row.id) for row in rows
        if wanted & declared_identifiers(
            row.external_id, row.title, row.doc_type, row.identifiers, row.comment_url)
    ][:limit]


def browse_documents(
    db,
    politician_id: str | None,
    *,
    limit: int = 20,
    doc_type: str | None = None,
    chamber: str | None = None,
    commentable: bool = False,
    agency_slugs: list[str] | None = None,
) -> dict:
    """Documents with no query. One member's, or one agency's
    (`agency_slugs`: it and its sub-agencies), newest first: what a
    profile's or an agency page's "view all documents" link opens. With
    neither, only `commentable` (every open comment period), rules first
    and most read, as the Today tab orders them, with paperwork notices
    apart under `paperwork` (app/comment_periods). Same response shape as hybrid_search, with no retrieval channel
    involved (so no distance, no matched terms)."""
    today = comment_period_today()
    query = db.query(ExploreDocument.id)
    if politician_id is not None:
        query = query.filter(ExploreDocument.politician_id == politician_id)
    if agency_slugs:
        query = query.filter(ExploreDocument.agency_slug.in_(agency_slugs))
    if doc_type:
        query = query.filter(ExploreDocument.doc_type == doc_type)
    if chamber or commentable:
        query = query.filter(ExploreDocument.chamber == (chamber or "Regulatory"))
    if commentable:
        query = query.filter(
            ExploreDocument.comment_url.isnot(None), ExploreDocument.comment_url != "",
            ExploreDocument.comments_close_on >= today,
        )
    paperwork_ids: list[int] = []
    paperwork_count = 0
    if commentable and politician_id is None:
        # Every open period (hundreds at most), ordered as the Today tab
        # orders them: rules first, paperwork notices folded apart, closing
        # soonest first (app/comment_periods).
        rows = query.with_entities(
            ExploreDocument.id, ExploreDocument.doc_type, ExploreDocument.fr_toc_subject,
            ExploreDocument.fr_page_views, ExploreDocument.comments_close_on,
        ).all()
        paperwork = sorted((r for r in rows if comment_periods.is_paperwork(r.doc_type, r.fr_toc_subject)),
                           key=lambda r: (r.comments_close_on, -r.id))
        others = sorted((r for r in rows if not comment_periods.is_paperwork(r.doc_type, r.fr_toc_subject)),
                        key=comment_periods.salience)
        ids = [r.id for r in others[:limit]]
        paperwork_ids, paperwork_count = [r.id for r in paperwork[:limit]], len(paperwork)
    else:
        order = (
            (ExploreDocument.date.desc(), ExploreDocument.id.desc())
            if politician_id is not None or (agency_slugs and not commentable)
            else (ExploreDocument.comments_close_on.asc(), ExploreDocument.date.desc(), ExploreDocument.id.desc())
        )
        ids = [row.id for row in query.order_by(*order).limit(limit)]
    hydrated = _hydrate(db, ids + paperwork_ids)

    def shaped(doc_ids: list[int]) -> list[dict]:
        out = []
        for doc_id in doc_ids:
            doc = hydrated.get(doc_id)
            if doc is None:
                continue
            doc.pop("_authority", None)
            doc.pop("_bodyHead", None)
            doc.pop("_bodyLength", None)
            doc.update(matchedBy=[], distance=None, snippet=doc["summary"], duplicateCount=0)
            if commentable:
                # The abstract's opening sentence, verbatim, as the card's
                # text, and the comment window from publication to deadline.
                doc["snippet"] = comment_periods.first_sentence(doc["summary"]) or doc["summary"]
                doc["commentWindow"] = comment_periods.comment_window(doc["date"], doc["commentsCloseOn"], today)
            out.append(doc)
        return out

    results = shaped(ids)
    outcome = {
        "results": results,
        "count": len(results),
        "indexReady": True,
        "semanticUnavailable": False,
        "channels": {"semantic": 0, "keyword": 0, "identifier": 0},
    }
    if commentable and politician_id is None:
        outcome["paperwork"] = {"count": paperwork_count, "results": shaped(paperwork_ids)}
    return outcome


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
    include_priors: bool = True,
    agency_slugs: list[str] | None = None,
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

    `include_priors=False` runs the two retrieval channels alone. It is a
    measurement affordance for the evaluation harness, not a mode the API
    exposes.
    """
    default_pool, max_pool = candidate_pool()
    pool = min(max(limit * 8, default_pool), max_pool)
    today = comment_period_today()
    commentable_after = today if commentable else None

    # `commentable` documents are Federal Register rulemaking and nothing
    # else, so scoping the semantic channel to that chamber asks the index
    # a question it can answer precisely, instead of retrieving a general
    # pool and hoping enough regulatory documents survive the filter.
    # An agency's documents are rulemaking too (only those carry one).
    effective_chamber = chamber or ("Regulatory" if commentable or agency_slugs else None)

    # A query that is only publisher identifiers ("89 FR 52508") is a lookup
    # the encoder has no meaning for: on known-item probes the semantic
    # channel found 2-3% of such targets in its top 20. Yet RRF rewards
    # appearing in both lists, so a document that both channels ranked
    # middling outscored the keyword channel's exact hit at rank 1 — fused
    # R@1 on those probes was 0.25 against keyword's 0.65 (2026-10). The
    # channel abstains (an empty list, not None: it is not unavailable).
    identifier_only = _identifier_only(query)
    if identifier_only:
        semantic = []
    else:
        try:
            semantic = search_explore_documents(
                query=query, n_results=pool, doc_type=doc_type,
                chamber=effective_chamber, politician_id=politician_id,
            )
        except Exception:
            logger.exception("Semantic channel failed")  # never the query (AGENTS.md §8)
            semantic = None

    # An identifier-only query asks the keyword channel for the identifier as
    # written, nothing looser (_identifier_phrases).
    keyword_query = _identifier_phrases(_query_identifiers(query)) if identifier_only else query
    keyword = search_lexical(
        db, keyword_query, limit=pool, doc_type=doc_type, chamber=effective_chamber,
        politician_id=politician_id, commentable_after=commentable_after,
        agency_slugs=agency_slugs,
    )
    exact = _identifier_matches(
        db, query, limit=pool, doc_type=doc_type, chamber=effective_chamber,
        politician_id=politician_id, agency_slugs=agency_slugs,
    )
    exact_ids = set(exact)
    # An identifier the query names that no document is known by: say so,
    # with where the official copy is (explore_coverage.not_held). Only on
    # an unfiltered search, where "not held" is what the miss means.
    wanted = _query_identifiers(query)
    unfiltered = not (doc_type or chamber or politician_id or commentable or agency_slugs)
    held_note = not_held(db, wanted) if wanted and not exact and unfiltered else []

    # None (not []) from the semantic channel means the vector index is
    # missing or mid-rebuild, which is different from "it found nothing".
    # The distinction has to survive to the response: a page served on the
    # keyword channel alone is a partial answer, and telling the reader so
    # is the difference between honest degradation and quiet degradation.
    semantic_unavailable = semantic is None

    if semantic_unavailable and not keyword and not exact:
        return {
            "results": [], "count": 0, "indexReady": False,
            "semanticUnavailable": True,
            "channels": {"semantic": 0, "keyword": 0, "identifier": 0},
        }

    semantic = semantic or []

    candidate_ids: list[int] = []
    seen_ids: set[int] = set()
    for hit in semantic:
        if hit["id"] not in seen_ids:
            seen_ids.add(hit["id"])
            candidate_ids.append(hit["id"])
    for doc_id in [hit["id"] for hit in keyword] + exact:
        if doc_id not in seen_ids:
            seen_ids.add(doc_id)
            candidate_ids.append(doc_id)

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
    if agency_slugs:
        # The vector index holds no agency, so its hits are filtered here.
        wanted = set(agency_slugs)
        candidates = [doc for doc in candidates if doc["agencySlug"] in wanted]
    if not candidates:
        return {
            "results": [], "count": 0, "indexReady": True,
            "semanticUnavailable": semantic_unavailable,
            "channels": {"semantic": len(semantic), "keyword": len(keyword),
                         "identifier": len(exact)},
            "notHeld": held_note,
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

    # The priors are weighted *relative to the relevance evidence actually
    # present*, not absolutely. Both retrieval channels contribute a
    # combined weight of 2.0 when both return candidates, so a freshness
    # voter of 0.4 is one fifth of the relevance mass — the ratio the
    # weights were chosen for and the one tests/test_explore_search.py
    # pins down. With one channel returning nothing (its index rebuilding,
    # or simply no keyword match) the relevance mass halves while a fixed
    # prior does not, so recency and authority silently double in
    # influence exactly when the engine can least afford it.
    #
    # Measured, not theorised. On a 93-document corpus with the semantic
    # channel unavailable, the same queries scored:
    #
    #     retrieval only (keyword)   MRR 0.978   R@1 0.962
    #     hybrid, fixed priors       MRR 0.752   R@1 0.613
    #     hybrid, scaled priors      MRR 0.850   R@1 0.732
    #
    # A third of top hits displaced by recency, inside the degraded mode
    # this change otherwise advertises as a feature. (Hybrid sits below
    # retrieval-only either way — known-item retrieval scores priors that
    # way by construction; see scripts/evaluate_explore_search.py's
    # "How to read this". The 0.752 -> 0.850 recovery is the signal.)
    retrieval_ranks = (semantic_rank, keyword_rank)
    live_channels = sum(1 for r in retrieval_ranks if r)
    prior_scale = live_channels / len(retrieval_ranks)
    if not include_priors:
        # Retrieval channels only. Exists for the evaluation harness, which
        # needs to separate "did fusing the two retrievers help" from "did
        # the priors help" — two questions that a single blended number
        # cannot answer, and that known-item retrieval scores in opposite
        # directions (see scripts/evaluate_explore_search.py).
        prior_scale = 0.0

    weights = fusion_weights()
    k = rrf_k()
    for doc in candidates:
        doc_id = doc["id"]
        doc["_score"] = (
            _rrf(semantic_rank.get(doc_id), weights["semantic"], k)
            + _rrf(keyword_rank.get(doc_id), weights["keyword"], k)
            + _rrf(freshness_rank.get(doc_id), weights["freshness"] * prior_scale, k)
            + _rrf(authority_rank.get(doc_id), weights["authority"] * prior_scale, k)
        )
        matched: list[str] = []
        if doc_id in semantic_rank:
            matched.append("semantic")
        if doc_id in keyword_rank:
            matched.append("keyword")
        if doc_id in exact_ids:
            matched.append("identifier")
        doc["matchedBy"] = matched
        doc["distance"] = semantic_distance.get(doc_id)
        # The keyword channel's snippet shows the query terms in context and
        # marks them; the semantic channel has no matched terms to point at,
        # so those results keep the document's own summary.
        doc["snippet"] = keyword_snippet.get(doc_id) or doc["summary"]

    # A document the query names by its own identifier leads: the reader
    # asked for that document, and no similarity or prior can outrank the
    # answer to "91 FR 48248". Ties among several (one RIN names every
    # document of a rulemaking) keep the fused order.
    candidates.sort(key=lambda doc: (
        doc["id"] not in exact_ids, -doc["_score"], doc["id"]))

    # Collapse duplicates *after* fusion so the survivor is the copy the
    # rankers liked best, not whichever row was inserted first.
    representatives = _collapse_duplicates(db, candidates)

    if sort == "date":
        # Sorted over the whole filtered candidate pool, not over the page.
        # The previous implementation sorted the twenty results it had
        # already chosen by relevance, so "newest" meant "newest of the
        # twenty most similar" — reliably not the newest matching document.
        representatives.sort(key=lambda doc: (doc["date"], doc["_score"]), reverse=True)
        results = representatives[:limit]
    else:
        # Exact identifier matches are exempt from crowding control: every
        # document of one rulemaking shares an agency, and they are what a
        # RIN search asked for.
        named = [doc for doc in representatives if doc["id"] in exact_ids]
        rest = [doc for doc in representatives if doc["id"] not in exact_ids]
        results = (named + _apply_diversity(rest, source_diversity_cap()))[:limit]

    for doc in results:
        doc.pop("_score", None)

    return {
        "results": results,
        "count": len(results),
        "indexReady": True,
        "semanticUnavailable": semantic_unavailable,
        "channels": {"semantic": len(semantic), "keyword": len(keyword),
                     "identifier": len(exact)},
        "notHeld": held_note,
    }
