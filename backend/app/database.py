import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# Named (not inline) so callers that need to temporarily override and
# restore a connection's busy-timeout (e.g. api/visits.py's track-visit,
# a best-effort write that shouldn't hold a pool connection for the full
# default wait under contention) have a single source of truth rather
# than a second hardcoded copy of this number that could drift.
SQLITE_BUSY_TIMEOUT_S = 30

def _sqlite_connect_args_for(url: str) -> dict:
    if "sqlite" not in url:
        return {}
    return {
        "check_same_thread": False,
        "timeout": SQLITE_BUSY_TIMEOUT_S,  # wait up to 30s for a write lock before raising
    }


def _derive_visits_database_url(main_url: str) -> str:
    """SiteVisit/PageView (api/visits.py's track-visit — by far the
    highest-frequency write in the app) get their own SQLite file,
    separate from the main database the nightly pipeline writes to.

    2026-07 incident: SQLite allows only one writer at a time even in
    WAL mode, and the nightly pipeline can hold that lock for extended
    stretches while processing a batch between commits. track-visit
    sharing that same file meant a blocked write held a pool connection
    for the full busy-timeout under contention, which exhausted the
    pool and OOM-killed the container. Graceful degradation (see
    api/visits.py) makes that contention survivable; giving these two
    tables their own file — SQLite's writer lock is scoped per-file —
    means the two write patterns physically can't contend at all.

    Only meaningful for SQLite: other backends (e.g. Postgres) handle
    concurrent writers natively via MVCC, so there's no lock to isolate
    and this just returns main_url unchanged.
    """
    if "sqlite" not in main_url:
        return main_url
    if main_url.endswith(":memory:"):
        return main_url  # a second, independent in-memory db is fine
    prefix, _, filename = main_url.rpartition("/")
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        return f"{main_url}_visits"
    return f"{prefix}/{stem}_visits{dot}{ext}"


VISITS_DATABASE_URL = _derive_visits_database_url(settings.DATABASE_URL)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_sqlite_connect_args_for(settings.DATABASE_URL),
    echo=False,
    pool_pre_ping=True,
)
visits_engine = create_engine(
    VISITS_DATABASE_URL,
    connect_args=_sqlite_connect_args_for(VISITS_DATABASE_URL),
    echo=False,
    pool_pre_ping=True,
)


def _set_sqlite_pragmas(dbapi_conn, _connection_record):
    """Apply WAL mode and performance PRAGMAs to every new pool connection.

    SQLite PRAGMAs are per-connection; setting them only once at init_db
    time leaves connections opened later (e.g. after a pool recycle or in
    a second container) with the default journal_mode=DELETE, which blocks
    concurrent reads during writes and causes 'database is locked' errors.
    Shared by both engine and visits_engine below (both sqlite).
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-32000")
    cursor.execute("PRAGMA mmap_size=268435456")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


if "sqlite" in settings.DATABASE_URL:
    event.listens_for(engine, "connect")(_set_sqlite_pragmas)
if "sqlite" in VISITS_DATABASE_URL:
    event.listens_for(visits_engine, "connect")(_set_sqlite_pragmas)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
VisitsSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=visits_engine)


class Base(DeclarativeBase):
    pass


class VisitsBase(DeclarativeBase):
    """Separate declarative base for SiteVisit/PageView — see
    _derive_visits_database_url's docstring for why these two tables are
    physically isolated from everything else."""
    pass


def _migrate_columns() -> None:
    """Align existing tables with current ORM models.

    SQLAlchemy's create_all does not ALTER existing tables, so we handle
    lightweight column additions and legacy column drops here.
    """
    inspector = inspect(engine)
    additions: list[tuple[str, str, str]] = [
        ("candidates", "confirmed_general", "BOOLEAN DEFAULT 0"),
        ("action_issues", "related_senators", "TEXT DEFAULT '[]'"),
        ("action_issues", "related_monitor_slugs", "TEXT DEFAULT '[]'"),
        ("action_issues", "concerned_count", "INTEGER DEFAULT 0"),
        ("action_issues", "not_priority_count", "INTEGER DEFAULT 0"),
        ("action_issues", "full_story", "TEXT"),
        ("action_issues", "bsky_posted_at", "DATETIME"),
        ("action_issues", "bsky_posted_rank", "INTEGER"),
        ("action_issues", "bsky_last_post_text", "TEXT"),
        ("action_issues", "bsky_posted_facts", "TEXT"),
        ("action_issues", "is_current", "INTEGER DEFAULT 1"),
        ("action_issues", "primary_article_date", "TEXT"),
        ("action_issues", "previous_facts", "TEXT DEFAULT '[]'"),
        ("senators", "website_url", "TEXT DEFAULT ''"),
        ("senators", "contact_form_url", "TEXT DEFAULT ''"),
        ("senators", "office_phone", "TEXT DEFAULT ''"),
        ("senators", "office_address", "TEXT DEFAULT ''"),
        ("representatives", "website_url", "TEXT DEFAULT ''"),
        ("representatives", "contact_form_url", "TEXT DEFAULT ''"),
        ("representatives", "office_phone", "TEXT DEFAULT ''"),
        ("representatives", "office_address", "TEXT DEFAULT ''"),
        ("senators", "score_legislative_effectiveness", "REAL DEFAULT 0.0"),
        ("representatives", "score_legislative_effectiveness", "REAL DEFAULT 0.0"),
        ("senators", "score_confidence", "TEXT DEFAULT '{}'"),
        ("representatives", "score_confidence", "TEXT DEFAULT '{}'"),
        ("score_snapshots", "score_5", "REAL DEFAULT 0.0"),
        ("score_snapshots", "algorithm_version", "TEXT"),
        ("pipeline_runs", "ground_truth_failures", "TEXT"),
        ("campaign_promises", "related_bills", "TEXT DEFAULT '[]'"),
        ("rep_campaign_promises", "related_bills", "TEXT DEFAULT '[]'"),
        ("week_summaries", "bsky_posted_at", "DATETIME"),
        ("senators", "bipartisanship_score", "REAL"),
        ("representatives", "bipartisanship_score", "REAL"),
        ("senators", "attracted_bipartisanship_score", "REAL"),
        ("representatives", "attracted_bipartisanship_score", "REAL"),
        ("senators", "is_current", "BOOLEAN DEFAULT 1"),
        ("senators", "vacancy_reason", "TEXT"),
        ("senators", "left_office_date", "TEXT"),
        ("representatives", "is_current", "BOOLEAN DEFAULT 1"),
        ("representatives", "vacancy_reason", "TEXT"),
        ("representatives", "left_office_date", "TEXT"),
        ("house_pipeline_runs", "ground_truth_failures", "TEXT"),
        ("senators", "leadership_title", "TEXT"),
        ("senators", "committees", "TEXT DEFAULT '[]'"),
        ("representatives", "leadership_title", "TEXT"),
        ("representatives", "committees", "TEXT DEFAULT '[]'"),
        ("sponsored_bills", "stage", "TEXT DEFAULT ''"),
        ("rep_sponsored_bills", "stage", "TEXT DEFAULT ''"),
        ("presidents", "gdp_growth_adjusted", "REAL"),
        ("presidents", "rulemaking_count", "INTEGER"),
        ("presidents", "rulemaking_finalized_pct", "REAL"),
        ("senators", "outside_spending_for", "REAL"),
        ("representatives", "outside_spending_for", "REAL"),
        ("lobbying_matches", "is_consensus_vote", "BOOLEAN"),
        ("rep_lobbying_matches", "is_consensus_vote", "BOOLEAN"),
        ("donors", "committee_type", "TEXT"),
        ("rep_donors", "committee_type", "TEXT"),
        ("house_pipeline_runs", "progress_detail", "TEXT"),
        ("supplementary_pipeline_runs", "progress_detail", "TEXT"),
        ("stock_trades_pipeline_runs", "progress_detail", "TEXT"),
        ("stock_trades_pipeline_runs", "president_trades_ingested", "INTEGER DEFAULT 0"),
        ("key_votes", "opposing_party_unity_pct", "REAL"),
        ("rep_key_votes", "opposing_party_unity_pct", "REAL"),
        ("presidents", "election_margin", "REAL"),
        ("presidents", "approval_trend", "REAL"),
        ("presidents", "recent_avg_approval", "REAL"),
        ("presidents", "historical_legacy_score", "INTEGER"),
        ("presidents", "score_historical_legacy", "REAL"),
        ("supplementary_pipeline_runs", "committee_leadership_refreshed", "BOOLEAN DEFAULT 0"),
        ("supplementary_pipeline_runs", "committee_leadership_skipped", "BOOLEAN DEFAULT 0"),
        ("supplementary_pipeline_runs", "district_pvi_refreshed", "BOOLEAN DEFAULT 0"),
        ("supplementary_pipeline_runs", "district_pvi_skipped", "BOOLEAN DEFAULT 0"),
        # Citation-graph ranking inputs for explore search. Defaults make an
        # un-migrated corpus rank exactly as it did before: authority 0 for
        # everyone means no document is eligible for the authority signal,
        # so the ranker falls back to relevance + freshness until the first
        # pipeline run fills these in.
        ("explore_documents", "identifiers", "TEXT DEFAULT '[]'"),
        ("explore_documents", "authority", "REAL DEFAULT 0.0"),
        ("explore_documents", "cited_by_count", "INTEGER DEFAULT 0"),
    ]

    drops: list[tuple[str, str]] = [
        ("key_votes", "impacted_groups"),
        ("key_votes", "classification"),
        ("key_votes", "corporate_interest"),
        ("key_votes", "public_impact"),
        ("key_votes", "relevant_donors"),
        ("key_votes", "relevant_donor_total"),
        ("key_votes", "stance_vote"),
        ("key_votes", "pro_business_vote"),
        ("key_votes", "affected_industries"),
        ("senators", "punk_nickname"),
        # 2026-07 incident: #215 removed Independence/Follow-Through from
        # the President model, but never added the matching drops here —
        # the two columns stayed in the live schema as NOT NULL, so any
        # INSERT built from the current (post-#215) seed_presidents code,
        # which no longer supplies them, violated the constraint. Harmless
        # on a database that already has presidents rows (this only
        # matters for a fresh INSERT), but fatal — a startup-time crash,
        # not just a failed request — the moment presidents ever needs to
        # re-seed: confirmed live when the table had been emptied by an
        # unrelated reset_all_data() call and a routine backend restart
        # then crash-looped on this exact constraint instead of reseeding.
        ("presidents", "score_independence"),
        ("presidents", "score_follow_through"),
        # 2026-07 (#218 review): defensive backstop for the same four
        # legacy columns _migrate_presidents_schema_rebuild handles via a
        # full table rebuild (see that function's docstring for why a
        # rebuild, not a plain drop, is the primary mechanism — these
        # entries only matter if that rebuild's trigger condition is ever
        # bypassed, e.g. a hand-edited schema missing score_competence).
        ("presidents", "score_competence"),
        ("presidents", "eo_court_success_pct"),
        ("presidents", "cabinet_turnover_pct"),
        ("presidents", "summary"),
        ("presidents", "key_achievements"),
        ("presidents", "key_failures"),
    ]

    with engine.begin() as conn:
        for table, column, col_type in additions:
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                logger.info("Adding column %s.%s", table, column)
                try:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                except Exception as exc:
                    if "duplicate column name" in str(exc).lower():
                        pass  # another container added the column concurrently
                    else:
                        raise

        for table, column in drops:
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                logger.info("Dropping legacy column %s.%s", table, column)
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))

    # Deliberately its own transaction, after the DDL block above has
    # committed. pysqlite implicitly commits around DDL, so a data write
    # sharing that block is not reliably part of the same commit — the
    # ALTER TABLEs landed and this UPDATE silently did not.
    _backfill_bsky_posted_facts()


def _backfill_bsky_posted_facts() -> None:
    """Seed action_issues.bsky_posted_facts for rows that were already
    posted to Bluesky before the column existed (#310).

    #310 made the repost gate measure new facts against the facts as of
    the last post (bsky_posted_facts) instead of the live `facts` column,
    which every hourly refresh overwrites. Rows predating the column have
    it NULL, and the gate falls back to `match.facts` for those — the
    ratcheting baseline #310 exists to eliminate.

    That fallback cannot resolve itself. bsky_posted_facts is only ever
    written by the Bluesky poster, the poster only sees issues whose
    bsky_posted_at is NULL, and for an already-posted issue only the
    repost gate can clear that — the same gate that is stuck on the old
    behavior. So every ongoing story carried across the migration keeps
    the pre-#310 ratchet indefinitely, and those are exactly the rows the
    fix was written for.

    Seeding the baseline to the current `facts` breaks the deadlock
    without the repost burst the NULL fallback was protecting against:
    the pinned baseline equals what the gate would have compared against
    on the very next run anyway, so nothing reads as newly-new. It just
    stops moving from then on.

    Safe to run on every startup rather than only when the column is
    added, which is why it isn't gated on that: the poster writes
    bsky_posted_at and bsky_posted_facts in the same commit, so a row
    with the former set and the latter NULL can only be one that predates
    the column. Once seeded it stops matching, and a baseline the poster
    has since advanced is never re-pinned. Running unconditionally also
    means a process that dies between the ALTER and this UPDATE — the
    ALTER having already committed, per the note above — repairs itself
    on the next boot instead of silently keeping the old behavior
    forever. test_bluesky_poster pins the write-both invariant this
    relies on.
    """
    if not inspect(engine).has_table("action_issues"):
        return
    with engine.begin() as conn:
        result = conn.execute(text(
            "UPDATE action_issues SET bsky_posted_facts = facts "
            "WHERE bsky_posted_at IS NOT NULL AND bsky_posted_facts IS NULL"
        ))
    if result.rowcount:
        logger.info(
            "Backfilled bsky_posted_facts for %d previously-posted action issue(s)",
            result.rowcount,
        )


def _migrate_presidents_schema_rebuild() -> None:
    """#218 makes score_public_mandate/score_effectiveness/
    score_agency_alignment nullable (previously NOT NULL DEFAULT 0.0) and
    drops score_competence/summary/key_achievements/key_failures
    entirely. SQLite has no ALTER COLUMN to relax a NOT NULL constraint,
    so an existing database still on the old schema would otherwise raise
    IntegrityError the moment the pipeline writes a legitimate None to
    one of those three now-nullable columns, or _sync_roster inserts a
    new president row without the four now-removed ones. Every
    president's scores are recomputed nightly from live sources — there
    is no user-entered data in this table worth preserving through a
    migration — so the simplest correct fix is to drop the whole table
    when the old shape is detected and let the next run_president_
    pipeline call repopulate it from scratch.

    Must run before create_all (which only creates tables that don't
    already exist) and after _migrate_president_ids (so a pending
    bush-41 rename isn't skipped by an empty table — see that function).
    """
    inspector = inspect(engine)
    if not inspector.has_table("presidents"):
        return
    existing = {c["name"] for c in inspector.get_columns("presidents")}
    if "score_competence" not in existing:
        return  # already on the current schema
    logger.warning(
        "Legacy presidents schema detected (score_competence still present) — "
        "dropping table for a full rebuild by the next president pipeline run"
    )
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE presidents"))


def _migrate_president_ids() -> None:
    """One-off id rename: George H.W. Bush's president_id changes from
    "bush-41" to "ghwbush-41" (2026-07).

    Every new UCSB-derived fetcher this platform's president pipeline
    uses (historical_executive_orders.py, presidential_elections.py,
    presidential_approval.py) — and economic_data.py, which pre-dates
    this rewrite — all independently converged on "ghwbush-41" to avoid
    ambiguity with George W. Bush's "gwbush-43". Only the now-removed
    hand-typed SEED_PRESIDENTS list used "bush-41". Without this rename,
    a production database seeded under the old id would get a second,
    duplicate president row from _sync_roster (president_pipeline.py)
    instead of updating the existing one, and orphan that row's score
    history. Idempotent and a no-op on a fresh database that never had
    "bush-41" to begin with.
    """
    inspector = inspect(engine)
    if not inspector.has_table("presidents"):
        return
    with engine.begin() as conn:
        old_exists = conn.execute(
            text("SELECT 1 FROM presidents WHERE id = 'bush-41'"),
        ).first()
        if not old_exists:
            return
        new_exists = conn.execute(
            text("SELECT 1 FROM presidents WHERE id = 'ghwbush-41'"),
        ).first()
        if new_exists:
            # Both rows exist (shouldn't happen outside a bad manual
            # edit) — drop the stale one rather than guess which to keep.
            logger.warning("Both bush-41 and ghwbush-41 exist — merging bush-41's score history into ghwbush-41 and dropping the stale row")
            conn.execute(text("DELETE FROM presidents WHERE id = 'bush-41'"))
            if inspector.has_table("score_snapshots"):
                # Move any bush-41 snapshot whose date doesn't already have
                # a ghwbush-41 row (2026-07 fix: this branch used to drop
                # bush-41's score_snapshots rows entirely, permanently
                # orphaning that trend data — the single-row rename path
                # below already migrates them, this branch just hadn't). A
                # date with both is a genuine duplicate, dropped under
                # bush-41 afterward.
                conn.execute(text(
                    "UPDATE score_snapshots SET entity_id = 'ghwbush-41' "
                    "WHERE entity_type = 'president' AND entity_id = 'bush-41' "
                    "AND date NOT IN ("
                    "  SELECT date FROM score_snapshots "
                    "  WHERE entity_type = 'president' AND entity_id = 'ghwbush-41'"
                    ")"
                ))
                conn.execute(text(
                    "DELETE FROM score_snapshots WHERE entity_type = 'president' AND entity_id = 'bush-41'"
                ))
            return
        logger.info("Renaming president id bush-41 -> ghwbush-41")
        conn.execute(text("UPDATE presidents SET id = 'ghwbush-41' WHERE id = 'bush-41'"))
        if inspector.has_table("score_snapshots"):
            conn.execute(text(
                "UPDATE score_snapshots SET entity_id = 'ghwbush-41' "
                "WHERE entity_type = 'president' AND entity_id = 'bush-41'"
            ))


def _ensure_indexes() -> None:
    """Create indexes on FK columns that may pre-date the index=True addition."""
    desired = [
        ("ix_lobbying_matches_senator_id", "lobbying_matches", "senator_id"),
        ("ix_campaign_promises_senator_id", "campaign_promises", "senator_id"),
        ("ix_sponsored_bills_senator_id", "sponsored_bills", "senator_id"),
        ("ix_sponsored_bills_stage", "sponsored_bills", "stage"),
        ("ix_rep_sponsored_bills_stage", "rep_sponsored_bills", "stage"),
        # get_bill_detail (bill_service.py) looks bills up by bill_id; both
        # tables previously scanned ~8-9k rows per detail-page hit.
        ("ix_sponsored_bills_bill_id", "sponsored_bills", "bill_id"),
        ("ix_rep_sponsored_bills_bill_id", "rep_sponsored_bills", "bill_id"),
    ]
    # The rep_* models' representative_id column already has index=True,
    # which SQLAlchemy names ix_{table}_representative_id — this list used
    # to also request ix_{table}_rep_id for the same column under an older
    # naming convention, creating a second, genuinely redundant index on
    # every affected table (2026-07 audit: confirmed via PRAGMA index_list
    # on rep_lobbying_matches, rep_campaign_promises, rep_sponsored_bills —
    # each had two separately-named indexes covering the identical column).
    # Drop the old-named duplicates once; don't recreate them.
    legacy_duplicate_indexes = [
        "ix_rep_lobbying_matches_rep_id",
        "ix_rep_campaign_promises_rep_id",
        "ix_rep_sponsored_bills_rep_id",
    ]
    inspector = inspect(engine)
    with engine.begin() as conn:
        for idx_name in legacy_duplicate_indexes:
            conn.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))
        for idx_name, table, column in desired:
            if not inspector.has_table(table):
                continue
            existing = {idx["name"] for idx in inspector.get_indexes(table)}
            if idx_name not in existing:
                logger.info("Creating index %s on %s(%s)", idx_name, table, column)
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})"
                ))

        # At most ONE running row per pipeline-run table, enforced by the
        # database (2026-07, platform-review O15): _acquire_pipeline_lock
        # was check-then-insert with no constraint, so its docstring's
        # atomicity claim didn't hold — two containers hitting the 03:00
        # tick during a blue/green overlap could both pass the check and
        # both start a full pipeline. A partial UNIQUE index turns the
        # second insert into an IntegrityError the acquirer handles
        # (insert-then-catch), which is race-free without transaction-
        # isolation gymnastics. Partial (WHERE status = 'running') so the
        # unbounded history of completed/failed/stale rows is unaffected.
        #
        # Only pipeline_runs (Senate) had this until 2026-07-23 — House,
        # Stock, and Supplementary had no equivalent protection AND no
        # stale-row auto-clear (run_tracker.acquire_pipeline_lock, added
        # the same day), so a row orphaned by a killed process (a deploy
        # restarting the container mid-run) stayed "running" forever,
        # silently blocking every future run of that pipeline. Confirmed
        # live: this left stock-trades data stale 4+ days and
        # supplementary data stale 1+ day after a since-fixed deploy-race
        # incident (check-and-deploy.sh) killed pipelines mid-run.
        for table, index_name in (
            ("pipeline_runs", "ux_pipeline_runs_one_running"),
            ("house_pipeline_runs", "ux_house_pipeline_runs_one_running"),
            ("stock_trades_pipeline_runs", "ux_stock_trades_pipeline_runs_one_running"),
            ("supplementary_pipeline_runs", "ux_supplementary_pipeline_runs_one_running"),
            ("election_pipeline_runs", "ux_election_pipeline_runs_one_running"),
        ):
            if inspector.has_table(table):
                conn.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table} (status) WHERE status = 'running'"
                ))

        # One coverage item per (race, url), enforced by the database
        # (2026-07 review B3): ingestion is check-then-insert, and the
        # 15-minute election-season refresh + nightly pipeline could
        # otherwise both pass the check during an overlap window. The
        # model declares the same UniqueConstraint for fresh installs;
        # this covers a database whose table predates the constraint
        # (create_all never ALTERs existing tables — see this function's
        # docstring).
        if inspector.has_table("race_coverage_items"):
            # Dedupe first (keep the earliest row) — a pre-constraint
            # database may hold duplicates from exactly the concurrency
            # window this index closes, and CREATE UNIQUE INDEX on a
            # table with duplicates fails, which would wedge startup.
            conn.execute(text(
                "DELETE FROM race_coverage_items WHERE id NOT IN "
                "(SELECT MIN(id) FROM race_coverage_items GROUP BY race_id, url)"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_race_coverage_race_url "
                "ON race_coverage_items (race_id, url)"
            ))

        # One vote per (justice, case), enforced by the database (2026-08
        # audit): upsert_justice's delete-then-recreate had no protection
        # against a single Oyez case carrying more than one `decisions`
        # entry (justice_votes.fetch_case_votes flattened all of them),
        # so an affected case wrote 2 rows per justice — see JusticeVote's
        # docstring. The model declares the same UniqueConstraint for
        # fresh installs; this covers a database whose table predates it.
        if inspector.has_table("justice_votes"):
            # Dedupe first — a pre-constraint database may hold duplicate
            # rows from exactly this bug, and CREATE UNIQUE INDEX on a
            # table with duplicates fails, which would wedge startup.
            # Keep MIN(id) (arbitrary but consistent): the two rows can
            # correspond to genuinely different real decisions in the
            # same case (e.g. a "dismissal - improvidently granted" and a
            # separate "per curiam" order), and this schema has no way to
            # keep both, so there is no objectively "correct" survivor.
            conn.execute(text(
                "DELETE FROM justice_votes WHERE id NOT IN "
                "(SELECT MIN(id) FROM justice_votes GROUP BY justice_id, case_id)"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_justice_vote_justice_case "
                "ON justice_votes (justice_id, case_id)"
            ))


def _migrate_visits_data_to_own_db() -> None:
    """One-time copy of any pre-split SiteVisit/PageView rows out of the
    main database into their new dedicated file (see
    _derive_visits_database_url's docstring). Only does anything on an
    existing deployment that had these tables in the main engine before
    2026-07; a fresh install never has anything to copy. Idempotent: once
    the visits engine has rows, this is a no-op on every later restart,
    so it's safe to leave in permanently rather than treat as a one-shot
    script to run and remove.
    """
    main_inspector = inspect(engine)
    if not main_inspector.has_table("site_visits") and not main_inspector.has_table("page_views"):
        return  # already migrated (or a fresh install) — old table is gone

    with visits_engine.begin() as visits_conn:
        already_migrated = visits_conn.execute(
            text("SELECT COUNT(*) FROM site_visits")
        ).scalar() or visits_conn.execute(
            text("SELECT COUNT(*) FROM page_views")
        ).scalar()
        if already_migrated:
            return

        with engine.begin() as main_conn:
            if main_inspector.has_table("site_visits"):
                rows = main_conn.execute(text("SELECT * FROM site_visits")).mappings().all()
                for row in rows:
                    visits_conn.execute(
                        text(
                            "INSERT OR IGNORE INTO site_visits "
                            "(date, visitor_hash, browser, os, device_type) "
                            "VALUES (:date, :visitor_hash, :browser, :os, :device_type)"
                        ),
                        dict(row),
                    )
            if main_inspector.has_table("page_views"):
                rows = main_conn.execute(text("SELECT * FROM page_views")).mappings().all()
                for row in rows:
                    visits_conn.execute(
                        text(
                            "INSERT OR IGNORE INTO page_views (date, path, count) "
                            "VALUES (:date, :path, :count)"
                        ),
                        dict(row),
                    )
    logger.info("Migrated SiteVisit/PageView data to their own database file")


def _init_lock_path() -> str | None:
    """Path of the cross-process lock file guarding init_db, or None.

    Derived from the database file so every process pointed at the same
    database serialises on the same lock, and two stacks pointed at
    different databases never block each other. None for in-memory or
    non-SQLite URLs, where there is nothing to serialise (an in-memory
    database is private to its process).
    """
    url = settings.DATABASE_URL
    if "sqlite" not in url or url.endswith(":memory:"):
        return None
    path = url.split("sqlite:///", 1)[-1].lstrip("/")
    return "/" + path + ".init.lock" if url.startswith("sqlite:////") else path + ".init.lock"


@contextmanager
def _init_lock():
    """Serialise init_db across worker processes.

    The backend runs `--workers 2` in production and each worker process
    runs its own FastAPI lifespan, so two of them call init_db at the same
    moment. Every step inside is check-then-act — `create_all` inspects
    `sqlite_master` before issuing CREATE TABLE, `_migrate_columns`
    inspects columns before ALTER, `_ensure_indexes` inspects indexes
    before CREATE INDEX — so two processes can both observe "absent" and
    both issue the DDL. The loser gets "table X already exists" and its
    whole initialisation aborts partway through, leaving migrations and
    the keyword index unapplied in that worker while the other worker
    reports a clean start.

    An advisory `flock` is the right shape here: the workers share a host
    and a filesystem (single-node Swarm, one volume), it costs nothing on
    the uncontended path, and the loser simply waits and then runs the
    same idempotent steps, finding everything already done.

    Never fails startup on the lock itself. If the lock file cannot be
    created — a read-only mount, a permissions problem — this logs and
    proceeds unlocked, which is exactly the behaviour that existed before.
    """
    import fcntl

    path = _init_lock_path()
    if path is None:
        yield
        return

    handle = None
    try:
        handle = open(path, "w")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError:
        logger.warning(
            "Could not acquire the init lock at %s — initialising unlocked. "
            "Concurrent worker startups may log spurious 'already exists' errors.",
            path, exc_info=True,
        )
        if handle is not None:
            handle.close()
            handle = None

    try:
        yield
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def init_db() -> None:
    """Create all tables defined in models and apply lightweight migrations.

    Serialised across worker processes — see `_init_lock`. Every step below
    is idempotent, so the process that waits for the lock still runs them
    all and simply finds the work already done.
    """
    with _init_lock():
        _init_db_locked()


def _init_db_locked() -> None:
    from app import models  # noqa: F401

    _migrate_president_ids()
    _migrate_presidents_schema_rebuild()
    Base.metadata.create_all(bind=engine)
    VisitsBase.metadata.create_all(bind=visits_engine)
    _migrate_columns()
    _ensure_indexes()
    _migrate_visits_data_to_own_db()

    # The FTS5 keyword index over explore_documents, plus the triggers that
    # keep it in step with ordinary ORM writes. Created here rather than in
    # the pipeline because the triggers have to exist *before* the next
    # insert, not after the run that would have rebuilt the index. Returns
    # False (never raises) when SQLite has no FTS5 module — explore search
    # then runs semantic-only, which is exactly what it did before.
    from app.pipeline.lexical_index import ensure_lexical_index

    ensure_lexical_index(engine)

    # President rows are no longer seeded here — run_president_pipeline
    # (president_pipeline.py) creates/updates them from a live UCSB
    # roster fetch on every run, so a fresh database is populated by that
    # pipeline's first pass instead of a separate hand-typed seed step
    # (2026-07, see president_pipeline.py's module docstring).


def reset_all_data() -> dict:
    """Drop all pipeline-generated data and start fresh.

    Truncates every table except the schema itself, resets the vector
    store's collections, and re-seeds static reference data (presidents).
    Returns a summary of what was cleared.
    """
    from app import models  # noqa: F401

    summary: dict[str, int] = {}
    db = SessionLocal()
    try:
        for model_cls in [
            models.Donor,
            models.IndustryDonation,
            models.KeyVote,
            models.LobbyingMatch,
            models.CampaignPromise,
            models.SponsoredBill,
            models.StockTrade,
            models.RepDonor,
            models.RepIndustryDonation,
            models.RepKeyVote,
            models.RepLobbyingMatch,
            models.RepCampaignPromise,
            models.RepSponsoredBill,
            models.RepStockTrade,
            # Before models.President below — the delete order here is
            # child-then-parent throughout, and a president row's cascade
            # would otherwise take these with it uncounted.
            models.PresidentTrade,
            models.JusticeVote,
            models.MonitorUpdate,
            models.NationalMonitor,
            models.TimelineEntry,
            models.LearnedClassification,
            models.ApiCache,
            models.AnalysisCache,
            models.ExploreDocument,
            models.ScoreSnapshot,
            # Before models.Senator/Representative: a fingerprint left
            # behind after its member row is deleted would let the next
            # incremental run skip re-deriving a member that no longer
            # exists, so a reset would silently not rebuild it.
            models.MemberAnalysisFingerprint,
            models.PipelineRun,
            # Election-cycle tables. These were omitted when the
            # midterm-elections feature landed (2026-07), so an admin
            # reset silently left the candidate roster, race coverage and
            # run history behind while reporting a clean wipe — the same
            # class of omission the drops list above was bitten by in
            # #215. Child-then-parent, like every other pair here:
            # Candidate/RaceCoverageItem cascade from Race.
            models.Candidate,
            models.RaceCoverageItem,
            models.Race,
            models.BallotMeasure,
            models.MeasureCoverage,
            models.ElectionPipelineRun,
            models.Senator,
            models.Representative,
            models.Justice,
            models.President,
        ]:
            table = model_cls.__tablename__
            count = db.query(model_cls).count()
            summary[table] = count
            db.query(model_cls).delete()
        db.commit()
    finally:
        db.close()

    # The FTS5 delete triggers fire per row on the bulk deletes above, so the
    # keyword index is already empty — this is the belt-and-braces rebuild
    # that makes that an invariant rather than a property of how SQLite
    # happens to run DELETE.
    try:
        from app.pipeline.lexical_index import rebuild_index

        rebuild_session = SessionLocal()
        try:
            rebuild_index(rebuild_session)
        finally:
            rebuild_session.close()
    except Exception:
        logger.exception("Keyword index rebuild after reset failed (non-fatal)")

    try:
        from app.pipeline.vector_store import reset_vector_db
        reset_vector_db()
        summary["vector_db_collections"] = 2
    except Exception as exc:
        # Full detail goes to the server log (already unflagged by CodeQL —
        # see error_utils.py's docstring); the admin-facing summary dict
        # gets a static string with zero reference to the exception object,
        # since even a hardcoded-literal classify_exception(exc) call kept
        # getting flagged at this class of sink (see federal_register.py's
        # history for the full trail of what didn't work).
        logger.warning("Vector DB reset failed: %s", exc)
        summary["vector_db_error"] = "reset failed — see server logs"

    logger.info("Full data reset complete: %s", summary)
    # President rows will be recreated by the next run_president_pipeline
    # run (live UCSB roster fetch) — see init_db's comment above.
    return summary


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_visits_db() -> Generator[Session, None, None]:
    """FastAPI dependency for SiteVisit/PageView's dedicated database —
    see VisitsBase/_derive_visits_database_url. Used by the read-only
    admin visitor-stats endpoints; api/visits.py's track_visit uses its
    own _get_db_or_none instead, since that write path also needs to
    degrade gracefully on pool exhaustion rather than raise."""
    db = VisitsSessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """`with`-based session for non-request code (pipeline stages, scripts).

    Guarantees the session is closed even on an early return or exception —
    the drop-in for the hand-rolled ``SessionLocal()`` / ``try`` / ``finally:
    db.close()`` blocks. Does not auto-commit; callers commit explicitly, as
    those blocks did.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
