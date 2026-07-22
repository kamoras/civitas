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
        ("action_issues", "related_senators", "TEXT DEFAULT '[]'"),
        ("action_issues", "related_monitor_slugs", "TEXT DEFAULT '[]'"),
        ("action_issues", "concerned_count", "INTEGER DEFAULT 0"),
        ("action_issues", "not_priority_count", "INTEGER DEFAULT 0"),
        ("action_issues", "full_story", "TEXT"),
        ("action_issues", "bsky_posted_at", "DATETIME"),
        ("action_issues", "bsky_posted_rank", "INTEGER"),
        ("action_issues", "bsky_last_post_text", "TEXT"),
        ("action_issues", "is_current", "INTEGER DEFAULT 1"),
        ("action_issues", "primary_article_date", "TEXT"),
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
        ("key_votes", "opposing_party_unity_pct", "REAL"),
        ("rep_key_votes", "opposing_party_unity_pct", "REAL"),
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


def _ensure_indexes() -> None:
    """Create indexes on FK columns that may pre-date the index=True addition."""
    desired = [
        ("ix_lobbying_matches_senator_id", "lobbying_matches", "senator_id"),
        ("ix_campaign_promises_senator_id", "campaign_promises", "senator_id"),
        ("ix_sponsored_bills_senator_id", "sponsored_bills", "senator_id"),
        ("ix_sponsored_bills_stage", "sponsored_bills", "stage"),
        ("ix_rep_sponsored_bills_stage", "rep_sponsored_bills", "stage"),
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


def init_db() -> None:
    """Create all tables defined in models and apply lightweight migrations."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    VisitsBase.metadata.create_all(bind=visits_engine)
    _migrate_columns()
    _ensure_indexes()
    _migrate_visits_data_to_own_db()

    # Seed president data if the table is empty
    from app.services.president_service import seed_presidents

    db = SessionLocal()
    try:
        seed_presidents(db)
    finally:
        db.close()


def reset_all_data() -> dict:
    """Drop all pipeline-generated data and start fresh.

    Truncates every table except the schema itself, resets ChromaDB
    collections, and re-seeds static reference data (presidents).
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
            models.JusticeVote,
            models.MonitorUpdate,
            models.NationalMonitor,
            models.TimelineEntry,
            models.LearnedClassification,
            models.ApiCache,
            models.AnalysisCache,
            models.ExploreDocument,
            models.ScoreSnapshot,
            models.PipelineRun,
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

    try:
        from app.pipeline.vector_store import reset_vector_db
        reset_vector_db()
        summary["chromadb_collections"] = 2
    except Exception as exc:
        # Full detail goes to the server log (already unflagged by CodeQL —
        # see error_utils.py's docstring); the admin-facing summary dict
        # gets a static string with zero reference to the exception object,
        # since even a hardcoded-literal classify_exception(exc) call kept
        # getting flagged at this class of sink (see federal_register.py's
        # history for the full trail of what didn't work).
        logger.warning("ChromaDB reset failed: %s", exc)
        summary["chromadb_error"] = "reset failed — see server logs"

    from app.services.president_service import seed_presidents
    db = SessionLocal()
    try:
        seed_presidents(db)
    finally:
        db.close()

    logger.info("Full data reset complete: %s", summary)
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
