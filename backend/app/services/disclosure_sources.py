"""When each financial-disclosure source was last read, and whether its
latest read failed: what the trades and holdings surfaces say beside data
that may have stopped moving (AGENTS.md: an outage is not "no data").

Read from the stock-trades run rows' own step outcomes (each run's
`progress_detail`, ProgressTracker): a step that finished is a read, a
step that failed is a failed read with the kind ProgressTracker recorded
(`failureKind`: "wall" when a bot wall refused the client, else "outage").
Steps that never ran (skipped, pending, killed mid-step) say nothing
either way. Nothing is stored beside the run rows, so a source's history
is the runs' own.

The note is a fixed template around those dates, served only while the
latest read has failed; the first good read clears it.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import StockTradesPipelineRun
from app.schemas import SourceStatusSchema
from app.time_utils import eastern_date


@dataclass(frozen=True)
class _Source:
    # "Senate disclosures last read ..."
    records: str
    # "The Senate's disclosure site has refused ..."
    site: str


_SOURCES = {
    "senate": _Source("Senate disclosures", "The Senate's disclosure site"),
    "house": _Source("House disclosures", "The House Clerk's disclosure site"),
    "president": _Source("The president's disclosures", "The Office of Government Ethics' site"),
}

# The stock-trades step that reads each (filer, record) pair. The Senate's
# scanned pages come from the same eFD session as its annual reports, so
# its holdings step speaks for both.
_STEPS = {
    ("senate", "trades"): "senate_ptr",
    ("house", "trades"): "house_ptr",
    ("president", "trades"): "president_ptr",
    ("senate", "holdings"): "senate_holdings",
    ("house", "holdings"): "house_holdings",
    ("president", "holdings"): "president_holdings",
}

# Runs read back to find a source's last good read: more than a year of
# nightly runs. A source failing longer than that is served as never read.
_RUNS_SCANNED = 400


def _stamp(value: str | None, fallback: datetime | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else fallback
    except ValueError:
        return fallback


def _day(stamp: datetime, *, year: bool = True) -> str:
    d = eastern_date(stamp)
    return f"{d:%b} {d.day}, {d.year}" if year else f"{d:%b} {d.day}"


def note(filer: str, last_read: datetime | None, failing_since: datetime, kind: str) -> str:
    """The one line a page shows while a source's reads fail."""
    source = _SOURCES[filer]
    since = _day(failing_since, year=last_read is None or eastern_date(last_read).year != eastern_date(failing_since).year)
    first = f"{source.records} last read {_day(last_read)}. " if last_read else ""
    if kind == "wall":
        return (f"{first}{source.site} has refused automated reads since {since}; "
                "new filings appear once it allows them again.")
    return (f"{first}Reads of {source.site[0].lower()}{source.site[1:]} have failed since {since}; "
            "new filings appear after the next successful read.")


def source_status(db: Session, filer: str, record: str) -> SourceStatusSchema | None:
    """`filer` "senate", "house" or "president"; `record` "trades" or
    "holdings". None when no run has tried the source yet."""
    step_key = _STEPS[(filer, record)]
    last_read: datetime | None = None
    failing_since: datetime | None = None
    kind: str | None = None
    rows = (
        db.query(StockTradesPipelineRun.started_at, StockTradesPipelineRun.completed_at,
                 StockTradesPipelineRun.progress_detail)
        .order_by(StockTradesPipelineRun.started_at.desc(), StockTradesPipelineRun.id.desc())
        .limit(_RUNS_SCANNED)
    )
    for started_at, completed_at, detail in rows:
        try:
            steps = json.loads(detail) if detail else []
        except ValueError:
            continue
        step = next((s for s in steps if isinstance(s, dict) and s.get("key") == step_key), None)
        if step is None:
            continue
        status = step.get("status")
        if status == "done":
            last_read = _stamp(step.get("completedAt"), completed_at or started_at)
            break
        if status == "failed":
            # Newest first: the first failure seen sets the kind, the last
            # one before a good read is when the failures began.
            kind = kind or step.get("failureKind") or "outage"
            failing_since = _stamp(step.get("completedAt"), completed_at or started_at)
    if last_read is None and failing_since is None:
        return None
    return SourceStatusSchema(
        last_read=last_read.isoformat() if last_read else None,
        failing_since=failing_since.isoformat() if failing_since else None,
        kind=kind,
        note=note(filer, last_read, failing_since, kind) if failing_since else None,
    )
