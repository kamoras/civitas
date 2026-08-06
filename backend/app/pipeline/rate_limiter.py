import asyncio
import logging
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Every limiter instance, keyed by source name. Module-level because the
# limiters themselves are module-level singletons: the point is to be able
# to ask, from outside any one fetch module, "how long did this run spend
# blocked on FEC?" without each of the 19 call sites having to report in.
_REGISTRY: dict[str, "RateLimiter"] = {}
_REGISTRY_LOCK = threading.Lock()


def _caller_module(depth: int) -> str:
    """Name a limiter after the fetch module that constructed it.

    Every limiter but one is the only limiter in its module, so deriving
    the name from the caller's __name__ gives readable per-source
    accounting ("fec", "congress", "govinfo") without touching any of the
    construction sites. The one module with two limiters passes an
    explicit name.
    """
    try:
        module = sys._getframe(depth).f_globals.get("__name__", "")
    except (ValueError, AttributeError):  # pragma: no cover - defensive
        return "unknown"
    # app.pipeline.fetch.fec -> fec
    return module.rsplit(".", 1)[-1] or "unknown"


class RateLimiter:
    """Async rate limiter that enforces a maximum requests-per-second.

    Instances are module-level and shared by coroutines running in
    *different* event loops on different threads (the nightly pipeline
    thread, admin-triggered run threads, the uvicorn loop). An
    ``asyncio.Lock`` is not thread-safe and must not cross loops — a
    release from one loop fails to wake a waiter parked in another,
    which hangs that pipeline silently (suspected mechanism of the
    2026-07-04 House Phase 1 wedge). A ``threading.Lock`` guarding only
    the timestamp arithmetic is safe everywhere; the actual waiting
    happens in ``asyncio.sleep`` on the caller's own loop, never while
    holding the lock.

    Each instance also accumulates how many requests it granted and how
    long callers spent blocked waiting for those grants. A pipeline run
    that takes 12 hours is not necessarily doing 12 hours of work — a
    large share may be this class deliberately sleeping to stay inside
    Congress.gov's 1.2 RPS or the FEC's 0.25 RPS. Those two possibilities
    call for completely different responses (restructure the fetch phase
    vs. add compute), and without these counters they are
    indistinguishable in the run data.
    """

    def __init__(self, rps: float, name: str | None = None):
        self.min_interval = 1.0 / rps
        self.last_call = 0.0
        self._lock = threading.Lock()
        self.total_requests = 0
        self.total_blocked_seconds = 0.0
        self.name = name or _caller_module(2)
        with _REGISTRY_LOCK:
            if self.name in _REGISTRY:
                # Two limiters in one module and no explicit name. Keep both
                # visible rather than silently folding one into the other —
                # merged counters would misattribute one source's waiting.
                suffix = 2
                while f"{self.name}#{suffix}" in _REGISTRY:
                    suffix += 1
                logger.warning(
                    "Duplicate rate limiter name %r — registering as %r. "
                    "Pass an explicit name= to make this readable.",
                    self.name, f"{self.name}#{suffix}",
                )
                self.name = f"{self.name}#{suffix}"
            _REGISTRY[self.name] = self

    async def acquire(self):
        entered = time.monotonic()
        while True:
            with self._lock:
                now = time.monotonic()
                wait = self.min_interval - (now - self.last_call)
                if wait <= 0:
                    self.last_call = now
                    self.total_requests += 1
                    self.total_blocked_seconds += now - entered
                    return
            await asyncio.sleep(wait)


def snapshot() -> dict[str, tuple[int, float]]:
    """Current (requests, blocked_seconds) for every registered limiter.

    Cumulative since process start, so callers take two snapshots and
    diff them. Counters are read under each limiter's own lock, but the
    set of reads is not atomic across limiters — a request granted
    partway through a snapshot lands in whichever side of the diff it
    happens to fall on. That is a rounding error against multi-minute
    waits and not worth a global lock on the pipeline's hottest path.
    """
    with _REGISTRY_LOCK:
        limiters = list(_REGISTRY.values())
    stats = {}
    for limiter in limiters:
        with limiter._lock:
            stats[limiter.name] = (limiter.total_requests, limiter.total_blocked_seconds)
    return stats


def diff_snapshots(
    before: dict[str, tuple[int, float]],
    after: dict[str, tuple[int, float]],
) -> dict[str, tuple[int, float]]:
    """Per-source (requests, blocked_seconds) between two snapshots.

    Sources with no activity in the interval are omitted, which keeps the
    persisted rows proportional to what actually happened rather than to
    the number of fetch modules that exist.
    """
    out = {}
    for name, (req_after, blocked_after) in after.items():
        req_before, blocked_before = before.get(name, (0, 0.0))
        req_delta = req_after - req_before
        blocked_delta = blocked_after - blocked_before
        if req_delta > 0 or blocked_delta > 0:
            out[name] = (req_delta, round(blocked_delta, 3))
    return out


def reset_registry() -> None:
    """Drop every registered limiter. Tests only — production limiters are
    module-level singletons created at import and never torn down."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()
