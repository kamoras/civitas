"""Network traffic for the admin dashboard, across the backend's containers.

A container sees only its own interfaces (its network namespace). Under
Swarm the admin API is served by the pipeline service, whose counters are
the pipeline's own upstream fetches; the visitors' traffic goes through the
API containers. So each API container records its *rate* — bytes per
second over its own last interval — to a record of its own on the data
volume both services mount, and the admin endpoint reports it beside its own cumulative
counters (the dashboard turns those into a rate and adds the API's).

A rate, not the API's cumulative counters: a sum of counters from
containers that start and stop at different times jumps by a container's
whole lifetime of bytes whenever its record appears, lapses, or passes to
a new task in a rolling update. A rate is only ever absent (counted as 0)
or right. With both roles in one process (PROCESS_ROLE=all) there is one
container and no record.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# How often the API records its rate (one worker per round), and how old a
# record may be before the API is taken for gone.
RECORD_EVERY_S = 60
_STALE_AFTER_S = 3 * RECORD_EVERY_S
# One record per API container (named by its hostname, which Docker sets to
# the container's id): during a rolling update the old and new containers
# both serve, and one shared record would hold whichever wrote last.
_RECORD_PREFIX = "api_network-"
# A container's record this old is gone for good (a replaced task): deleted.
_FORGET_AFTER_S = 24 * 3600

# This process's previous sample: (rx, tx, monotonic time).
_previous: tuple[int, int, float] | None = None
# Set once this process has removed its record on the way out
# (forget_own_record), under the lock the write takes: a recording already
# on its thread when the loop is cancelled would otherwise write it back.
_record_lock = threading.Lock()
_forgotten = False


def own_totals() -> tuple[int, int] | None:
    """(received, sent) bytes on this container's interfaces, loopback
    excluded; None when they can't be read — never zeros, which the next
    good read would turn into a lifetime's bytes in one interval."""
    rx = tx = 0
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" not in line:
                    continue
                iface, data = line.split(":", 1)
                if iface.strip() == "lo":
                    continue
                cols = data.split()
                rx += int(cols[0])
                tx += int(cols[8])
    except (OSError, ValueError, IndexError):
        return None
    return rx, tx


def record_api_rate() -> bool:
    """Record this container's rate since this process's last sample; False
    (nothing written) on a first sample or a counter that went backwards."""
    from app.shared_state import write_record

    global _previous
    totals = own_totals()
    if totals is None:
        return False  # this sample is lost; the previous one stands
    rx, tx = totals
    now = time.monotonic()
    previous, _previous = _previous, (rx, tx, now)
    if previous is None or now <= previous[2] or rx < previous[0] or tx < previous[1]:
        return False
    elapsed = now - previous[2]
    with _record_lock:
        if _forgotten:
            return False
        write_record(_record_path(), {
            "rxRate": (rx - previous[0]) / elapsed,
            "txRate": (tx - previous[1]) / elapsed,
        })
    return True


def api_rates() -> dict | None:
    """{"rxRate", "txRate"}: the sum over the API containers that recorded
    recently (both, while a rolling update overlaps them), or None."""
    import glob
    import os

    from app.shared_state import read_record, record_path
    from app.time_utils import utcnow

    rx = tx = 0.0
    found = False
    for path in glob.glob(record_path(f"{_RECORD_PREFIX}*.json")):
        record = read_record(path)
        if not isinstance(record, tuple):
            continue
        age = (utcnow() - record[0]).total_seconds()
        if age > _FORGET_AFTER_S:
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        if age > _STALE_AFTER_S or not isinstance(record[1], dict):
            continue
        try:
            rx += float(record[1]["rxRate"])
            tx += float(record[1]["txRate"])
        except (KeyError, TypeError, ValueError):
            continue
        found = True
    return {"rxRate": rx, "txRate": tx} if found else None


def _record_path() -> str:
    """This container's record."""
    import socket

    from app.shared_state import record_path

    return record_path(f"{_RECORD_PREFIX}{socket.gethostname()}.json")


def forget_own_record() -> None:
    """Remove this container's record as its API process stops (a rolling
    update replacing it), so the dashboard doesn't count it for the minutes
    until it would have gone stale. The container's other worker, if it
    keeps running, writes it again within a round."""
    import os

    global _forgotten
    with _record_lock:
        _forgotten = True
        try:
            os.unlink(_record_path())
        except OSError:
            pass


async def run_recorder() -> None:
    """The API process's recording loop (main.lifespan, PROCESS_ROLE=api).
    Every worker in the container runs it; each round goes to one of them
    (the throttle store's claim), so the volume sees one write a round."""
    import asyncio

    from app.api import throttle

    global _forgotten
    _forgotten = False  # a new run of the app in this process records again
    while True:
        try:
            if await throttle.run(throttle.claim, "net-record", "api", period=RECORD_EVERY_S * 0.9):
                await asyncio.to_thread(record_api_rate)
            else:
                # Keep this worker's own sample current, so the round it
                # wins measures a recent interval rather than a long one.
                global _previous
                totals = own_totals()
                if totals is not None:
                    _previous = (*totals, time.monotonic())
        except Exception:
            logger.debug("Couldn't record the API's network rate", exc_info=True)
        await asyncio.sleep(RECORD_EVERY_S)
