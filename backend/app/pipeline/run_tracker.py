import logging
import time
from datetime import timedelta
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_RunModel = TypeVar("_RunModel")

# Shared stale-run threshold for acquire_pipeline_lock's callers other than
# Senate (which keeps its own STALE_PIPELINE_TIMEOUT_S in senate_pipeline.py —
# same 12h value, not re-derived from this constant, to avoid disturbing
# that module's existing behavior for an unrelated refactor). 12h matches
# the "definitely wedged, not just slow" bar already established there;
# distinct from and longer than the 2h/8h thresholds _hourly_action_refresh
# uses in scheduler.py, which answer a different question (should THIS
# hourly tick wait or proceed) than "should this row be marked failed."
STALE_PIPELINE_TIMEOUT = timedelta(hours=12)


def acquire_pipeline_lock(db: Session, model: type[_RunModel], stale_timeout: timedelta) -> "_RunModel | None":
    """Atomically create a new locked run of `model`, auto-clearing a
    stale leftover RUNNING row first. Returns None if a genuinely still-
    active (non-stale) run already holds the lock.

    Generalizes senate_pipeline.py's original _acquire_pipeline_lock
    (2026-07, platform-review O15) to House/Stock/Supplementary, which
    had no equivalent protection at all until 2026-07-23: no unique
    index (a real cross-container double-start race, not just a
    theoretical one — see database._ensure_indexes, where only
    pipeline_runs had the partial UNIQUE index this relies on), and no
    stale-row auto-clear, so a row orphaned by a killed process (a
    deploy restarting the container mid-run) stayed RUNNING forever —
    blocking every future run of that pipeline, and every future run of
    anything that treats it as "another pipeline busy"
    (stock_pipeline.py's _other_pipeline_running) — until a human
    noticed and used the manual admin "clear stuck" endpoint. Confirmed
    live: this is what left stock-trades data stale for 4+ days and
    supplementary data stale for 1+ day after a deploy-race incident
    (check-and-deploy.sh, fixed the same day) killed pipelines mid-run.

    ``model`` must have ``status``/``started_at``/``completed_at``/
    ``error_message`` columns (PipelineRun/HousePipelineRun/
    StockTradesPipelineRun/SupplementaryPipelineRun all do) and a partial
    UNIQUE index on ``status`` WHERE ``status = 'running'`` for the
    atomicity guarantee to actually hold across processes — without that
    index this still auto-clears stale rows correctly, but a genuine
    same-instant race between two containers could both pass the check
    (see database._ensure_indexes for where each table's index lives).
    """
    from app.models import PipelineStatus

    running = db.query(model).filter(model.status == PipelineStatus.RUNNING).first()
    if running:
        age = utcnow() - running.started_at
        if age > stale_timeout:
            running.status = PipelineStatus.STALE
            running.completed_at = utcnow()
            running.error_message = f"Marked stale: exceeded {stale_timeout} timeout"
            db.commit()
            logger.warning(
                "Cleaned up stale %s run #%d (age: %s)", model.__name__, running.id, age,
            )
        else:
            return None

    run = model(started_at=utcnow(), status=PipelineStatus.RUNNING)
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        # Another container inserted its running row between our check
        # and our commit — it holds the lock.
        db.rollback()
        logger.info("%s lock held by another container — skipping this run", model.__name__)
        return None
    return run


class PipelineRunTracker:
    """In-process running/age tracker for a pipeline that also persists
    its status to a DB row (HousePipelineRun/StockTradesPipelineRun).

    Exists alongside the DB row, not instead of it: a crashed/killed
    process can never update its own DB row to "failed", so callers (the
    admin dashboard, the hourly action-center refresh) use this flag to
    detect a run that's still marked "running" in the DB but is no
    longer actually alive in this process — see house_pipeline.py's
    2026-07-04 wedge, where a run held the DB "running" status for 17h
    with no way to distinguish live-but-slow from dead-but-stuck other
    than this in-memory flag.

    house_pipeline.py and stock_pipeline.py each independently
    duplicated an identical pair of module-level globals plus getter
    functions before this extraction. senate_pipeline.py does NOT use
    this pattern — it tracks state via the PipelineRun DB row directly,
    so it has no tracker instance.

    Not thread-safe by design: each pipeline runs in at most one
    dedicated background thread at a time (enforced by the DB-row lock
    each pipeline acquires via acquire_pipeline_lock before starting),
    so this only ever has one writer.
    """

    def __init__(self) -> None:
        self._running: bool = False
        self._started_at: float | None = None

    def start(self) -> None:
        self._running = True
        self._started_at = time.time()

    def stop(self) -> None:
        self._running = False
        self._started_at = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def age(self) -> timedelta | None:
        """Wall-clock age of the current run, or None when idle."""
        if not self._running or self._started_at is None:
            return None
        return timedelta(seconds=time.time() - self._started_at)
