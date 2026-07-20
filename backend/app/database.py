import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

_sqlite_connect_args: dict = {}
if "sqlite" in settings.DATABASE_URL:
    _sqlite_connect_args = {
        "check_same_thread": False,
        "timeout": 30,  # wait up to 30s for a write lock before raising
    }

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_sqlite_connect_args,
    echo=False,
    pool_pre_ping=True,
)

if "sqlite" in settings.DATABASE_URL:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):
        """Apply WAL mode and performance PRAGMAs to every new pool connection.

        SQLite PRAGMAs are per-connection; setting them only once at init_db
        time leaves connections opened later (e.g. after a pool recycle or in
        a second container) with the default journal_mode=DELETE, which blocks
        concurrent reads during writes and causes 'database is locked' errors.
        """
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-32000")
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
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
        ("site_visits", "browser", "TEXT DEFAULT ''"),
        ("site_visits", "os", "TEXT DEFAULT ''"),
        ("site_visits", "device_type", "TEXT DEFAULT ''"),
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


def init_db() -> None:
    """Create all tables defined in models and apply lightweight migrations."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_columns()
    _ensure_indexes()

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
