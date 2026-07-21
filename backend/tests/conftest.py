"""Shared test fixtures."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base


@pytest.fixture()
def db_session():
    """In-memory SQLite session for testing the learning store.

    StaticPool + check_same_thread=False: SQLAlchemy's default SQLite pool
    hands each thread its own connection, which for a `:memory:` database
    means each thread sees a separate, empty database. classify_donors_hybrid
    now runs its sync body via asyncio.to_thread (see donor_classifier_ai.py),
    so a session used across that boundary needs the single shared
    connection StaticPool provides — the same fix production doesn't need
    since a file-backed SQLite DB is the same database regardless of which
    thread opens the connection.
    """
    engine = create_engine(
        "sqlite:///:memory:", echo=False,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()
