import logging
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
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
        ("score_snapshots", "score_5", "REAL DEFAULT 0.0"),
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
    ]

    with engine.begin() as conn:
        for table, column, col_type in additions:
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                logger.info("Adding column %s.%s", table, column)
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))

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
        ("ix_rep_lobbying_matches_rep_id", "rep_lobbying_matches", "representative_id"),
        ("ix_rep_campaign_promises_rep_id", "rep_campaign_promises", "representative_id"),
        ("ix_rep_sponsored_bills_rep_id", "rep_sponsored_bills", "representative_id"),
    ]
    inspector = inspect(engine)
    with engine.begin() as conn:
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

    if "sqlite" in settings.DATABASE_URL:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA synchronous=NORMAL"))
            conn.execute(text("PRAGMA cache_size=-32000"))  # 32MB page cache
            conn.execute(text("PRAGMA mmap_size=268435456"))  # 256MB memory-mapped I/O
            conn.execute(text("PRAGMA temp_store=MEMORY"))

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
            models.RepDonor,
            models.RepIndustryDonation,
            models.RepKeyVote,
            models.RepLobbyingMatch,
            models.RepCampaignPromise,
            models.RepSponsoredBill,
            models.JusticeVote,
            models.MonitorUpdate,
            models.NationalMonitor,
            models.TimelineEntry,
            models.LearnedClassification,
            models.ApiCache,
            models.AnalysisCache,
            models.ExploreDocument,
            models.ScoreSnapshot,
            models.DailyTheme,
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
        logger.warning("ChromaDB reset failed: %s", exc)
        summary["chromadb_error"] = str(exc)

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
