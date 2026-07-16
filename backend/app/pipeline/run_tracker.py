import time
from datetime import timedelta


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
    each pipeline already acquires before starting), so this only ever
    has one writer.
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
