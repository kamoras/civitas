"""Lexical (keyword) index over explore documents — SQLite FTS5 + BM25.

Dense retrieval is the wrong tool for half of what people actually type
into a federal-document search box. A 384-dimensional bi-encoder embeds
"Executive Order 14110" and "Executive Order 13985" to nearly the same
point: the number carries the meaning and the model never saw it. The
same failure covers docket numbers, RINs, agency acronyms, statutory
citations, member surnames, and any term rare enough that the encoder
never learned it. These are precisely the queries where the user knows
exactly what they want, and precisely where similarity search is worst.

Classical inverted-index retrieval is the complement: exact terms, rare
terms, and identifiers are what it is *best* at, and it degrades where
the embedding is strong (paraphrase, synonymy, topical queries). Running
both and fusing them is the standard modern arrangement, and it is why
this module exists rather than a bigger embedding model.

Scoring is Okapi BM25 (Robertson & Spärck Jones 1976; Robertson &
Zaragoza 2009), SQLite's built-in `bm25()` ranking function, with
per-field weights so a query term in a title outranks the same term
buried in the body — the field-weighted variant those authors call
BM25F (§3.2). The weights live in `config_definitions.EXPLORE_FIELD_WEIGHTS`
with everything else tunable about search ranking.

Storage: an FTS5 **external-content** table. `content='explore_documents'`
means the index stores only the inverted lists and reads column values
back from `explore_documents` on demand, so the corpus text is not
duplicated — worth the extra care below on a device where the whole
database shares one SD card with two embedding models.

Sync: AFTER INSERT/UPDATE/DELETE triggers keep the index live between
pipeline runs, and `rebuild_index()` at the end of every ingest run makes
any drift self-healing within a day. External-content FTS5 corrupts
silently if a trigger's 'delete' command is handed values that differ
from what was indexed (the documented hazard), and the ingest pipeline
rewrites bodies in place during backfill — so the nightly rebuild is the
correctness backstop, not housekeeping.

Rows that predate the index are backfilled once, on a background thread
(`_backfill_in_background`). The table and triggers are created
synchronously so no write is ever missed; only the re-tokenising of an
existing corpus is deferred, because doing that inside `init_db()` would
hold up app startup on the one deploy that introduces this index.
"""

from __future__ import annotations

import logging
import re
import threading

from sqlalchemy import text

from app.pipeline.explore_ranking import field_weights, text_shape

logger = logging.getLogger(__name__)

FTS_TABLE = "explore_fts"

# Bump when the FTS schema below changes so deployed databases rebuild
# instead of querying an index whose columns no longer line up.
FTS_SCHEMA_VERSION = "1"

# Sentinels wrapping matched terms in a snippet. Control characters,
# not markup: the API hands snippets to a browser, and `<b>` would either
# have to be escaped (showing users literal tags) or trusted (injecting
# whatever a document body happens to contain). U+0002/U+0003 cannot
# occur in Federal Register text and cannot mean anything to a parser.
HIGHLIGHT_START = "\x02"
HIGHLIGHT_END = "\x03"

# Snippet width and the shortest term worth searching for are both
# properties of the corpus, not choices: the excerpt should show a
# sentence of context, and a term short enough to appear in most documents
# carries almost no retrieval signal (Spärck Jones 1972). Both are measured
# by scripts/calibrate_explore_ranking.py.

_TERM_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z'’.\-]*")
_PHRASE_RE = re.compile(r'"([^"]+)"')


# Set once at startup when the running SQLite has no FTS5 module. Without
# it every single search would attempt a query against a table that cannot
# exist, fail, and log a warning with a traceback — turning a permanent,
# already-reported degradation into per-request log spam.
_index_unavailable = False


def _fts_available(conn) -> bool:
    try:
        conn.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)"))
        conn.execute(text("DROP TABLE IF EXISTS _fts5_probe"))
        return True
    except Exception:
        return False


def ensure_lexical_index(engine) -> bool:
    """Create the FTS5 table and its sync triggers if absent.

    Returns True when the index is usable. Returns False — rather than
    raising — when the running SQLite has no FTS5 module, because search
    still works on the dense channel alone and a keyword index is not
    worth failing app startup over.
    """
    global _index_unavailable
    try:
        with engine.begin() as conn:
            if not _fts_available(conn):
                logger.warning(
                    "SQLite has no FTS5 module — explore search will run "
                    "semantic-only (no keyword channel)"
                )
                _index_unavailable = True
                return False

            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS explore_fts_meta "
                "(key TEXT PRIMARY KEY, value TEXT)"
            ))
            stored = conn.execute(text(
                "SELECT value FROM explore_fts_meta WHERE key = 'schema_version'"
            )).fetchone()

            if stored is not None and stored[0] != FTS_SCHEMA_VERSION:
                logger.info(
                    "Explore FTS schema changed (%s -> %s) — dropping for rebuild",
                    stored[0], FTS_SCHEMA_VERSION,
                )
                _drop(conn)
                stored = None

            existing = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=:n"
            ), {"n": FTS_TABLE}).fetchone()

            needs_backfill = False
            if existing is None:
                logger.info("Creating explore FTS5 index")
                # IF NOT EXISTS because the backend runs --workers 2 in
                # production and each worker process runs its own lifespan,
                # so two of them reach this DDL at once. Without it the
                # loser raises "table already exists", the whole
                # initialisation is caught as a failure, and a worker logs a
                # traceback for a table that is in fact perfectly fine.
                conn.execute(text(
                    f"""CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
                        title, summary, body,
                        content='explore_documents',
                        content_rowid='id',
                        tokenize="unicode61 remove_diacritics 2"
                    )"""
                ))
                _create_triggers(conn)
                # Only rows that predate the index need backfilling, and on
                # a fresh database there are none — every write from here is
                # caught by the triggers. Checking first keeps startup from
                # spawning a thread that has nothing to do, which is the
                # normal case everywhere except the one deploy that
                # introduces this index.
                needs_backfill = bool(conn.execute(text(
                    "SELECT 1 FROM explore_documents LIMIT 1"
                )).fetchone())

            conn.execute(text(
                "INSERT INTO explore_fts_meta (key, value) VALUES ('schema_version', :v) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            ), {"v": FTS_SCHEMA_VERSION})

        if needs_backfill:
            _backfill_in_background(engine)
        return True
    except Exception:
        logger.exception("Could not initialise the explore keyword index")
        return False


def _backfill_in_background(engine) -> None:
    """Tokenise the pre-existing corpus off the startup path.

    The table and triggers are created synchronously above, so every write
    from this moment on is indexed. Only the *backfill* of rows that
    predate the index runs here — re-tokenising the whole corpus takes
    long enough on a Pi to matter, and doing it inside `init_db()` would
    hold up the FastAPI lifespan and the container health check on the one
    deploy that introduces this index.

    Search is already correct while this runs: the keyword channel simply
    returns fewer hits, and the fusion treats that the same as any other
    ranker that didn't return a document. This mirrors how
    `vector_store.ensure_explore_index` handles its own reindex.
    """
    threading.Thread(
        target=_run_backfill, args=(engine,),
        name="explore-fts-backfill", daemon=True,
    ).start()


def _run_backfill(engine) -> None:
    """The backfill itself, separated from the thread so it can be driven
    synchronously in tests."""
    try:
        with engine.begin() as conn:
            conn.execute(text(
                f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')"
            ))
        logger.info("Explore keyword index backfill complete")
    except Exception:
        logger.exception("Explore keyword index backfill failed")


def _drop(conn) -> None:
    for trigger in ("explore_fts_ai", "explore_fts_ad", "explore_fts_au"):
        conn.execute(text(f"DROP TRIGGER IF EXISTS {trigger}"))
    conn.execute(text(f"DROP TABLE IF EXISTS {FTS_TABLE}"))


def _create_triggers(conn) -> None:
    """Keep the index in step with `explore_documents` between rebuilds.

    `coalesce(..., '')` on every column because `summary`/`body` are
    nullable in practice on rows written before their defaults existed,
    and an external-content 'delete' whose values don't match what was
    indexed leaves the index quietly wrong.
    """
    conn.execute(text(
        f"""CREATE TRIGGER IF NOT EXISTS explore_fts_ai
        AFTER INSERT ON explore_documents BEGIN
            INSERT INTO {FTS_TABLE}(rowid, title, summary, body)
            VALUES (new.id, coalesce(new.title,''), coalesce(new.summary,''),
                    coalesce(new.body,''));
        END"""
    ))
    conn.execute(text(
        f"""CREATE TRIGGER IF NOT EXISTS explore_fts_ad
        AFTER DELETE ON explore_documents BEGIN
            INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, summary, body)
            VALUES ('delete', old.id, coalesce(old.title,''),
                    coalesce(old.summary,''), coalesce(old.body,''));
        END"""
    ))
    # `UPDATE OF title, summary, body` — not a bare `AFTER UPDATE`. The
    # nightly authority pass writes `authority`/`cited_by_count` on every
    # row in the corpus, and an unscoped trigger would re-tokenise the
    # entire index as a side effect of updating two numeric columns the
    # index does not contain.
    conn.execute(text(
        f"""CREATE TRIGGER IF NOT EXISTS explore_fts_au
        AFTER UPDATE OF title, summary, body ON explore_documents BEGIN
            INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, summary, body)
            VALUES ('delete', old.id, coalesce(old.title,''),
                    coalesce(old.summary,''), coalesce(old.body,''));
            INSERT INTO {FTS_TABLE}(rowid, title, summary, body)
            VALUES (new.id, coalesce(new.title,''), coalesce(new.summary,''),
                    coalesce(new.body,''));
        END"""
    ))


def rebuild_index(db) -> int:
    """Re-tokenise the whole corpus from `explore_documents`.

    Run at the end of each ingest pass. Returns the number of indexed
    documents, or -1 if the index isn't available.
    """
    try:
        db.execute(text(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')"))
        db.commit()
        count = db.execute(text("SELECT COUNT(*) FROM explore_documents")).scalar()
        logger.info("Rebuilt explore keyword index over %d documents", count or 0)
        return int(count or 0)
    except Exception:
        logger.exception("Explore keyword index rebuild failed")
        db.rollback()
        return -1


# ── Query parsing ────────────────────────────────────────────────

def build_match_expression(query: str) -> str:
    """Turn a raw user query into a safe FTS5 MATCH expression.

    FTS5's MATCH grammar has operators (`AND`, `OR`, `NOT`, `NEAR`, `*`,
    `^`, `:`, parentheses, quotes). A query typed by a member of the
    public is not written in that grammar, and feeding it through raw
    either raises `fts5: syntax error` on an apostrophe or silently
    reinterprets an ordinary word like "not" as an operator. Every term
    is therefore emitted double-quoted, which FTS5 reads as a literal
    string, and the operators between them are ours.

    Double-quoted spans in the user's own query are preserved as phrase
    queries — the one piece of search syntax people genuinely expect to
    work, and the only one this honours.

    Terms are joined with OR rather than AND so a query is never
    all-or-nothing; BM25 does the work of preferring documents that match
    more of the query, and more *discriminating* parts of it, because
    each term's contribution is weighted by its inverse document
    frequency. Returns "" when nothing survives, which callers read as
    "no keyword channel for this query".
    """
    if not query:
        return ""

    parts: list[str] = []
    remainder = query

    for match in _PHRASE_RE.finditer(query):
        phrase_terms = _TERM_RE.findall(match.group(1))
        if phrase_terms:
            parts.append('"' + " ".join(phrase_terms) + '"')
    remainder = _PHRASE_RE.sub(" ", remainder)

    _snippet_tokens, min_term_length = text_shape()
    seen: set[str] = set()
    for term in _TERM_RE.findall(remainder):
        cleaned = term.strip(".-'’")
        if len(cleaned) < min_term_length:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append('"' + cleaned.replace('"', "") + '"')

    return " OR ".join(parts)


def _bm25_weights() -> tuple[float, float, float]:
    weights = field_weights()
    return (
        float(weights["title"]),
        float(weights["summary"]),
        float(weights["body"]),
    )


# ── Search ───────────────────────────────────────────────────────

def search_lexical(
    db,
    query: str,
    limit: int,
    doc_type: str | None = None,
    chamber: str | None = None,
    politician_id: str | None = None,
    commentable_after: str | None = None,
) -> list[dict]:
    """BM25F keyword search, best first.

    Returns dicts with `id`, `bm25` (SQLite's score — negative, lower is
    better) and `snippet` (keyword-in-context, matched terms wrapped in
    HIGHLIGHT_START/END). Returns [] on any failure, including a missing
    FTS5 module: the keyword channel is an enhancement, and a query that
    can't use it should still get semantic results.

    Filters are applied inside the same statement rather than afterwards,
    so `limit` is a limit on *matching, filtered* documents. Filtering
    after the fact is how a chamber-scoped search ends up with three
    results out of a requested thirty.
    """
    if _index_unavailable:
        return []

    match_expr = build_match_expression(query)
    if not match_expr:
        return []

    w_title, w_summary, w_body = _bm25_weights()
    snippet_tokens, _min_term_length = text_shape()

    sql = f"""
        SELECT d.id AS id,
               bm25({FTS_TABLE}, :w_title, :w_summary, :w_body) AS score,
               snippet({FTS_TABLE}, -1, :hl_start, :hl_end, '…', :tokens) AS snip
        FROM {FTS_TABLE}
        JOIN explore_documents d ON d.id = {FTS_TABLE}.rowid
        WHERE {FTS_TABLE} MATCH :match
    """
    params: dict = {
        "match": match_expr, "limit": limit,
        "w_title": w_title, "w_summary": w_summary, "w_body": w_body,
        "hl_start": HIGHLIGHT_START, "hl_end": HIGHLIGHT_END,
        "tokens": snippet_tokens,
    }
    if doc_type:
        sql += " AND d.doc_type = :doc_type"
        params["doc_type"] = doc_type
    if chamber:
        sql += " AND d.chamber = :chamber"
        params["chamber"] = chamber
    if politician_id:
        sql += " AND d.politician_id = :politician_id"
        params["politician_id"] = politician_id
    if commentable_after is not None:
        sql += (
            " AND d.comment_url IS NOT NULL AND d.comment_url != ''"
            " AND d.comments_close_on >= :commentable_after"
        )
        params["commentable_after"] = commentable_after

    sql += " ORDER BY score LIMIT :limit"

    try:
        rows = db.execute(text(sql), params).fetchall()
    except Exception:
        logger.warning("Explore keyword search failed for %r", query, exc_info=True)
        return []

    return [
        {"id": int(row.id), "bm25": float(row.score), "snippet": row.snip or ""}
        for row in rows
    ]
