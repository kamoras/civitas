"""Centralized UTC clock.

`datetime.utcnow()` is deprecated as of Python 3.12 (and slated for removal),
so this module provides the single replacement used across the backend.

It deliberately returns a **naive** UTC datetime — matching the project's
existing convention. Every stored timestamp (the SQLite `DateTime` columns in
`models.py`, the `PipelineRun` bookkeeping) is naive, and every comparison
against those values (`utcnow() - run.started_at`, cache-age math, scheduler
overlap guards) subtracts two naive datetimes. Returning a naive value here
keeps all of that working while dropping the deprecated call.

Do **not** replace this with a piecemeal switch to an aware
`datetime.now(UTC)`: mixing aware and naive datetimes in a subtraction raises
`TypeError`, and SQLite reads a stored datetime back as naive regardless of the
column's declared `tzinfo`, so an aware clock would break the very comparisons
this preserves.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC ``now`` — drop-in replacement for the deprecated ``datetime.utcnow()``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
