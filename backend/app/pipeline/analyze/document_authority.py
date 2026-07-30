"""Document authority — PageRank over the federal citation graph.

Vector similarity answers "what is this document about?" It cannot answer
"which of these documents matters?" — the question web search solved with
link analysis (Brin & Page 1998). Federal documents have the same
structure available: they cite each other, constantly and by canonical
identifier. An executive order that agencies keep invoking a decade later
("consistent with Executive Order 12866...") is doing the same job as a
heavily-linked web page, and the citation graph says so without anyone
hand-ranking anything.

**The identifiers are parsed, not classified.** The regexes below match
*documented citation formats* published in the Office of the Federal
Register's Document Drafting Handbook (executive order and proclamation
numbering, the "volume FR page" citation form, the "FR Doc." filing
stamp) and the Unified Agenda's RIN format. That is the AGENTS.md
principle-1 carve-out for data-format conventions — none of these decide
what a document *is about*, they only recognise a serial number that the
publisher assigned. Nothing here classifies.

**A document only earns authority if the corpus actually cites it.**
`compute_document_authority` returns `cited_by` alongside the PageRank
score, and the search ranker only lets documents with `cited_by > 0` into
the authority ranking at all (see `services/explore_search.py`). That
matters because citability is unevenly distributed by document type: a
Federal Register rule carries an FR citation the next rule can point at,
while a floor speech carries nothing anyone cites. If uncited documents
were ranked at the bottom of an authority ordering rather than left out
of it, every query would quietly demote every speech in the corpus.
Leaving them out means the prior can *lift* a well-cited document and can
never *push down* a document that had no way to earn the signal — the
same "a ranker that didn't return you contributes zero" semantics
reciprocal rank fusion already gives the retrieval channels.

**Sparse graph ⇒ no-op, by construction.** On a corpus too new or too
narrow to have accumulated cross-references, few or no documents clear
`cited_by > 0`, the authority ranking is empty or near-empty, and the
prior contributes nothing to anyone. It grows a voice as the corpus grows
one, which is the correct failure mode for a signal like this.
"""

from __future__ import annotations

import json
import logging
import re

import numpy as np

logger = logging.getLogger(__name__)

# Damping factor from Brin & Page (1998) — the probability the random
# surfer follows a citation rather than teleporting. 0.85 is their
# published value and the one sponsorship_analysis.compute_leadership_scores
# already uses for the cosponsorship graph.
PAGERANK_DAMPING = 0.85
PAGERANK_MAX_ITERATIONS = 200
PAGERANK_TOLERANCE = 1e-10

def _max_scan_chars() -> int:
    """How much of a body to scan for citations — the longest body the
    ingest fetchers will ever store, so nothing is missed and nothing is
    scanned twice. Read from the fetcher rather than restated, so the two
    cannot drift apart.
    """
    from app.pipeline.fetch.fr_rulemaking import MAX_BODY_LEN

    return MAX_BODY_LEN


# ── Canonical identifier extraction ──────────────────────────────
#
# Each namespace is a format the *publisher* assigns, so two documents
# referring to the same thing produce the same string:
#
#   eo:14110        Executive Order 14110
#   proc:10714      Proclamation 10714
#   fr:89-12345     Federal Register citation, "89 FR 12345"
#   frdoc:2023-24283  Federal Register document number
#   rin:2060-AV50   Regulation Identifier Number
#
# Case-sensitivity is deliberate where the format is: "FR" and "RIN" are
# uppercase in every published citation, and matching them case-insensitively
# turns ordinary prose ("...for 89 fr more...") into phantom edges.

_EO_RE = re.compile(r"\bexecutive\s+order\s+(?:no\.?\s*)?(\d{4,5})\b", re.IGNORECASE)
_EO_ABBR_RE = re.compile(r"\bE\.\s?O\.\s*(?:no\.?\s*)?(\d{4,5})\b", re.IGNORECASE)
_EO_TITLE_RE = re.compile(r"\bEO\s+(\d{4,5})\b")
_PROC_RE = re.compile(r"\bproclamation\s+(?:no\.?\s*)?(\d{4,5})\b", re.IGNORECASE)
_FR_CITE_RE = re.compile(r"\b(\d{2,3})\s+FR\s+(\d{1,6})\b")
_FR_DOC_RE = re.compile(r"\bFR\s+Doc\.?\s*(\d{4}-\d{4,6})\b", re.IGNORECASE)
_RIN_RE = re.compile(r"\bRIN\s+(\d{4}[-–][A-Z]{2}\d{2})\b")


def _normalize_rin(raw: str) -> str:
    return raw.replace("–", "-").upper()


def extract_citations(text: str) -> set[str]:
    """Canonical identifiers referenced by a document's text.

    Returns a *set*: a rule that invokes E.O. 12866 six times is one
    citation, exactly as six links from one web page to another count
    once in link analysis.
    """
    if not text:
        return set()
    text = text[:_max_scan_chars()]

    found: set[str] = set()
    for pattern in (_EO_RE, _EO_ABBR_RE):
        for match in pattern.finditer(text):
            found.add(f"eo:{int(match.group(1))}")
    for match in _PROC_RE.finditer(text):
        found.add(f"proc:{int(match.group(1))}")
    for match in _FR_CITE_RE.finditer(text):
        found.add(f"fr:{int(match.group(1))}-{int(match.group(2))}")
    for match in _FR_DOC_RE.finditer(text):
        found.add(f"frdoc:{match.group(1)}")
    for match in _RIN_RE.finditer(text):
        found.add(f"rin:{_normalize_rin(match.group(1))}")
    return found


def declared_identifiers(
    external_id: str | None,
    title: str | None,
    doc_type: str | None,
    identifiers_json: str | None = None,
) -> set[str]:
    """Canonical identifiers a document *is known by* — its citable names.

    Three sources, in order of reliability:

    1. `identifiers_json`, the list the ingest pipeline stored straight
       from the Federal Register API (FR citation, document number, RINs).
       Authoritative when present.
    2. `external_id`, which encodes the FR document number for every
       FR-sourced row (`fr-<n>` for presidential documents, `fr-reg-<n>`
       for rulemaking).
    3. The title, which carries the executive-order number for
       presidential documents (`presidential_actions` formats these as
       "EO 14110: ...").

    2 and 3 are what make this work on documents ingested before the
    `identifiers` column existed — they need no re-ingest, so the graph
    covers the existing corpus from the first run rather than only
    documents added afterwards.
    """
    ids: set[str] = set()

    if identifiers_json:
        try:
            stored = json.loads(identifiers_json)
        except (ValueError, TypeError):
            stored = []
        if isinstance(stored, list):
            ids.update(str(i) for i in stored if i)

    ext = external_id or ""
    if ext.startswith("fr-reg-"):
        ids.add(f"frdoc:{ext[len('fr-reg-'):]}")
    elif ext.startswith("fr-"):
        ids.add(f"frdoc:{ext[len('fr-'):]}")

    title = title or ""
    doc_type = doc_type or ""
    if doc_type == "Executive Order":
        match = _EO_TITLE_RE.search(title) or _EO_RE.search(title) or _EO_ABBR_RE.search(title)
        if match:
            ids.add(f"eo:{int(match.group(1))}")
    elif doc_type == "Proclamation":
        match = _PROC_RE.search(title)
        if match:
            ids.add(f"proc:{int(match.group(1))}")

    return ids


# ── PageRank over the citation graph ─────────────────────────────

def pagerank(
    n: int,
    edges: list[tuple[int, int]],
    damping: float = PAGERANK_DAMPING,
) -> np.ndarray:
    """Sparse PageRank by power iteration (Brin & Page 1998).

    `edges` are (citing, cited) index pairs. Dangling nodes — documents
    that cite nothing, which is most of any federal corpus — have their
    mass redistributed uniformly, the standard formulation; without it
    the stationary distribution leaks away and every score decays toward
    zero at a rate set by how many speeches happen to be in the index.

    Sparse on purpose: an explicit n×n transition matrix is ~800 MB at
    10k documents, which is not a thing that fits on the Pi this runs on.
    The bincount below is the same matrix-vector product with only the
    non-zero entries materialised.
    """
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if not edges:
        return np.full(n, 1.0 / n, dtype=np.float64)

    src = np.array([e[0] for e in edges], dtype=np.int64)
    dst = np.array([e[1] for e in edges], dtype=np.int64)
    out_degree = np.bincount(src, minlength=n).astype(np.float64)

    dangling_mask = out_degree == 0
    safe_degree = np.where(dangling_mask, 1.0, out_degree)

    x = np.full(n, 1.0 / n, dtype=np.float64)
    teleport = (1.0 - damping) / n

    for _ in range(PAGERANK_MAX_ITERATIONS):
        contribution = np.bincount(dst, weights=x[src] / safe_degree[src], minlength=n)
        dangling_mass = x[dangling_mask].sum()
        nxt = damping * (contribution + dangling_mass / n) + teleport
        if np.abs(nxt - x).sum() < PAGERANK_TOLERANCE:
            x = nxt
            break
        x = nxt

    total = x.sum()
    return x / total if total > 0 else x


def build_citation_graph(
    identity_rows, text_rows,
) -> tuple[list[int], list[tuple[int, int]], np.ndarray]:
    """Document ids by row index, edges as row-index pairs, and inbound counts.

    Rows are dicts with keys: id, external_id, title, doc_type, summary,
    body, identifiers. Order defines the row indices.

    Two separate passes over two separate iterables, rather than one pass
    over a list of fully-populated documents. `identity_rows` needs only the
    small columns (id, external_id, title, doc_type, identifiers);
    `text_rows` needs the body, which is the expensive one. Splitting them
    is what lets `update_document_authority` stream bodies past this
    function and discard each after use instead of holding the entire
    corpus's text — tens of megabytes, in a process that also holds two
    sentence-transformer models. In-memory callers (tests, small corpora)
    pass the same list twice.

    `text_rows` may arrive in any order and may omit documents; each is
    matched back by id. A text row for an unknown id is skipped rather than
    silently indexed at the wrong position.

    Self-citations are dropped. Federal Register documents stamp their own
    "FR Doc. 2023-24283 Filed" line into their own body, and a final rule
    restates its own RIN in its own heading — counted, every FR document
    would cite itself and the ranking would be a document-length contest.
    """
    order: list[int] = []
    row_of: dict[int, int] = {}
    owner: dict[str, list[int]] = {}
    for doc in identity_rows:
        row = len(order)
        order.append(doc["id"])
        row_of[doc["id"]] = row
        for ident in declared_identifiers(
            doc.get("external_id"), doc.get("title"),
            doc.get("doc_type"), doc.get("identifiers"),
        ):
            owner.setdefault(ident, []).append(row)

    edges: set[tuple[int, int]] = set()
    for doc in text_rows:
        row = row_of.get(doc["id"])
        if row is None:
            continue
        text = f"{doc.get('summary') or ''}\n{doc.get('body') or ''}"
        for ident in extract_citations(text):
            for target in owner.get(ident, ()):
                if target != row:
                    edges.add((row, target))

    edge_list = sorted(edges)
    inbound = np.zeros(len(order), dtype=np.int64)
    for _, target in edge_list:
        inbound[target] += 1
    return order, edge_list, inbound


def compute_document_authority(docs: list[dict]) -> dict[int, tuple[float, int]]:
    """Map document id → (pagerank score, inbound citation count).

    The in-memory entry point: fine for tests and small corpora, and the
    same code path `update_document_authority` takes with streamed rows.

    The score is the raw stationary probability. Callers rank by it rather
    than thresholding on it, so it deliberately isn't rescaled here — a
    rescale would need a calibration constant, and the ranker only ever
    asks "which of these is higher".
    """
    if not docs:
        return {}
    return _authority_from_graph(*build_citation_graph(docs, docs))


def _authority_from_graph(
    order: list[int], edges: list[tuple[int, int]], inbound: np.ndarray,
) -> dict[int, tuple[float, int]]:
    scores = pagerank(len(order), edges)
    logger.info(
        "Citation graph: %d documents, %d edges, %d cited at least once",
        len(order), len(edges), int((inbound > 0).sum()),
    )
    return {
        doc_id: (float(scores[row]), int(inbound[row]))
        for row, doc_id in enumerate(order)
    }


def _stream_batch() -> int:
    """Rows fetched per batch while streaming bodies.

    Derived from the largest body the fetchers store and a one-megabyte
    resident-text ceiling, so the memory this pass holds is bounded by the
    data's own shape rather than by a row count someone picked. Any value
    is *correct* — this only trades resident memory against round trips —
    but tying it to MAX_BODY_LEN means it stays right if bodies grow.
    """
    from app.pipeline.fetch.fr_rulemaking import MAX_BODY_LEN

    return max(1, (1024 * 1024) // max(MAX_BODY_LEN, 1))


def update_document_authority(db) -> dict:
    """Recompute authority for every explore document and persist it.

    Called at the end of the explore ingest pipeline.

    Two queries, deliberately. The first reads only the small columns and
    builds the identifier map; the second streams bodies in batches, which
    are handed to the citation extractor and dropped. Reading all of it in
    one pass would hold the corpus's entire body text — tens of megabytes —
    resident on a device that is simultaneously running two
    sentence-transformer models and the rest of the nightly pipeline.
    `yield_per` alone does not achieve this: it batches the *fetch*, and
    accumulating the rows it yields into a list puts every body right back
    in memory.
    """
    from app.models import ExploreDocument

    identity_rows = [
        {"id": row.id, "external_id": row.external_id, "title": row.title,
         "doc_type": row.doc_type, "identifiers": row.identifiers}
        for row in db.query(
            ExploreDocument.id, ExploreDocument.external_id,
            ExploreDocument.title, ExploreDocument.doc_type,
            ExploreDocument.identifiers,
        ).yield_per(_stream_batch())
    ]
    if not identity_rows:
        return {"documents": 0, "cited": 0}

    def _stream_text():
        for row in db.query(
            ExploreDocument.id, ExploreDocument.summary, ExploreDocument.body,
        ).yield_per(_stream_batch()):
            yield {"id": row.id, "summary": row.summary, "body": row.body}

    authority = _authority_from_graph(
        *build_citation_graph(identity_rows, _stream_text())
    )
    if not authority:
        return {"documents": 0, "cited": 0}

    from sqlalchemy import update

    payload = [
        {"id": doc_id, "authority": score, "cited_by_count": cited_by}
        for doc_id, (score, cited_by) in authority.items()
    ]
    # Single executemany keyed on the primary key rather than one
    # UPDATE...WHERE round trip per document: the corpus is thousands of
    # rows and this runs inside the nightly pipeline's write window,
    # where the app DB's writer lock is the scarce resource.
    db.execute(update(ExploreDocument), payload)
    db.commit()
    updated = len(payload)

    cited = sum(1 for _, c in authority.values() if c > 0)
    logger.info("Updated authority for %d documents (%d cited)", updated, cited)
    return {"documents": updated, "cited": cited}
