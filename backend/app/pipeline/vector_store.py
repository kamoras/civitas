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
    conn.execute(
        f"""CREATE VIRTUAL TABLE IF NOT EXISTS vec_explore USING vec0(
            embedding float[{EMBEDDING_DIMENSIONS}] distance_metric=cosine,
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
    (see module docstring's scope discipline)."""
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

    rows = []
    for doc in docs:
        text = (
            f"{doc.get('title', '')} "
            f"{doc.get('summary', '')} "
            f"{doc.get('body', '')[:800]}"
        ).strip()
        if not text:
            continue
        rows.append((int(doc["id"]), text, doc))

    if not rows:
        return 0

    BATCH = 200
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        embs = model.encode(
            [t for _, t, _ in batch], show_progress_bar=False, normalize_embeddings=True,
        )
        with _vec_lock:
            for (doc_id, text, doc), emb in zip(batch, embs):
                conn.execute("DELETE FROM vec_explore WHERE rowid = ?", (doc_id,))
                conn.execute(
                    "INSERT INTO vec_explore (rowid, embedding, doc_type, chamber, "
                    "politician_id, title, date, source, politician_name, snippet) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        doc_id, _serialize(emb),
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

    _set_meta(conn, "explore_index_model", INDEX_MODEL_VERSION)
    logger.info("Embedded %d explore documents in vector DB", len(rows))
    return len(rows)


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

    sql = (
        "SELECT rowid, distance, title, date, doc_type, source, "
        "politician_name, politician_id, chamber, snippet "
        "FROM vec_explore WHERE embedding MATCH ? AND k = ?"
    )
    params: list = [_serialize(query_embedding), n_results]
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    if chamber:
        sql += " AND chamber = ?"
        params.append(chamber)
    if politician_id:
        sql += " AND politician_id = ?"
        params.append(politician_id)

    matches = []
    for row in conn.execute(sql, params).fetchall():
        matches.append({
            "id": int(row[0]),
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
    }


def get_bill_reference(limit: int = 5000):
    """kNN reference corpus: (normalized embeddings ndarray, policy labels)
    from the stored bills, or (None, []) when empty. Preserves the
    pre-migration limit semantics (see platform-review O3 for the known
    cap concern — unchanged here)."""
    import numpy as np

    conn = get_vec_conn()
    rows = conn.execute(
        "SELECT embedding, policy_area FROM vec_bills LIMIT ?", (limit,)
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
    """Ids of explore documents already in the index (incremental embedding)."""
    conn = get_vec_conn()
    return {r[0] for r in conn.execute("SELECT rowid FROM vec_explore").fetchall()}


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
    if stored == INDEX_MODEL_VERSION and count > 0:
        return

    def _reindex() -> None:
        db = db_session_factory()
        try:
            from app.models import ExploreDocument

            if stored is not None and stored != INDEX_MODEL_VERSION:
                logger.warning(
                    "Explore index model changed (%s -> %s) — rebuilding",
                    stored, INDEX_MODEL_VERSION,
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
