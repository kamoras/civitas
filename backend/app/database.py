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

    # Enable WAL mode on SQLite so readers never block on pipeline writes.
    if "sqlite" in settings.DATABASE_URL:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA synchronous=NORMAL"))


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
