import asyncio
import time


class RateLimiter:
    """Async rate limiter that enforces a maximum requests-per-second."""

    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps
        self.last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self.last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_call = time.monotonic()
