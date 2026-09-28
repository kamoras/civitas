"""Network byte counters for the admin dashboard, across the backend's
containers.

A container sees only its own interfaces (its network namespace). Under
Swarm the admin API is served by the pipeline service, whose counters are
the pipeline's own upstream fetches; the visitors' traffic goes through the
API containers. So the API process records its container's totals to a
file on the data volume both services mount (record_api_totals), and the
admin endpoint adds them to its own (backend_totals). With both roles in
one process (PROCESS_ROLE=all) there is one container and no file.
"""

import json
import logging
import time

logger = logging.getLogger(__name__)

# How often the API process records its totals, and how old a record may be
# before it is taken for an API container that is gone.
RECORD_EVERY_S = 30
_STALE_AFTER_S = 4 * RECORD_EVERY_S


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


def _api_path() -> str:
    from app.atomic_write import runtime_data_path

    return runtime_data_path("api_network.json")


def record_api_totals() -> None:
    """Write this (API) container's totals for the admin endpoint to read.
    Every worker in the container writes the same numbers: one namespace."""
    from app.atomic_write import write_text_atomic

    rx, tx = own_totals()
    write_text_atomic(_api_path(), json.dumps({"rx": rx, "tx": tx, "at": time.time()}))


def api_totals() -> tuple[int, int] | None:
    """The API containers' last recorded totals, or None when none were
    recorded recently (one process runs both roles, or the API is down)."""
    try:
        with open(_api_path()) as fh:
            data = json.load(fh)
        if time.time() - float(data["at"]) > _STALE_AFTER_S:
            return None
        return int(data["rx"]), int(data["tx"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def backend_totals() -> dict:
    """{"rx", "tx", "includesApi"}: this container's totals plus the API
    containers' when they recorded some recently."""
    rx, tx = own_totals()
    api = api_totals()
    if api is not None:
        rx, tx = rx + api[0], tx + api[1]
    return {"rx": rx, "tx": tx, "includesApi": api is not None}


async def run_recorder() -> None:
    """The API process's recording loop (main.lifespan, PROCESS_ROLE=api)."""
    import asyncio

    while True:
        try:
            await asyncio.to_thread(record_api_totals)
        except Exception:
            logger.debug("Couldn't record API network totals", exc_info=True)
        await asyncio.sleep(RECORD_EVERY_S)
