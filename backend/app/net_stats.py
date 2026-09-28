"""Network traffic for the admin dashboard, across the backend's containers.

A container sees only its own interfaces (its network namespace). Under
Swarm the admin API is served by the pipeline service, whose counters are
the pipeline's own upstream fetches; the visitors' traffic goes through the
API containers. So the API records its container's *rate* — bytes per
second over its own last interval — to a record on the data volume both
services mount, and the admin endpoint reports it beside its own cumulative
counters (the dashboard turns those into a rate and adds the API's).

A rate, not the API's cumulative counters: a sum of counters from
containers that start and stop at different times jumps by a container's
whole lifetime of bytes whenever its record appears, lapses, or passes to
a new task in a rolling update. A rate is only ever absent (counted as 0)
or right. With both roles in one process (PROCESS_ROLE=all) there is one
container and no record.
"""

import logging
import time

logger = logging.getLogger(__name__)

# How often the API records its rate (one worker per round), and how old a
# record may be before the API is taken for gone.
RECORD_EVERY_S = 60
_STALE_AFTER_S = 3 * RECORD_EVERY_S
_RECORD = "api_network.json"

# This process's previous sample: (rx, tx, monotonic time).
_previous: tuple[int, int, float] | None = None


def own_totals() -> tuple[int, int]:
    """(received, sent) bytes on this container's interfaces, loopback
    excluded; (0, 0) when they can't be read."""
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
        return 0, 0
    return rx, tx


def record_api_rate() -> bool:
    """Record this container's rate since this process's last sample; False
    (nothing written) on a first sample or a counter that went backwards."""
    from app.shared_state import write_record

    global _previous
    rx, tx = own_totals()
    now = time.monotonic()
    previous, _previous = _previous, (rx, tx, now)
    if previous is None or now <= previous[2] or rx < previous[0] or tx < previous[1]:
        return False
    elapsed = now - previous[2]
    write_record(_record_path(), {
        "rxRate": (rx - previous[0]) / elapsed,
        "txRate": (tx - previous[1]) / elapsed,
    })
    return True


def api_rates() -> dict | None:
    """{"rxRate", "txRate"} the API recorded recently, or None."""
    from app.shared_state import read_record
    from app.time_utils import utcnow

    record = read_record(_record_path())
    if not isinstance(record, tuple) or not isinstance(record[1], dict):
        return None
    if (utcnow() - record[0]).total_seconds() > _STALE_AFTER_S:
        return None
    try:
        return {"rxRate": float(record[1]["rxRate"]), "txRate": float(record[1]["txRate"])}
    except (KeyError, TypeError, ValueError):
        return None


def _record_path() -> str:
    from app.shared_state import record_path

    return record_path(_RECORD)


async def run_recorder() -> None:
    """The API process's recording loop (main.lifespan, PROCESS_ROLE=api).
    Every worker in the container runs it; each round goes to one of them
    (the throttle store's claim), so the volume sees one write a round."""
    import asyncio

    from app.api import throttle

    while True:
        try:
            if await throttle.run(throttle.claim, "net-record", "api", period=RECORD_EVERY_S * 0.9):
                await asyncio.to_thread(record_api_rate)
            else:
                # Keep this worker's own sample current, so the round it
                # wins measures a recent interval rather than a long one.
                global _previous
                rx, tx = own_totals()
                _previous = (rx, tx, time.monotonic())
        except Exception:
            logger.debug("Couldn't record the API's network rate", exc_info=True)
        await asyncio.sleep(RECORD_EVERY_S)
