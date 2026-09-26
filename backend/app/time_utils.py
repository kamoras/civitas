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
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Naive UTC ``now`` — drop-in replacement for the deprecated ``datetime.utcnow()``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Federal comment periods close at 11:59 PM Eastern on the Federal
# Register's `comments_close_on` date — Regulations.gov shows every docket's
# deadline that way ("Comments Due ... 11:59 PM ET"). So "is this period still
# open" is a question about the calendar date in Eastern time, not UTC and not
# the reader's own zone: a UTC date closed every period ~4-5 hours early (the
# final evening, when late comments actually arrive), while a Pacific
# visitor's local date kept it open ~3 hours after it had shut.
COMMENT_DEADLINE_TZ = ZoneInfo("America/New_York")


def comment_period_today() -> str:
    """Today's date in the comment-deadline zone, as ``YYYY-MM-DD``.

    A period is open while ``comments_close_on >= comment_period_today()``.
    Compare against this — never ``date.today()`` or ``utcnow().date()``.
    """
    return datetime.now(COMMENT_DEADLINE_TZ).date().isoformat()
