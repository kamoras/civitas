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
    """
    inspector = inspect(engine)
    migrations: list[tuple[str, str, str]] = [
        ("key_votes", "affected_industries", "TEXT"),
        ("pipeline_runs", "current_phase", "TEXT"),
        ("pipeline_runs", "senators_total", "INTEGER DEFAULT 0"),
        ("senators", "approval_rating", "REAL"),
        ("senators", "disapproval_rating", "REAL"),
        ("senators", "approval_source", "TEXT"),
        ("presidents", "score_agency_alignment", "REAL DEFAULT 0.0"),
        ("explore_documents", "agency_name", "TEXT"),
        ("explore_documents", "comment_url", "TEXT"),
        ("explore_documents", "comments_close_on", "TEXT"),
        ("learned_classifications", "model_version", "TEXT"),
        ("learned_classifications", "match_metadata", "TEXT"),
        ("senators", "partisan_depth", "TEXT"),
        ("campaign_promises", "party_alignment", "TEXT"),
        ("pipeline_runs", "progress_detail", "TEXT"),
        ("justices", "score_consistency", "REAL DEFAULT 0.0"),
        ("justices", "score_independence", "REAL DEFAULT 0.0"),
        ("justices", "score_bipartisan_agreement", "REAL DEFAULT 0.0"),
        ("justices", "score_judicial_restraint", "REAL DEFAULT 0.0"),
        ("key_votes", "policy_areas", "TEXT DEFAULT '[]'"),
        ("key_votes", "party_alignment_weight", "REAL DEFAULT 0.0"),
    ]
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


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
