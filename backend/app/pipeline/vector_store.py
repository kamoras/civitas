"""
Vector store for semantic search using sqlite-vec and sentence-transformers.

2026-07 migration from ChromaDB (permanent-solutions roadmap program 4):
sqlite-vec is a single-file SQLite extension — pure C, no server, runs on
the Pi — replacing chromadb's heavy dependency tree. The chromadb stack's
hnswlib had no prebuilt aarch64 wheel and SIGILL'd when compiled on a
different ARM microarchitecture than the Pi 5, which is why CI image
publishing was disabled (see ci.yml's build-and-push comment); with it
gone, that constraint disappears. Vectors live in their own SQLite file
(/data/vectors.db), separate from the app database for the same
writer-lock isolation reasoning as the visits split (database.py).

Architecture note — two vector computation paths coexist by design:

  1. **sqlite-vec** (this module): persistent storage + user-facing
     semantic search (explore documents, bill embeddings, admin stats).
     The INDEX is embedded with the similarity model (all-MiniLM-L6-v2 —
     symmetric, measured; see get_similarity_model), replacing the
     retrieval-asymmetric arctic model as part of this migration's
     one-time reindex.

  2. **Numpy matrix ops** (policy_alignment, industry_classifier,
     nn_classifier): pipeline-time batch classification via raw cosine
     similarity matrices, still on the PRIMARY model (arctic) until the
     O1-O7 ground-truth-validated recalibration program — swapping a
     classification gate without re-measuring its threshold is how
     thresholds go vacuous.

Index versioning: the index's model id is stored inside vectors.db
(meta table). A mismatch at startup drops the vec tables and triggers a
background reindex from the ExploreDocument rows already in the app DB
(see ensure_explore_index) — search returns None ("index not ready")
until it completes, which callers already handle.
"""

import json
import logging
import os
import re
import sqlite3
import struct
import threading

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ── Embedding model versions ─────────────────────────────────────
# Classification/learning-store side (numpy paths + LearnedClassification
# kNN references) — unchanged by the index migration.
EMBEDDING_MODEL_NAME = "Snowflake/snowflake-arctic-embed-xs"
EMBEDDING_MODEL_VERSION = "arctic-xs"  # short id for metadata
EMBEDDING_DIMENSIONS = 384

# Search-index side — the similarity model (same 384 dims).
INDEX_MODEL_VERSION = "minilm-l6-v2"

# Layout of vec_explore, tracked separately from the model because the two
# change for different reasons and either one invalidates the index. Bumped
# when the table became chunk-level. `ensure_explore_index` compares the
# pair, so a deployed index rebuilds itself on either change without anyone
# remembering to clear it.
INDEX_SCHEMA_VERSION = "2-chunked"


def index_identity() -> str:
    """What the stored index was built by — model and layout together."""
    return f"{INDEX_MODEL_VERSION}+{INDEX_SCHEMA_VERSION}"

# NOT under /data/chroma/ — that directory is the old chromadb store,
# orphaned by the sqlite-vec migration and safe to delete entirely, but
# this file tracks something unrelated (the PRIMARY/classification model
# version, still arctic-xs, untouched by that migration) and would have
# been silently wiped along with it if left in the same directory.
_VERSION_FILE = "/data/classification_model_version"

_VECTOR_DB_PATH = os.environ.get("VECTOR_DB_PATH", "/data/vectors.db")

_model: "SentenceTransformer | None" = None
_similarity_model: "SentenceTransformer | None" = None
_vec_conn: "sqlite3.Connection | None" = None
_vec_lock = threading.Lock()


def get_embedding_model() -> SentenceTransformer:
    """Get or load the PRIMARY (classification-side) model (singleton)."""
    global _model
    if _model is None:
        logger.info("Loading sentence-transformers model: %s", EMBEDDING_MODEL_NAME)
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


_SIMILARITY_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def get_similarity_model() -> SentenceTransformer:
    """Second embedding model for SYMMETRIC-similarity gates and the
    search index (2026-07 embedding-swap program).

    The primary model (retrieval-asymmetric arctic) places all
    same-register text in a ~0.55-0.87 raw-cosine band, which made
    several similarity thresholds unable to separate genuine matches
    from noise (docs/action_center_audit_2026-07.md; the eval harness in
    scripts/evaluate_embedding_models.py). all-MiniLM-L6-v2 — same ~22M
    size class, so no meaningful Pi cost — measured ~4x the separation
    margin on explore-doc anchoring and ~3x on policy relevance against
    this platform's own live failure cases.

    Scope discipline: the gates re-measured under this model consume it
    (action_center's policy filter, trending mask, explore-doc re-rank,
    topic-candidate/title-dedup sims) plus the search index (reindexed
    under it in the sqlite-vec migration). The centered-space clustering
    gates and the classification subsystem (donor/kNN/bills) stay on the
    primary model until their own measurement + recalibration pass.
    """
    global _similarity_model
    if _similarity_model is None:
        logger.info("Loading similarity model: %s", _SIMILARITY_MODEL_NAME)
        _similarity_model = SentenceTransformer(_SIMILARITY_MODEL_NAME)
    return _similarity_model


# ── sqlite-vec connection & schema ───────────────────────────────

def _serialize(vec) -> bytes:
    return struct.pack("%sf" % len(vec), *vec)


def get_vec_conn() -> sqlite3.Connection:
    """Get or create the sqlite-vec connection (singleton, extension loaded)."""
    global _vec_conn
    with _vec_lock:
        if _vec_conn is None:
            import sqlite_vec

            logger.info("Opening vector store: %s", _VECTOR_DB_PATH)
            conn = sqlite3.connect(_VECTOR_DB_PATH, check_same_thread=False)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            _ensure_schema(conn)
            _vec_conn = conn
        return _vec_conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS vec_meta (key TEXT PRIMARY KEY, value TEXT)")
    # One row per CHUNK, not per document — `doc_id` is the parent. See
    # chunk_text and embed_explore_documents for why the corpus is chunked
    # at all, and search_explore_documents for how chunks are folded back
    # into document-level results.
    conn.execute(
        f"""CREATE VIRTUAL TABLE IF NOT EXISTS vec_explore USING vec0(
            embedding float[{EMBEDDING_DIMENSIONS}] distance_metric=cosine,
            doc_id integer,
            doc_type text,
            chamber text,
            politician_id text,
            +title text,
            +date text,
            +source text,
            +politician_name text,
            +snippet text
        )"""
    )
    conn.execute(
        f"""CREATE VIRTUAL TABLE IF NOT EXISTS vec_bills USING vec0(
            embedding float[{EMBEDDING_DIMENSIONS}] distance_metric=cosine,
            policy_area text,
            +meta_json text
        )"""
    )
    conn.commit()


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM vec_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO vec_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ── Legacy model-version tracking (classification side) ──────────

def get_model_version() -> str:
    """Return the current embedding model version string."""
    return EMBEDDING_MODEL_VERSION


def check_model_version() -> bool:
    """Check if stored embeddings match the current model version.

    Returns True if versions match (or no prior version recorded).
    Returns False if a model change is detected — caller should
    call invalidate_on_model_change().
    """
    try:
        if os.path.exists(_VERSION_FILE):
            with open(_VERSION_FILE) as f:
                stored = f.read().strip()
            return stored == EMBEDDING_MODEL_VERSION
    except OSError:
        pass
    return True


def _write_model_version() -> None:
    try:
        os.makedirs(os.path.dirname(_VERSION_FILE), exist_ok=True)
        with open(_VERSION_FILE, "w") as f:
            f.write(EMBEDDING_MODEL_VERSION)
    except OSError:
        logger.warning("Could not write model version file %s", _VERSION_FILE)


def invalidate_on_model_change(db_session=None) -> None:
    """Wipe model-derived stores after an embedding model change.

    Clears the vector index and the kNN learning store — both hold
    vectors from the previous model that would silently mis-compare
    against new-model queries.
    """
    logger.warning("Embedding model change detected — invalidating stored embeddings")
    reset_vector_db()

    if db_session is not None:
        try:
            from app.models import LearnedClassification

            deleted = db_session.query(LearnedClassification).delete()
            db_session.commit()
            logger.info("Cleared %d learned classifications (stale embeddings)", deleted)
        except Exception:
            logger.exception("Failed clearing learned classifications")
            db_session.rollback()

    _write_model_version()


# ── Write paths ──────────────────────────────────────────────────

def _bill_rowid(bill_id: str) -> int:
    """Stable integer rowid for a string bill id (vec0 rowids are ints).
    Deterministic (not Python's salted hash) so purges/upserts hit the
    same row across processes."""
    import hashlib

    return int.from_bytes(hashlib.sha1(bill_id.encode()).digest()[:7], "big")


def embed_bills(bills: list[dict]) -> None:
    """Embed and store bills in the vector index.

    Uses the PRIMARY (classification-side) model, NOT the similarity
    model: this collection is the kNN reference corpus bill_learning.py
    classifies against — its vectors must live in the same space as the
    classifier's query embeddings. Swapping it without the O1-O7
    ground-truth recalibration would silently break bill classification
    (see module docstring's scope discipline).

    2026-07 fix (O3): every classified bill, including low-confidence
    guesses, used to be upserted here unconditionally and then treated as
    a real kNN reference example forever — the audited 55%-PROCEDURAL
    corpus skew was partly this (the procedural seed match used to report
    a blind 1.0 confidence for every match; see _is_procedural_seed_match's
    fix, same review finding). Only bills whose top policy-area confidence
    clears EMBEDDING_CONFIDENCE_THRESHOLD go into the reference corpus now
    — the same real floor bill_analyzer.py's own classification already
    uses to decide "confident enough to accept outright" vs. falling
    through to a second-pass/fallback guess. A bill excluded here isn't
    lost: it's still scored and served for the current run, just not
    promoted into future runs' training examples.
    """
    if not bills:
        return

    from app.pipeline.analyze.bill_analyzer import EMBEDDING_CONFIDENCE_THRESHOLD

    def _top_confidence(bill: dict) -> float:
        areas = bill.get("policyAreas") or []
        return areas[0].get("confidence", 0.0) if areas else 0.0

    skipped = sum(1 for b in bills if _top_confidence(b) < EMBEDDING_CONFIDENCE_THRESHOLD)
    bills = [b for b in bills if _top_confidence(b) >= EMBEDDING_CONFIDENCE_THRESHOLD]
    if skipped:
        logger.info(
            "embed_bills: skipped %d low-confidence classification(s) (< %.2f) — "
            "not promoted to the kNN reference corpus",
            skipped, EMBEDDING_CONFIDENCE_THRESHOLD,
        )
    if not bills:
        return

    conn = get_vec_conn()
    model = get_embedding_model()

    documents, ids, metas = [], [], []
    for bill in bills:
        policy_area = bill.get("policyArea", "")
        stance = bill.get("stance", "")
        text = (
            f"{bill.get('billName', '')} "
            f"{bill.get('description', '')} "
            f"Policy: {policy_area}. "
            f"Stance: {stance}."
        ).strip()
        documents.append(text)
        ids.append(bill["billId"])
        metas.append({
            "billId": bill["billId"],
            "billName": bill.get("billName", "")[:200],
            "policyArea": policy_area,
            "stance": stance,
            "congress": str(bill.get("congress", "")),
            "date": bill.get("date", ""),
        })

    embeddings = model.encode(documents, show_progress_bar=False, normalize_embeddings=True)
    with _vec_lock:
        for bid, emb, meta in zip(ids, embeddings, metas):
            rowid = _bill_rowid(bid)
            conn.execute("DELETE FROM vec_bills WHERE rowid = ?", (rowid,))
            conn.execute(
                "INSERT INTO vec_bills (rowid, embedding, policy_area, meta_json) "
                "VALUES (?, ?, ?, ?)",
                (rowid, _serialize(emb), meta.get("policyArea") or "PROCEDURAL",
                 json.dumps(meta)),
            )
        conn.commit()

    logger.info("Stored %d bill embeddings in vector DB", len(bills))


# Paragraph and sentence boundaries. Chunking splits on the document's own
# structure rather than at a fixed offset, so a window never begins or ends
# mid-thought.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        out.extend(s for s in (p.strip() for p in _SENTENCE_BREAK.split(paragraph)) if s)
    return out


def chunk_text(text: str, max_tokens: int, count_tokens) -> list[str]:
    """Split a document into windows that fit the encoder's context window.

    `max_tokens` is not a tuning choice — it is the model's own
    `max_seq_length`. A sentence-transformer silently truncates anything
    past it, so text beyond that point was never embedded no matter how it
    was passed in. Chunking is what makes a long document reachable rather
    than partially indexed.

    Boundaries are the document's own: paragraphs, then sentences.
    Consecutive windows overlap by one sentence, so a passage that straddles
    a boundary is still wholly present in at least one window. A sentence is
    the unit of overlap because it is a unit of the text; an overlap
    measured in tokens would be a number someone picked.

    A single sentence longer than the window — a Federal Register heading
    run together with its own citation block, most often — is hard-split on
    whitespace, since there is no smaller boundary left to respect.
    """
    text = (text or "").strip()
    if not text:
        return []
    if count_tokens(text) <= max_tokens:
        return [text]

    windows: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            windows.append(" ".join(current))

    for sentence in _sentences(text):
        if count_tokens(sentence) > max_tokens:
            flush()
            current = []
            words = sentence.split()
            piece: list[str] = []
            for word in words:
                if piece and count_tokens(" ".join([*piece, word])) > max_tokens:
                    windows.append(" ".join(piece))
                    piece = [word]
                else:
                    piece.append(word)
            if piece:
                current = [" ".join(piece)]
            continue

        candidate = [*current, sentence]
        if current and count_tokens(" ".join(candidate)) > max_tokens:
            flush()
            # Overlap: carry the last sentence forward, unless doing so
            # would leave no room for the incoming one.
            tail = current[-1]
            current = ([tail, sentence] if count_tokens(f"{tail} {sentence}") <= max_tokens
                       else [sentence])
        else:
            current = candidate

    flush()
    return windows


def embed_explore_documents(docs: list[dict]) -> int:
    """Embed explore documents for semantic search.

    Args:
        docs: list of dicts with keys: id (int), title, summary, body,
              doc_type, source, date, politician_name, chamber.

    Returns:
        Number of documents embedded.
    """
    if not docs:
        return 0

    conn = get_vec_conn()
    model = get_similarity_model()
    max_tokens = int(model.max_seq_length)

    def _count(text: str) -> int:
        return len(model.tokenizer.tokenize(text))

    # Title and summary lead every window. They are the strongest statement
    # of what a document is about, and without them a window drawn from the
    # middle of a rule is a paragraph with no subject.
    units: list[tuple[int, str, dict]] = []
    for doc in docs:
        head = f"{doc.get('title', '')} {doc.get('summary', '')}".strip()
        body = (doc.get("body") or "").strip()
        pieces = chunk_text(f"{head}\n\n{body}".strip(), max_tokens, _count)
        if not pieces:
            continue
        for piece in pieces:
            text = piece if piece.startswith(head[:40]) else f"{head} {piece}".strip()
            units.append((int(doc["id"]), text, doc))

    if not units:
        return 0

    doc_ids = {doc_id for doc_id, _, _ in units}
    with _vec_lock:
        for doc_id in doc_ids:
            conn.execute("DELETE FROM vec_explore WHERE doc_id = ?", (doc_id,))
        conn.commit()

    BATCH = 200
    for i in range(0, len(units), BATCH):
        batch = units[i:i + BATCH]
        embs = model.encode(
            [t for _, t, _ in batch], show_progress_bar=False, normalize_embeddings=True,
        )
        with _vec_lock:
            for (doc_id, text, doc), emb in zip(batch, embs):
                conn.execute(
                    "INSERT INTO vec_explore (embedding, doc_id, doc_type, chamber, "
                    "politician_id, title, date, source, politician_name, snippet) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _serialize(emb), doc_id,
                        doc.get("doc_type", "") or "",
                        doc.get("chamber") or "",
                        doc.get("politician_id") or "",
                        doc.get("title", "")[:200],
                        doc.get("date", "") or "",
                        doc.get("source", "") or "",
                        doc.get("politician_name") or "",
                        text[:300],
                    ),
                )
            conn.commit()

    _set_meta(conn, "explore_index_model", index_identity())
    # Mean chunks per document, measured rather than assumed: the search
    # path needs it to know how many chunk slots to request for a given
    # number of documents. Stored here because it is a property of the
    # index and recomputing it per query is a COUNT DISTINCT over the
    # whole table.
    total_chunks = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
    total_docs = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT doc_id FROM vec_explore)"
    ).fetchone()[0]
    if total_docs:
        _set_meta(conn, "explore_chunks_per_doc", str(total_chunks / total_docs))

    logger.info(
        "Embedded %d explore documents as %d chunks (%.1f per document)",
        len(doc_ids), len(units), len(units) / len(doc_ids),
    )
    return len(doc_ids)


# ── Search ───────────────────────────────────────────────────────

def search_explore_documents(
    query: str,
    n_results: int = 20,
    doc_type: str | None = None,
    chamber: str | None = None,
    politician_id: str | None = None,
) -> list[dict] | None:
    """Semantic search over explore documents.

    Returns list of dicts with id, title, date, docType, source,
    politicianName, politicianId, chamber, distance (cosine distance,
    0 = identical), snippet — or None when the index is empty/not built
    yet (e.g. right after a model-change reindex started), so callers can
    tell "index not ready" apart from "genuinely no matches" (an empty
    list). Filters are pushed into the KNN query (vec0 metadata columns),
    so a member-scoped search returns that member's real matches instead
    of the global top-k intersected down to near-empty.
    """
    conn = get_vec_conn()

    count = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
    if count == 0:
        logger.warning("explore index empty — not ready")
        return None

    model = get_similarity_model()
    query_embedding = model.encode([query], show_progress_bar=False, normalize_embeddings=True)[0]

    # The index holds chunks, so asking for `n_results` rows would return
    # far fewer than `n_results` documents whenever a long rule occupies
    # several of the top slots. Scale the request by the index's own
    # measured mean chunks per document — written at embed time, not
    # guessed here — and bound it by the table size.
    chunks_per_doc = float(_get_meta(conn, "explore_chunks_per_doc") or 1.0)
    k = min(max(int(n_results * max(chunks_per_doc, 1.0)), n_results), count)

    sql = (
        "SELECT doc_id, distance, title, date, doc_type, source, "
        "politician_name, politician_id, chamber, snippet "
        "FROM vec_explore WHERE embedding MATCH ? AND k = ?"
    )
    params: list = [_serialize(query_embedding), k]
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    if chamber:
        sql += " AND chamber = ?"
        params.append(chamber)
    if politician_id:
        sql += " AND politician_id = ?"
        params.append(politician_id)

    # Fold chunks back into documents by their best-matching chunk. Max
    # pooling, not averaging: a hundred-page rule with one passage squarely
    # on the query is a good answer, and averaging over its other ninety-nine
    # pages of unrelated text would bury it under a short document that is
    # vaguely on-topic throughout. Rows arrive in ascending distance, so the
    # first sighting of a doc_id is already its best chunk.
    matches: list[dict] = []
    seen: set[int] = set()
    for row in conn.execute(sql, params).fetchall():
        doc_id = int(row[0])
        if doc_id in seen:
            continue
        seen.add(doc_id)
        matches.append({
            "id": doc_id,
            "distance": float(row[1]),
            "title": row[2] or "",
            "date": row[3] or "",
            "docType": row[4] or "",
            "source": row[5] or "",
            "politicianName": row[6] or "",
            "politicianId": row[7] or "",
            "chamber": row[8] or "",
            "snippet": row[9] or "",
        })
        if len(matches) >= n_results:
            break
    return matches


# ── Maintenance ──────────────────────────────────────────────────

def collection_stats() -> dict:
    """Counts + size for the admin dashboard (replaces chroma's
    list_collections/peek API)."""
    conn = get_vec_conn()
    explore = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
    bills = conn.execute("SELECT COUNT(*) FROM vec_bills").fetchone()[0]
    try:
        size = os.path.getsize(_VECTOR_DB_PATH)
    except OSError:
        size = 0
    return {
        "totalVectors": explore + bills,
        "sizeBytes": size,
        "collections": [
            {"name": "explore_documents", "count": explore, "metadata": {}},
            {"name": "bills", "count": bills, "metadata": {}},
        ],
        "indexModelVersion": _get_meta(conn, "explore_index_model") or "",
        "chunksPerDocument": float(_get_meta(conn, "explore_chunks_per_doc") or 0.0),
    }


def get_bill_reference(limit: int = 5000):
    """kNN reference corpus: (normalized embeddings ndarray, policy labels)
    from the stored bills, or (None, []) when empty.

    2026-07 fix (O3): this LIMIT used to have no ORDER BY. rowid is a
    deterministic hash of bill_id (_bill_rowid), not an insertion or
    recency order, so once the corpus grew past `limit` the excluded rows
    were an arbitrary hash-ordered slice — not "the most recent 5000,"
    just whatever 5000 happened to sort first. Ordered by each bill's own
    `date` (already stored in meta_json for every row; see embed_bills)
    so growth past the cap drops the *oldest* bills, keeping the reference
    corpus current with recent Congresses rather than whichever slice a
    hash function happened to favor.
    """
    import numpy as np

    conn = get_vec_conn()
    rows = conn.execute(
        "SELECT embedding, policy_area FROM vec_bills "
        "ORDER BY json_extract(meta_json, '$.date') DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return None, []
    embs = np.array([np.frombuffer(r[0], dtype=np.float32) for r in rows], dtype=np.float64)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embs / norms, [r[1] or "PROCEDURAL" for r in rows]


def purge_bills(bill_ids: list[str]) -> int:
    """Remove specific bills from the reference corpus. Returns count removed."""
    if not bill_ids:
        return 0
    conn = get_vec_conn()
    removed = 0
    with _vec_lock:
        for bid in bill_ids:
            cur = conn.execute("DELETE FROM vec_bills WHERE rowid = ?", (_bill_rowid(bid),))
            removed += cur.rowcount if cur.rowcount > 0 else 0
        conn.commit()
    return removed


def clear_bills() -> int:
    """Delete all bill embeddings; returns how many existed."""
    conn = get_vec_conn()
    with _vec_lock:
        n = conn.execute("SELECT COUNT(*) FROM vec_bills").fetchone()[0]
        conn.execute("DELETE FROM vec_bills")
        conn.commit()
    return n


def clear_explore() -> None:
    """Delete all explore-document embeddings (pre-reembed reset)."""
    conn = get_vec_conn()
    with _vec_lock:
        conn.execute("DELETE FROM vec_explore")
        conn.commit()


def get_embedded_explore_ids() -> set[int]:
    """Ids of explore documents already in the index (incremental embedding).

    Distinct `doc_id`, not rowid: rows are chunks now, and several of them
    belong to one document.
    """
    conn = get_vec_conn()
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT doc_id FROM vec_explore").fetchall()}


def reset_vector_db() -> None:
    """Reset the entire vector index (useful for fresh starts)."""
    conn = get_vec_conn()
    with _vec_lock:
        for name in ("vec_explore", "vec_bills"):
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.execute("DELETE FROM vec_meta")
        conn.commit()
        _ensure_schema(conn)
    logger.info("Reset vector DB")


def ensure_explore_index(db_session_factory) -> None:
    """Rebuild the explore index in the background when it is missing or
    was built by a different model — the migration/upgrade path.

    Called from app startup (main.py lifespan). Runs in a daemon thread
    because re-embedding thousands of documents takes minutes on the Pi;
    search correctly reports "not ready" (None) until it finishes.
    """
    conn = get_vec_conn()
    stored = _get_meta(conn, "explore_index_model")
    count = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
    if stored == index_identity() and count > 0:
        return

    def _reindex() -> None:
        db = db_session_factory()
        try:
            from app.models import ExploreDocument

            if stored is not None and stored != index_identity():
                logger.warning(
                    "Explore index identity changed (%s -> %s) — rebuilding",
                    stored, index_identity(),
                )
                with _vec_lock:
                    conn.execute("DELETE FROM vec_explore")
                    conn.commit()

            total = 0
            BATCH = 500
            offset = 0
            while True:
                docs = (
                    db.query(ExploreDocument)
                    .order_by(ExploreDocument.id)
                    .offset(offset).limit(BATCH).all()
                )
                if not docs:
                    break
                total += embed_explore_documents([
                    {
                        "id": d.id, "title": d.title, "summary": d.summary or "",
                        "body": getattr(d, "body", "") or "",
                        "doc_type": d.doc_type, "source": getattr(d, "source", "") or "",
                        "date": d.date or "",
                        "politician_name": getattr(d, "politician_name", "") or "",
                        "politician_id": getattr(d, "politician_id", "") or "",
                        "chamber": getattr(d, "chamber", "") or "",
                    }
                    for d in docs
                ])
                offset += BATCH
            logger.info("Explore index rebuild complete: %d documents", total)
        except Exception:
            logger.exception("Explore index rebuild failed")
        finally:
            db.close()

    threading.Thread(target=_reindex, name="explore-reindex", daemon=True).start()
