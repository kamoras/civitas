import logging
import time
from datetime import timedelta
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, OperationalError
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


# The run table of every pipeline with one: the admin data reset (api/admin.py)
# refuses while any has a live run.
def run_tables() -> dict[str, type]:
    from app.models import (
        ElectionPipelineRun, HousePipelineRun, PipelineRun, StockTradesPipelineRun, SupplementaryPipelineRun,
    )

    return {
        "Senate": PipelineRun, "Supplementary": SupplementaryPipelineRun, "House": HousePipelineRun,
        "Stock trades": StockTradesPipelineRun, "Election": ElectionPipelineRun,
    }


def run_in_progress(db: Session, model: type[_RunModel], stale_timeout: timedelta = STALE_PIPELINE_TIMEOUT) -> bool:
    """Whether a `model` run is RUNNING and young enough to be real. A row
    older than `stale_timeout` is one a killed process left behind (the
    same bar acquire_pipeline_lock clears it by), not a live run."""
    from app.models import PipelineStatus

    running = db.query(model).filter(model.status == PipelineStatus.RUNNING).first()
    return running is not None and utcnow() - running.started_at < stale_timeout


def acquire_pipeline_lock(db: Session, model: type[_RunModel], stale_timeout: timedelta) -> "_RunModel | None":
    """Atomically create a new locked run of `model`, auto-clearing a
    stale leftover RUNNING row first. Returns None if a genuinely still-
    active (non-stale) run already holds the lock.

    Generalizes senate_pipeline.py's original _acquire_pipeline_lock
    (2026-07) to House/Stock/Supplementary, which
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
            try:
                db.commit()
            except OperationalError as error:
                db.rollback()
                from app.pipeline import lease

                if not lease.is_locked(error):
                    raise
                logger.warning("%s not started: the database is locked by another writer", model.__name__)
                return None
            logger.warning(
                "Cleaned up stale %s run #%d (age: %s)", model.__name__, running.id, age,
            )
        else:
            return None

    from app.pipeline import lease

    run = model(started_at=utcnow(), status=PipelineStatus.RUNNING)
    db.add(run)
    try:
        db.flush()
        if lease.held(db, lease.DATA_RESET):
            # Checked inside the insert's own transaction (see
            # lease.DATA_RESET): backing out is a rollback, no second write.
            db.rollback()
            held_off_by_reset(db, model.__name__)
            return None
        db.commit()
    except IntegrityError:
        # Another container inserted its running row between our check
        # and our commit — it holds the lock.
        db.rollback()
        logger.info("%s lock held by another container — skipping this run", model.__name__)
        return None
    except OperationalError as error:
        db.rollback()
        if not lease.is_locked(error):
            raise
        # The database stayed locked past the busy timeout — a writer (the
        # admin data reset's wipe, say) holding it. Not this run's to wait on.
        logger.warning("%s not started: the database is locked by another writer", model.__name__)
        return None
    return run


# A live reset's wipe is a few table deletes. A reset lease left unbeaten
# longer than this is almost certainly a dead reset's, holding pipelines off
# until it lapses (lease.stale_after) — which must not look like a quiet night.
_DEAD_RESET_AFTER = timedelta(minutes=5)


def held_off_by_reset(db: Session, run_name: str) -> None:
    """A pipeline didn't start because a data reset holds the database:
    logged, and alerted when the reset looks dead rather than mid-wipe (a
    live one's alert would be noise, and its write would queue behind the
    wipe)."""
    from app.models import ApiCache
    from app.pipeline import lease

    logger.warning("%s not started: an admin data reset is running", run_name)
    beaten = db.query(ApiCache.cached_at).filter(
        ApiCache.tier == lease.DATA_RESET, ApiCache.cache_key == "lock",
    ).scalar()
    if beaten is None or utcnow() - beaten < _DEAD_RESET_AFTER:
        return
    try:
        from app.ops_alerts import send_ops_alert

        send_ops_alert(
            "Pipeline held off by a stalled data reset",
            f"{run_name} did not start: a data reset's lease, last renewed {beaten:%H:%M} UTC, still holds "
            "the database. If no reset is running, one died mid-wipe and its lease lapses within the "
            "half hour; trigger the pipeline after that.",
            dedupe_key=f"held-off-by-reset-{run_name}-{utcnow():%Y-%m-%d}",
        )
    except Exception:
        logger.exception("Could not send the held-off-by-reset alert")


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
