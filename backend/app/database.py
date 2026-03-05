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
    """Add any missing nullable columns to existing tables.

    SQLAlchemy's create_all does not ALTER existing tables, so we handle
    lightweight column additions here for development convenience.
    After a full data reset + schema rebuild, this list can be empty.
    """
    inspector = inspect(engine)
    migrations: list[tuple[str, str, str]] = []
    with engine.begin() as conn:
        for table, column, col_type in migrations:
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                logger.info("Adding column %s.%s", table, column)
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


def init_db() -> None:
    """Create all tables defined in models and apply lightweight migrations."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_columns()

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
            models.JusticeVote,
            models.LearnedClassification,
            models.ApiCache,
            models.AnalysisCache,
            models.ExploreDocument,
            models.PipelineRun,
            models.Senator,
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
