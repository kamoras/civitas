import asyncio
import threading
import time


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
    """

    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps
        self.last_call = 0.0
        self._lock = threading.Lock()

    async def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                wait = self.min_interval - (now - self.last_call)
                if wait <= 0:
                    self.last_call = now
                    return
            await asyncio.sleep(wait)
