import logging
import threading
import time
from datetime import timedelta
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_RunModel = TypeVar("_RunModel")

# Shared stale-run threshold for every run lock and every reader of a run
# row (live_run). 12h is the "definitely wedged, not just slow" bar;
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


def _run_lease(model: type) -> "str | None":
    """The lease every run of `model` holds for its duration, if any: the
    Senate run's (lease.SENATE_RUN, taken before its row is written)."""
    from app.models import PipelineRun
    from app.pipeline import lease

    return lease.SENATE_RUN if model is PipelineRun else None


# Why a run's row was marked stale on its lease's proof (_proven_dead).
DEAD_RUN_MESSAGE = "Marked stale: its run's lease lapsed — no beat for its whole stale window"


def _lease_on(db: Session, model: type, row_id: int) -> str:
    """What `model`'s run lease says about row `row_id`: "live" (it names
    the row and is held), "dead" (it names the row and has lapsed — for the
    Senate run, an hour without a beat: lease.stale_after), or "none" (no
    lease, or one naming another run or none, says nothing about it)."""
    from app.pipeline import lease

    tier = _run_lease(model)
    record = lease.lease_record(db, tier) if tier is not None else None
    if record is None or record[1] != row_id:
        return "none"
    return "live" if lease.held(db, tier) else "dead"


def _proven_dead(db: Session, model: type, row) -> bool:
    """Whether `row` (RUNNING) is proven dead by its run's lease: the lease
    row names this run (lease.tag, written with the row) and has lapsed.
    Anything else keeps the age rule."""
    return _lease_on(db, model, row.id) == "dead"


def live_run(db: Session, model: type[_RunModel], stale_timeout: timedelta = STALE_PIPELINE_TIMEOUT) -> "_RunModel | None":
    """`model`'s RUNNING row if its run may still be live, else None: a row
    older than `stale_timeout`, or one its lease proves dead (_proven_dead),
    is not. Anything short of proof gets the benefit of the doubt — a
    falsely dead run can be deployed over, reset under or run twice; a
    falsely live one only makes things wait. Every reader asking "is a
    Senate run going?" — the status endpoint and triggers, the data reset,
    the rescores, Stock, the hourly refreshes, the overrun alert — asks
    this."""
    from app.models import PipelineStatus

    running = db.query(model).filter(model.status == PipelineStatus.RUNNING).first()
    if running is None:
        return None
    if utcnow() - running.started_at >= stale_timeout or _proven_dead(db, model, running):
        logger.debug("%s run #%d is RUNNING but dead — not waited on", model.__name__, running.id)
        return None
    return running


def senate_run_state(db: Session) -> "tuple[int | None, bool, bool]":
    """One read of the Senate run's row and lease, for everything an admin
    sees: (the RUNNING row's id or None, whether it counts as running —
    live_run's answer — and whether an operator may clear it:
    clear-stuck-senate). Clearable is a row no live lease speaks for: past
    the age rule, proven dead, or named by no lease (a run from a release
    without leases, or one left RUNNING after its lease was let go). A row
    its lease names while held may be a live run whose beats stalled."""
    from app.models import PipelineRun, PipelineStatus

    row = db.query(PipelineRun).filter(PipelineRun.status == PipelineStatus.RUNNING).first()
    if row is None:
        return None, False, False
    past_age = utcnow() - row.started_at >= STALE_PIPELINE_TIMEOUT
    said = _lease_on(db, PipelineRun, row.id)
    return row.id, not past_age and said != "dead", past_age or said != "live"


def mark_proven_dead_stale(db: Session, model: type) -> int:
    """Mark stale `model`'s RUNNING rows its lease proves dead
    (_proven_dead), conditional on still RUNNING; commits unless a data
    reset holds the database. Returns how many. Raises what the database
    raises."""
    from app.models import PipelineStatus
    from app.pipeline import lease

    marked = 0
    for row in db.query(model).filter(model.status == PipelineStatus.RUNNING).all():
        if _proven_dead(db, model, row):
            marked += db.query(model).filter(model.id == row.id, model.status == PipelineStatus.RUNNING).update({
                "status": PipelineStatus.STALE,
                "completed_at": utcnow(),
                "error_message": DEAD_RUN_MESSAGE,
            }, synchronize_session=False)
            logger.warning("Marking the dead %s run #%d stale — its lease lapsed", model.__name__, row.id)
    if marked and lease.held(db, lease.DATA_RESET):
        # Checked in the write's own transaction, before committing
        # (lease.DATA_RESET): a reset holds the database, so back out.
        db.rollback()
        return 0
    db.commit()
    return marked


def tidy_dead_runs() -> int:
    """mark_proven_dead_stale for every run table that holds a run lease,
    on its own session, so run history stops showing a proven-dead run as
    running. Run at startup and hourly. Rows no lease proves dead are left
    to the age rule, applied by their own run lock as a new run starts.
    Logs, never raises."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        return sum(mark_proven_dead_stale(db, model) for model in run_tables().values() if _run_lease(model))
    except Exception:
        db.rollback()
        logger.exception("Dead-run tidy failed — it runs again next hour")
        return 0
    finally:
        db.close()


def run_in_progress(db: Session, model: type[_RunModel], stale_timeout: timedelta = STALE_PIPELINE_TIMEOUT) -> bool:
    """Whether a `model` run is live (live_run)."""
    return live_run(db, model, stale_timeout) is not None


# A refusal because the lock's own holder is live (acquire_pipeline_lock_why);
# the others are lease.refusal_code's.
ALREADY_RUNNING = "already_running"
# The stock pipeline's own: it waits for the member pipelines.
MEMBER_PIPELINE_RUNNING = "member_pipeline_running"


def skip_reason_text(reason: str | None, tier: str | None = None) -> str:
    """A pipeline skip's reason code, as its log and the nightly alert say
    it — a lease refusal in lease.refusal_text's words, naming `tier`'s job
    when it holds the lease."""
    from app.pipeline import lease

    if reason in (lease.REFUSED_BY_RESET, lease.REFUSED_BUSY, lease.REFUSED_HELD):
        return lease.refusal_text(reason, tier)
    return {
        ALREADY_RUNNING: "a previous run of it was still active",
        MEMBER_PIPELINE_RUNNING: "a member pipeline (Senate or House) was running",
    }.get(reason or "", f"it was skipped ({reason or 'no reason given'})")


def acquire_pipeline_lock_why(
    db: Session, model: type[_RunModel], stale_timeout: timedelta, *,
    on_insert: Callable[[_RunModel], bool] | None = None,
) -> "tuple[_RunModel | None, str | None]":
    """Atomically create a new locked run of `model`, auto-clearing a
    stale leftover RUNNING row first. Returns (run, None), or (None, why):
    ALREADY_RUNNING when a genuinely still-active (non-stale) run holds the
    lock, else a lease.refusal_code — a data reset holds the database, or it
    stayed busy — so the skip can say which.

    Generalizes senate_pipeline.py's original _acquire_pipeline_lock_why
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

    from app.pipeline import lease

    running = db.query(model).filter(model.status == PipelineStatus.RUNNING).first()
    cleared = None
    if running:
        age = utcnow() - running.started_at
        if age <= stale_timeout:
            return None, ALREADY_RUNNING
        # Marked stale in the same transaction as the new row and the reset
        # check below, not committed ahead of them: a run that yields to the
        # reset backs out with a rollback, writing nothing (lease.DATA_RESET).
        running.status = PipelineStatus.STALE
        running.completed_at = utcnow()
        running.error_message = f"Marked stale: exceeded {stale_timeout} timeout"
        cleared = (running.id, age)

    try:
        db.flush()  # the stale mark before the new row: one RUNNING row at a time
        run = model(started_at=utcnow(), status=PipelineStatus.RUNNING)
        db.add(run)
        db.flush()
        if lease.held(db, lease.DATA_RESET):
            # Checked inside the insert's own transaction (see
            # lease.DATA_RESET): backing out is a rollback, no second write.
            db.rollback()
            logger.warning("%s not started: an admin data reset is running", model.__name__)
            return None, lease.REFUSED_BY_RESET
        # In the row's own transaction (a run lease's tag); refused, the
        # run doesn't start — its lease was lost meanwhile.
        if on_insert is not None and not on_insert(run):
            db.rollback()
            logger.warning("%s not started: its lease was taken over before its row was written", model.__name__)
            return None, lease.REFUSED_HELD
        db.commit()
    except IntegrityError:
        # Another container inserted its running row between our check
        # and our commit — it holds the lock.
        db.rollback()
        logger.info("%s lock held by another container — skipping this run", model.__name__)
        return None, ALREADY_RUNNING
    except OperationalError as error:
        db.rollback()
        if not lease.is_locked(error):
            raise
        # The database stayed locked past the busy timeout — a writer (the
        # admin data reset's wipe, say) holding it. Not this run's to wait on.
        logger.warning("%s not started: the database is locked by another writer", model.__name__)
        return None, lease.REFUSED_BUSY
    if cleared is not None:
        logger.warning("Cleaned up stale %s run #%d (age: %s)", model.__name__, *cleared)
    return run, None


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

    A pipeline runs in at most one background thread at a time (its DB-row
    lock sees to that), so start() replaces any run before it: one the lock
    let it past was stale. A job without a DB lock runs under its lease
    instead (lease.tracked_job): it checks busy(), takes the lease, and only
    then start()s — so it too starts only past any run but a hung one. A
    replaced run is not waited on again, and its late stop() is a no-op.
    One slot, under one lock.
    """

    def __init__(self) -> None:
        self._token = 0
        self._started_at: float | None = None  # time.time() of the run going, None when idle
        self._holder: str | None = None  # who started it, for a refusal to name
        self._lock = threading.Lock()

    def _begin(self, holder: str | None = None) -> int:
        self._token += 1
        self._started_at = time.time()
        self._holder = holder
        return self._token

    def start(self, holder: str | None = None) -> int:
        """Mark a run started, replacing any before it; returns its token
        for stop(). `holder` names it to a refusal (see holder)."""
        with self._lock:
            return self._begin(holder)

    def busy(self, hung_after: timedelta | None = None) -> bool:
        """Whether a run is going — with `hung_after`, one younger than that
        (an older one is presumed hung). A check that holds nothing."""
        with self._lock:
            if self._started_at is None:
                return False
            return hung_after is None or time.time() - self._started_at < hung_after.total_seconds()

    def stop(self, run: int | None) -> None:
        """Mark the run `run` stopped; a no-op unless it is the run going
        (None, a replaced run's token)."""
        with self._lock:
            if run is not None and run == self._token:
                self._started_at = None

    def clear(self) -> None:
        """Forget the run going — for tests that reset shared module state."""
        with self._lock:
            self._started_at = None

    @property
    def holder(self) -> str | None:
        """Who started the run going (start's `holder`), None when idle or
        unnamed."""
        with self._lock:
            return self._holder if self._started_at is not None else None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._started_at is not None

    @property
    def age(self) -> timedelta | None:
        """Wall-clock age of the run going, or None when idle."""
        with self._lock:
            if self._started_at is None:
                return None
            return timedelta(seconds=time.time() - self._started_at)
