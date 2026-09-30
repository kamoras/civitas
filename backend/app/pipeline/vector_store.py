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
     similarity matrices, still on the PRIMARY model (arctic) until each
     classification threshold has been re-measured against real
     ground truth on the new model — swapping a classification gate
     without re-measuring its threshold is how thresholds go vacuous.

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
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sentence_transformers import SentenceTransformer
from app.atomic_write import write_text_atomic
from app.background import start_writer
from app.database import SQLITE_BUSY_TIMEOUT_S

logger = logging.getLogger(__name__)

# ── Embedding model versions ─────────────────────────────────────
# Classification/learning-store side (numpy paths + LearnedClassification
# kNN references) — unchanged by the index migration.
EMBEDDING_MODEL_NAME = "Snowflake/snowflake-arctic-embed-xs"
EMBEDDING_MODEL_VERSION = "arctic-xs"  # short id for metadata
EMBEDDING_DIMENSIONS = 384

# Search-index side — the similarity model. Its own width: vec_explore holds
# its vectors and vec_bills the classification model's, and a change of one
# model mustn't resize the other's table.
INDEX_MODEL_VERSION = "minilm-l6-v2"
SIMILARITY_DIMENSIONS = 384

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
# One load at a time per model: a request that arrives while startup's
# preload is still loading a model waits for it rather than loading a
# second copy — and only for that model, not the other.
_model_load_lock = threading.Lock()
_similarity_load_lock = threading.Lock()
_vec_conn: "sqlite3.Connection | None" = None
_vec_lock = threading.Lock()


def get_embedding_model() -> SentenceTransformer:
    """Get or load the PRIMARY (classification-side) model (singleton)."""
    global _model
    if _model is None:
        with _model_load_lock:
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
    from noise (measured by the eval harness in
    scripts/evaluate_embedding_models.py). all-MiniLM-L6-v2 — same ~22M
    size class, so no meaningful Pi cost — measured ~4x the separation
    margin on explore-doc anchoring and ~3x on policy relevance against
    this platform's own live failure cases.

    Scope discipline: the gates re-measured under this model consume it
    (action_center's policy filter, trending mask, explore-doc re-rank,
    topic-candidate/title-dedup sims) plus the search index (reindexed
    under it in the sqlite-vec migration). The centered-space clustering
    gates — action_center.py's DEDUP_THRESHOLD (0.50) and SOURCE_SIM_FLOOR
    (0.25), both still justified only anecdotally ("0.15 was too loose"),
    not against a measured same-story/different-story distribution the
    way this file's other gates (TOPIC_CHANGE_THRESHOLD,
    _NEAR_IDENTICAL_TITLE_THRESHOLD) are — remain unmeasured under either
    model. 2026-08 audit: rather than guess a number, both call sites now
    log a bucketed action_metrics counter per merge/keep decision on every
    run (cluster_dedup_{merged,kept}_sim_bucket_N,
    source_coherence_{kept,dropped}_sim_bucket_N), so the same style of
    measurement evaluate_embedding_models.py does elsewhere accumulates
    automatically from live traffic — no one-off manual production pull
    needed. Once enough runs have logged, read the accumulated
    action-metrics history the same way past thresholds elsewhere in this
    pipeline were calibrated and set real thresholds from it.
    The classification subsystem (donor/kNN/bills) stays on the primary
    model until their own measurement + recalibration pass — the harness's
    donor_type / bill_policy tasks are that measurement, and
    docs/research/embedding-models.md states the decision rule it feeds.
    """
    global _similarity_model
    if _similarity_model is None:
        with _similarity_load_lock:
            if _similarity_model is None:
                logger.info("Loading similarity model: %s", _SIMILARITY_MODEL_NAME)
                _similarity_model = SentenceTransformer(_SIMILARITY_MODEL_NAME)
    return _similarity_model


def encode_normalized(
    model: SentenceTransformer,
    texts: list,
    prompt_name: str | None = None,
    chunk_size: int = 256,
):
    """Encode texts in chunks and L2-normalize the result.

    2026-08 cleanup: this exact batch-encode-then-normalize idiom
    (chunk to bound memory, encode, stack, divide by row norms with a
    zero-norm guard) was duplicated at 4 call sites across
    nn_classifier.py and industry_classifier.py. chunk_size only bounds
    how many texts are handed to the model per call — sentence-
    transformers' own internal batching (batch_size=min(64, len(chunk)))
    is unaffected.
    """
    import numpy as np

    parts = []
    kwargs = {"prompt_name": prompt_name} if prompt_name else {}
    for start in range(0, len(texts), chunk_size):
        chunk = texts[start:start + chunk_size]
        embs = model.encode(chunk, show_progress_bar=False, batch_size=min(64, len(chunk)), **kwargs)
        parts.append(embs)
    result = np.vstack(parts)
    norms = np.linalg.norm(result, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return result / norms


# ── sqlite-vec connection & schema ───────────────────────────────

def _serialize(vec) -> bytes:
    return struct.pack("%sf" % len(vec), *vec)


# While the file isn't in WAL yet, how often an open connection tries the
# switch again (get_vec_conn).
_WAL_RETRY_EVERY_S = 60
_wal_retry_at: float | None = None  # None: in WAL (or not yet opened)


def _switch_to_wal() -> bool:
    """Switch the file to WAL (sqlite_wal.switch_to_wal), waiting a second
    at most: waiting out the whole busy timeout while another process holds
    a transaction would hold every search up behind _vec_lock. Failing, the
    store goes on in its current mode; get_vec_conn tries again a minute
    later, until it takes."""
    from app.sqlite_wal import switch_to_wal

    if switch_to_wal(_VECTOR_DB_PATH, 1.0):
        return True
    logger.info("Vector store busy — WAL switch retried in %ds", _WAL_RETRY_EVERY_S)
    return False


def _retry_wal() -> None:
    global _wal_retry_at
    if _switch_to_wal():
        with _vec_lock:
            _wal_retry_at = None


# The read-only API process only searches this file: a search that can't
# get in (the file still in the rollback journal, behind a pipeline write)
# gives up after sqlite's own default rather than holding a worker thread
# for the writers' full wait.
_API_BUSY_TIMEOUT_S = 5.0


def _busy_timeout_s() -> float:
    from app.config import settings

    return _API_BUSY_TIMEOUT_S if settings.PROCESS_ROLE == "api" else SQLITE_BUSY_TIMEOUT_S


# vec_meta key: the identity (index_identity) of the last complete build of
# the explore index. Search takes the index as ready only while it matches.
# A rebuild blanks it first and records it after its last batch, so an index
# a rebuild left partway (a failure, a restart) is not ready until one
# completes; incremental embeds record it only on an index never built, so
# they can't make a partial or other-model index look whole.
_INDEX_MODEL = "explore_index_model"

# One rebuild at a time in this process (the pipeline's, which is always one
# process): two overlapping would each clear what the other built, and
# whichever finished first would record a partial index as complete.
_rebuild_lock = threading.Lock()
# Rebuilds underway — waiting for the lock, checking, or running (a top-up,
# which holds the lock too, is not one): what is_rebuilding and the
# dashboard report, and check-and-deploy waits out.
_rebuilds_underway = 0
_underway_lock = threading.Lock()
# When the last rebuild that completed began (monotonic): a re-embed asked
# for before that needn't do the same work again — every document it wrote
# was read after the ask.
_last_rebuild_began_at = float("-inf")


@contextmanager
def rebuild_underway() -> Iterator[None]:
    """Count the enclosed work as a rebuild underway (rebuild_explore_index
    does; the admin re-embed wraps its keyword and authority passes too)."""
    global _rebuilds_underway
    with _underway_lock:
        _rebuilds_underway += 1
    try:
        yield
    finally:
        with _underway_lock:
            _rebuilds_underway -= 1

# Documents a rebuild reads and embeds at a time.
_REBUILD_BATCH = 500
# Chunks encoded and written at a time (rounded up to whole documents).
_EMBED_BATCH = 200


def _open_vec_conn(timeout: float, *, check_same_thread: bool = True, extension: bool = True) -> sqlite3.Connection:
    """A new connection to the vector store, with sqlite-vec loaded unless
    `extension` is False (get_vec_conn loads it after its WAL switch)."""
    conn = sqlite3.connect(_VECTOR_DB_PATH, check_same_thread=check_same_thread, timeout=timeout)
    if extension:
        try:
            _load_vec(conn)
        except BaseException:
            conn.close()
            raise
    return conn


def _load_vec(conn: sqlite3.Connection) -> None:
    import sqlite_vec

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _swap_tables(ddl: dict[str, str], *, clear_meta: bool = False, meta: dict[str, str] | None = None) -> None:
    """DROP and recreate each of `ddl`'s tables (name -> CREATE statement)
    in one transaction on a connection of its own, so every other
    connection — the shared one, whose users commit without _vec_lock, and
    the API processes' — sees the old tables or the new, never none. (A
    vec0 table's vector width and columns are fixed at creation: recreating
    is the only way to change them.)"""
    swap = _open_vec_conn(SQLITE_BUSY_TIMEOUT_S)
    try:
        swap.execute("BEGIN IMMEDIATE")
        for name, create in ddl.items():
            swap.execute(f"DROP TABLE IF EXISTS {name}")
            swap.execute(create)
        if clear_meta:
            swap.execute("DELETE FROM vec_meta")
        for key, value in (meta or {}).items():
            # With the tables: a swap that fails leaves what it would have
            # recorded unrecorded too.
            swap.execute(
                "INSERT INTO vec_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        swap.commit()
    finally:
        swap.close()


def is_busy_error(error: BaseException) -> bool:
    """A lock another connection held past the busy timeout: a moment's
    state of the file, not its contents — never a reason to rebuild it."""
    return isinstance(error, sqlite3.OperationalError) and any(
        word in str(error).lower() for word in ("locked", "busy")
    )


def get_vec_conn() -> sqlite3.Connection:
    """Get or create the sqlite-vec connection (singleton, extension loaded)."""
    global _vec_conn, _wal_retry_at
    with _vec_lock:
        if _vec_conn is not None and _wal_retry_at is not None and time.monotonic() >= _wal_retry_at:
            # On a thread of its own: the try can wait a second, and a
            # search holding _vec_lock mustn't. The shared connection's
            # synchronous level stays at the safe default (FULL) until the
            # next open — other threads use it without the lock, so it
            # isn't changed under them.
            _wal_retry_at = time.monotonic() + _WAL_RETRY_EVERY_S
            threading.Thread(target=_retry_wal, daemon=True, name="vectors-wal-switch").start()
        if _vec_conn is None:
            logger.info("Opening vector store: %s", _VECTOR_DB_PATH)
            conn = _open_vec_conn(_busy_timeout_s(), check_same_thread=False, extension=False)
            try:
                # WAL, as the main database has: the pipeline process writes
                # this file while the API processes search it (PROCESS_ROLE),
                # and under the default rollback journal a long write holds
                # every reader off until it commits. Persistent in the file,
                # so after the first switch this is a no-op — and every
                # connection, this one included, follows a switch made by
                # another.
                retry_at = None if _switch_to_wal() else time.monotonic() + _WAL_RETRY_EVERY_S
                # NORMAL only in WAL, where it is durable against a crash; in
                # the rollback journal it can corrupt the file on power loss,
                # so the default (FULL) stands until the switch takes.
                if retry_at is None:
                    conn.execute("PRAGMA synchronous=NORMAL")
                _load_vec(conn)
                _ensure_schema(conn)
            except BaseException:
                # Not kept, so closed: a caller retrying through a locked
                # file mustn't leave a connection behind per attempt.
                conn.close()
                raise
            _wal_retry_at = retry_at
            _vec_conn = conn
        return _vec_conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS vec_meta (key TEXT PRIMARY KEY, value TEXT)")
    # One row per CHUNK, not per document — `doc_id` is the parent. See
    # chunk_text and embed_explore_documents for why the corpus is chunked
    # at all, and search_explore_documents for how chunks are folded back
    # into document-level results.
    conn.execute(_EXPLORE_DDL.format(if_not_exists="IF NOT EXISTS "))
    conn.execute(_TEXT_HASH_DDL.format(if_not_exists="IF NOT EXISTS "))
    conn.execute(_BILLS_DDL.format(if_not_exists="IF NOT EXISTS "))
    conn.commit()


# What each document's vectors were built from (explore_text_hash), written
# with them: a document whose text has changed since — a body backfilled, a
# re-ingest — reads as stale to the next top-up whatever changed it. A
# document embedded before this table existed has no row, and is taken as
# current rather than re-encoded wholesale on the first run after it.
_TEXT_HASH_DDL = """CREATE TABLE {if_not_exists}vec_explore_text (
    doc_id INTEGER PRIMARY KEY,
    text_hash TEXT NOT NULL
)"""


def explore_text_hash(doc: dict) -> str:
    """A hash of everything embed_explore_documents reads from a document."""
    import hashlib

    fields = ("title", "summary", "body", "doc_type", "source", "date", "politician_name", "politician_id", "chamber")
    return hashlib.sha256(
        json.dumps([doc.get(f) or "" for f in fields], ensure_ascii=False).encode()
    ).hexdigest()[:32]


_EXPLORE_DDL = f"""CREATE VIRTUAL TABLE {{if_not_exists}}vec_explore USING vec0(
    embedding float[{SIMILARITY_DIMENSIONS}] distance_metric=cosine,
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


_BILLS_DDL = f"""CREATE VIRTUAL TABLE {{if_not_exists}}vec_bills USING vec0(
    embedding float[{EMBEDDING_DIMENSIONS}] distance_metric=cosine,
    policy_area text,
    +meta_json text
)"""


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
        write_text_atomic(_VERSION_FILE, EMBEDDING_MODEL_VERSION)
    except OSError:
        logger.warning("Could not write model version file %s", _VERSION_FILE)


def invalidate_on_model_change(db_session=None) -> None:
    """Wipe model-derived stores after a classification embedding model
    change.

    Clears the bill vectors (the kNN reference corpus) and the kNN learning
    store — both hold vectors from the previous model that would silently
    mis-compare against new-model queries. Not the Explore search index:
    that one is the similarity model's, and rebuilds itself when its own
    identity changes (ensure_explore_index) — dropping it here threw away a
    whole rebuild, and waited out a running one first.
    """
    logger.warning("Embedding model change detected — invalidating stored embeddings")
    # DROP + recreate, not DELETE: a new model may have another width.
    _swap_tables({"vec_bills": _BILLS_DDL.format(if_not_exists="")})

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
    classifier's query embeddings. Swapping it without recalibrating every
    measured similarity threshold across this pipeline against real
    ground truth would silently break bill classification (see module
    docstring's scope discipline).

    2026-07 fix: every classified bill, including low-confidence
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


def embed_explore_documents(docs: list[dict], *, record_chunks_per_doc: bool = True, fresh: bool = False) -> int:
    """Embed explore documents for semantic search.

    Args:
        docs: list of dicts with keys: id (int), title, summary, body,
              doc_type, source, date, politician_name, chamber.

        record_chunks_per_doc: measure the index's chunks per document
              after (a rebuild measures once, at its end, not per batch
              over a half-built table).
        fresh: the documents aren't in the index (a rebuild's new table),
              so there are no old chunks to delete first.

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

    # In batches of whole documents (about 200 chunks each), each written as
    # soon as it is encoded, in one transaction: each document's old chunks
    # deleted and its new ones inserted together. A failure partway (a lock,
    # an encode error) leaves every document either as it was or as it now
    # is, never with its old chunks gone and only some new ones in — that
    # state reads as embedded to the next top-up, which would never finish
    # it — and keeps every batch written before it. `fresh` (a rebuild's new
    # table) skips the deletes: there is nothing to replace.
    batches: list[list[tuple[int, str, dict]]] = [[]]
    for unit in units:
        if len(batches[-1]) >= _EMBED_BATCH and batches[-1][-1][0] != unit[0]:
            batches.append([])
        batches[-1].append(unit)
    doc_ids: set[int] = set()
    for batch in batches:
        embs = model.encode([t for _, t, _ in batch], show_progress_bar=False, normalize_embeddings=True)
        with _vec_lock:
            try:
                if not fresh:
                    for doc_id in dict.fromkeys(d for d, _, _ in batch):
                        conn.execute("DELETE FROM vec_explore WHERE doc_id = ?", (doc_id,))
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
                for doc_id, doc in {d: doc for d, _, doc in batch}.items():
                    conn.execute(
                        "INSERT INTO vec_explore_text (doc_id, text_hash) VALUES (?, ?) "
                        "ON CONFLICT(doc_id) DO UPDATE SET text_hash = excluded.text_hash",
                        (doc_id, explore_text_hash(doc)),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        doc_ids.update(d for d, _, _ in batch)

    if _get_meta(conn, _INDEX_MODEL) is None:
        # A store never built at all (no identity, blank or other): only a
        # direct caller's embed reaches here — tests, a one-off script —
        # since every pipeline path builds through rebuild_explore_index,
        # which blanks the identity first. Recorded so such a caller can
        # search what it embedded; a partial or other-model index always
        # carries an identity, and is never recorded here.
        _set_meta(conn, _INDEX_MODEL, index_identity())
    if record_chunks_per_doc:
        _record_chunks_per_doc(conn)

    logger.info(
        "Embedded %d explore documents as %d chunks (%.1f per document)",
        len(doc_ids), len(units), len(units) / len(doc_ids),
    )
    return len(doc_ids)


def _record_chunks_per_doc(conn: sqlite3.Connection) -> None:
    """Mean chunks per document, measured rather than assumed: the search
    path needs it to know how many chunk slots to request for a given
    number of documents. Stored because it is a property of the index and
    recomputing it per query is a COUNT DISTINCT over the whole table."""
    total_chunks = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
    total_docs = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT doc_id FROM vec_explore)"
    ).fetchone()[0]
    if total_docs:
        _set_meta(conn, "explore_chunks_per_doc", str(total_chunks / total_docs))
    else:  # an empty index: no ratio, rather than the last corpus's
        conn.execute("DELETE FROM vec_meta WHERE key = 'explore_chunks_per_doc'")
        conn.commit()


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

    try:
        count = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
    except sqlite3.OperationalError:
        # A model/schema-version bump briefly DROPs and recreates this
        # table (see ensure_explore_index) — search doesn't hold _vec_lock
        # (a read shouldn't block on a rebuild that can take minutes), so
        # a query landing in that brief gap sees "no such table" rather
        # than "0 rows". Same "not ready yet" contract as the empty-index
        # case below, not a real error.
        logger.warning("explore index mid-rebuild — not ready")
        return None
    if count == 0:
        logger.warning("explore index empty — not ready")
        return None
    # Built by another model (a deploy changed it, and the pipeline process —
    # which rebuilds the index — hasn't yet): its vectors don't live in this
    # model's space, and ranking against them would be noise presented as a
    # whole answer. Nor while a rebuild is partway, or after one failed: a
    # few hundred documents are not the index. Not ready, either way.
    if _get_meta(conn, _INDEX_MODEL) != index_identity():
        logger.warning("explore index not a complete build by this model — not ready until it is rebuilt")
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
    recorded = _get_meta(conn, _INDEX_MODEL)  # once: a rebuild may be changing it
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
        "indexModelVersion": recorded or "",
        "chunksPerDocument": float(_get_meta(conn, "explore_chunks_per_doc") or 0.0),
        # "running" (in this process, the pipeline's), "incomplete" (not a
        # complete build by this model — a rebuild left it partway, empty
        # or not, or another model built it: search is off until a rebuild
        # completes), or "" (ready, or never built: nothing to search yet).
        "indexRebuild": (
            "running" if is_rebuilding()
            else "incomplete" if recorded == "" or (explore and recorded != index_identity())
            else ""
        ),
    }


def get_bill_reference(limit: int = 5000):
    """kNN reference corpus: (normalized embeddings ndarray, policy labels)
    from the stored bills, or (None, []) when empty.

    2026-07 fix: this LIMIT used to have no ORDER BY. rowid is a
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


def get_embedded_explore_ids() -> set[int]:
    """Ids of explore documents already in the index (incremental embedding).

    Distinct `doc_id`, not rowid: rows are chunks now, and several of them
    belong to one document.
    """
    conn = get_vec_conn()
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT doc_id FROM vec_explore").fetchall()}


def delete_explore_vectors(doc_ids: set[int] | list[int]) -> int:
    """Drop every chunk belonging to these documents. Returns rows deleted.

    Search reads title/date/snippet straight out of vec_explore's own
    columns rather than joining back to explore_documents, so a vector
    left behind after its row is deleted keeps appearing as a result —
    with metadata nothing can correct. Any code path that deletes an
    ExploreDocument has to come through here too.
    """
    ids = list(doc_ids)
    if not ids:
        return 0
    conn = get_vec_conn()
    removed = 0
    with _vec_lock:
        # Chunked: SQLite caps host parameters per statement, and this is
        # called with whole-corpus-sized id sets during a cleanup sweep.
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = conn.execute(
                f"DELETE FROM vec_explore WHERE doc_id IN ({placeholders})",
                chunk,
            )
            removed += cur.rowcount or 0
            conn.execute(f"DELETE FROM vec_explore_text WHERE doc_id IN ({placeholders})", chunk)
        conn.commit()
    return removed


def get_embedded_text_hashes() -> dict[int, str]:
    """Each embedded document's explore_text_hash, as recorded when its
    vectors were written (none for one embedded before hashes were kept)."""
    return dict(get_vec_conn().execute("SELECT doc_id, text_hash FROM vec_explore_text").fetchall())


def reset_vector_db() -> None:
    """Reset the entire vector index (useful for fresh starts). Waits out a
    rebuild running here: one reset under it would lose the recorded
    identity, and its next batch would record a partial index as built."""
    get_vec_conn()  # the schema exists to be swapped
    with _rebuild_lock:
        _swap_tables({
            "vec_explore": _EXPLORE_DDL.format(if_not_exists=""),
            "vec_explore_text": _TEXT_HASH_DDL.format(if_not_exists=""),
            "vec_bills": _BILLS_DDL.format(if_not_exists=""),
        }, clear_meta=True)
    logger.info("Reset vector DB")


def explore_embed_dict(d) -> dict:
    """An ExploreDocument as embed_explore_documents takes it — the one
    spelling of it, for every path that embeds (a rebuild, the Explore
    run's incremental step)."""
    return {
        "id": d.id, "title": d.title, "summary": d.summary or "",
        "body": d.body or "",
        "doc_type": d.doc_type, "source": d.source or "",
        "date": d.date or "",
        "politician_name": d.politician_name or "",
        "politician_id": d.politician_id or "",
        "chamber": d.chamber or "",
    }


class RebuildFailed(Exception):
    """A rebuild raised after its swap: the old index is gone and the new
    one partial, whatever the cause — a lock included. (One that raises
    before the swap left the index as it was.) The cause is __cause__."""


def alert_rebuild_failed(where: str, error: BaseException) -> None:
    """The one alert for a rebuild that left the index incomplete, from any
    of the three that can (an Explore run, a start, an admin re-embed);
    resolved by the next that completes."""
    from app.ops_alerts import send_ops_alert
    from app.time_utils import utcnow

    cause = error.__cause__ if isinstance(error, RebuildFailed) and error.__cause__ else error
    send_ops_alert(
        "Explore vector index rebuild failed",
        f"The {where}'s rebuild of the search vector index raised ({type(cause).__name__}: {cause}). "
        "Semantic search stays off (keyword-only) until a rebuild completes; the next Explore run or "
        "pipeline start tries again.",
        dedupe_key=f"explore-index-rebuild-{utcnow():%Y-%m-%d}",
        condition="explore-index-rebuild",
    )


def rebuild_explore_index(
    db_session_factory, *, wait: bool = False, if_incomplete: bool = False, unless_rebuilt_since: float | None = None,
) -> int | None:
    """Rebuild the explore index from scratch, in the calling thread: the
    documents embedded, or None when it didn't. Without `wait`, None when a
    rebuild or top-up is already running here; with it, waiting that out.
    With `if_incomplete`, None when the index is (by then) a complete build
    — an Explore run then tops it up, rather than embedding beside it. With
    `unless_rebuilt_since` (a time.monotonic()), None when a rebuild that
    began after it has completed — one this waited out did the work already.

    DROP + recreate, not DELETE FROM: INDEX_SCHEMA_VERSION signals a COLUMN
    LAYOUT change (e.g. adding doc_id when chunking landed), and a vec0
    virtual table's columns are fixed at creation — they can't be ALTERed.
    DELETE FROM only clears rows against whatever schema is already on disk,
    silently keeping a stale pre-migration table forever and failing every
    embed_explore_documents() call against it. Recreating picks up whatever
    _ensure_schema currently defines, so this is correct for a pure
    model-version bump or a plain re-embed too (identical schema either way).
    """
    with rebuild_underway():
        return _rebuild(db_session_factory, wait, if_incomplete, unless_rebuilt_since)


def _rebuild(db_session_factory, wait: bool, if_incomplete: bool, unless_rebuilt_since: float | None) -> int | None:
    global _last_rebuild_began_at
    if not _rebuild_lock.acquire(blocking=wait):
        return None
    try:
        if if_incomplete:
            try:
                whole = index_is_whole()
            except Exception as error:
                if is_busy_error(error):
                    # Can't tell: never a reason to drop an index — the
                    # caller sees the lock, before anything was touched.
                    raise
                # Unreadable is not whole: this rebuild recreates it.
                logger.warning("Explore index unreadable (%s) — rebuilding it", error)
                whole = False
            if whole:
                return None

        if unless_rebuilt_since is not None and _last_rebuild_began_at > unless_rebuilt_since:
            return None
        began = time.monotonic()
        conn = get_vec_conn()
        # Not ready from here until the last batch is in (_INDEX_MODEL):
        # blanked with the swap, so a swap that fails leaves a whole index
        # whole.
        _swap_tables({
            "vec_explore": _EXPLORE_DDL.format(if_not_exists=""),
            "vec_explore_text": _TEXT_HASH_DDL.format(if_not_exists=""),
        }, meta={_INDEX_MODEL: ""})
        try:
            total = _embed_all(db_session_factory)
            _record_chunks_per_doc(conn)  # once, over the finished index
            _set_meta(conn, _INDEX_MODEL, index_identity())
        except Exception as error:
            raise RebuildFailed(f"explore index rebuild failed after its swap: {error}") from error
        _last_rebuild_began_at = began
        logger.info("Explore index rebuild complete: %d documents", total)
        return total
    finally:
        _rebuild_lock.release()


def _embed_all(db_session_factory) -> int:
    """Every document into the (fresh) index, in batches by id."""
    from app.models import ExploreDocument

    db = db_session_factory()
    try:
        total = 0
        after = 0
        while True:
            # By id, not OFFSET: an Explore run may delete documents
            # meanwhile, and an offset would then skip past ones never
            # embedded. One deleted after its batch leaves an orphan
            # vector, which the run's own purge removes.
            docs = (
                db.query(ExploreDocument)
                .filter(ExploreDocument.id > after)
                .order_by(ExploreDocument.id)
                .limit(_REBUILD_BATCH).all()
            )
            if not docs:
                break
            total += embed_explore_documents(
                [explore_embed_dict(d) for d in docs], record_chunks_per_doc=False, fresh=True,
            )
            after = docs[-1].id
    finally:
        db.close()
    return total


def recalibrate_ranking(db_session_factory) -> None:
    """Fit Explore's ranking to the index just rebuilt: runs skip it while
    the index isn't whole (explore_ranking.calibrate_and_store), so the one
    in force was fitted to the index this rebuild replaced."""
    from app.pipeline.explore_ranking import calibrate_and_store

    db = db_session_factory()
    try:
        calibrate_and_store(db)
    finally:
        db.close()


# How often, and how many times, a start's index check waits out a lock.
_BUSY_CHECKS = 10
_BUSY_CHECK_EVERY_S = 30.0


def top_up_explore_index(docs_to_embed, adopt_hashes: dict[int, str] | None = None) -> int:
    """An Explore run's incremental step, under the rebuild lock: a start's
    rebuild waits for it (and then looks again) rather than embed the same
    documents beside it. `docs_to_embed()` is asked under the lock, so what
    it finds missing is what the index lacks then, not before a rebuild
    this waited out. `adopt_hashes` records text hashes for documents
    embedded before hashes were kept, taken as current (their vectors are
    what their text is). A failure leaves each document as it was or as it now
    is (embed_explore_documents writes a batch of whole documents per
    transaction, and a failed batch rolls all of its documents back)."""
    with _rebuild_lock:
        embedded = embed_explore_documents(docs_to_embed())
        if adopt_hashes:
            conn = get_vec_conn()
            with _vec_lock:
                # Only for documents still without one: a rebuild this waited
                # out, or this embed, recorded the real thing.
                # And only for documents still in the index: one deleted
                # meanwhile would leave a hash for vectors that aren't there.
                conn.executemany(
                    "INSERT INTO vec_explore_text (doc_id, text_hash) SELECT ?, ? "
                    "WHERE EXISTS (SELECT 1 FROM vec_explore WHERE doc_id = ?) "
                    "ON CONFLICT(doc_id) DO NOTHING",
                    [(doc_id, digest, doc_id) for doc_id, digest in adopt_hashes.items()],
                )
                conn.commit()
        return embedded


def wait_for_rebuild() -> None:
    """Return once no rebuild is running in this process."""
    with _rebuild_lock:
        pass


def _refit_after_a_start_rebuild(db_session_factory) -> None:
    """The fit in force was measured against the index a start's rebuild
    replaced. Under the Explore lease — a run holding it is mid-ingest (the
    corpus and keyword index moving under a fit), and refits at its own
    end. Refused with neither a run nor a reset holding anything (the
    lease's database was busy a moment), it refits anyway rather than leave
    the old fit in force for a day; when that can't be told either, it
    leaves the fit to the next Explore run rather than take one blind."""
    from app.pipeline import lease

    with lease.job(lease.EXPLORE, who="Explore ranking refit") as held:
        if held:
            recalibrate_ranking(db_session_factory)
            return
    try:
        db = db_session_factory()
        try:
            running = lease.holder(db, lease.EXPLORE) is not None or lease.held(db, lease.DATA_RESET)
        finally:
            db.close()
    except Exception:
        logger.warning("Explore ranking refit skipped — the lease couldn't be read", exc_info=True)
        return
    if not running:
        recalibrate_ranking(db_session_factory)


def is_rebuilding() -> bool:
    """Whether a rebuild of the explore index is underway in this process
    (the pipeline's) — waiting its turn included: check-and-deploy.sh waits
    it out like a run."""
    with _underway_lock:
        return _rebuilds_underway > 0


def index_is_whole() -> bool:
    """Whether the explore index is a complete build by this model — an
    empty corpus's, empty, included: only a completed build records it.
    The table is read too, so one that can't be raises (and is rebuilt)
    rather than pass on its identity alone."""
    conn = get_vec_conn()
    conn.execute("SELECT rowid FROM vec_explore LIMIT 1").fetchall()
    return _get_meta(conn, _INDEX_MODEL) == index_identity()


def ensure_explore_index(db_session_factory) -> None:
    """Rebuild the explore index in the background unless it is a complete
    build by this model — the migration/upgrade path, and the recovery from
    a rebuild that failed or was cut off.

    Called from app startup (main.py lifespan). Runs in a daemon thread
    because re-embedding thousands of documents takes minutes on the Pi;
    search correctly reports "not ready" (None) until it finishes. Takes no
    Explore lease — holding it for twenty minutes would skip an Explore run
    that starts meanwhile — since the rebuild reads documents by id
    (rebuild_explore_index) and one rebuild at a time is the lock's job.
    """
    whole = None
    for attempt in range(_BUSY_CHECKS):
        if attempt:
            time.sleep(_BUSY_CHECK_EVERY_S)
        try:
            whole = index_is_whole()
            break
        except Exception as error:
            if not is_busy_error(error):
                # Unreadable is not whole: the rebuild recreates it.
                logger.warning("Explore index unreadable — rebuilding it", exc_info=True)
                whole = False
                break
            # Locked a moment (a rollout's overlap): no reason to drop an
            # index that may well be whole — nor to leave one that isn't for
            # a day. Looked at again shortly (main runs this on a thread).
            if attempt < _BUSY_CHECKS - 1:
                logger.warning("Explore index busy at start (%s) — checking again", error)
    if whole is None:
        logger.warning("Explore index stayed busy at start — the next Explore run checks it")
        return
    if whole or is_rebuilding():
        return

    def _reindex() -> None:
        # An empty corpus too: its (empty) build is complete, and recording
        # it ends an incomplete index's "incomplete" and its open alert.
        try:
            logger.warning("Explore index not a complete build by %s — rebuilding", index_identity())
            # Waiting out an Explore run's top-up (or its rebuild) rather
            # than embedding beside it; None when that left it whole.
            if rebuild_explore_index(db_session_factory, wait=True, if_incomplete=True) is None:
                return
        except Exception as error:
            if is_busy_error(error):
                logger.warning("Explore index busy — rebuild left to the next Explore run (%s)", error)
                return
            logger.exception("Explore index rebuild failed — not ready until one completes")
            alert_rebuild_failed("pipeline start", error)
            return
        from app.ops_alerts import resolve_ops_alert

        resolve_ops_alert("explore-index-rebuild")  # whole again
        try:
            _refit_after_a_start_rebuild(db_session_factory)
        except Exception:
            logger.exception("Explore ranking refit after the rebuild failed — the next Explore run refits")

    from app.background import WritesHeld

    try:
        start_writer(_reindex, name="explore-reindex")
    except WritesHeld:
        # A data reset began while this looked: it empties the index, and the
        # first Explore run after it builds it.
        logger.info("Explore index rebuild not started — a data reset holds writes; the next Explore run builds it")
