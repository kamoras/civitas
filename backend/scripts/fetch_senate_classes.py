"""Regenerate the bundled app/data/senate_classes.json fallback.

The election pipeline refreshes /data/senate_classes.json from the Senate's
member list at the start of every run (app/pipeline/fetch/senate_classes.py);
the bundled copy is read only before a fresh volume's first run. This runs
the same refresh against the bundled file.

Run from backend/ (network required):
    python3 scripts/fetch_senate_classes.py
"""

import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.http_client import make_async_client  # noqa: E402
from app.pipeline.fetch.senate_classes import refresh_senate_classes  # noqa: E402

BUNDLED = pathlib.Path(__file__).resolve().parents[1] / "app" / "data" / "senate_classes.json"


async def _run() -> bool:
    async with make_async_client(follow_redirects=True) as client:
        return await refresh_senate_classes(client, str(BUNDLED))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ok = asyncio.run(_run())
    print(f"wrote {BUNDLED}" if ok else "kept the previous file; see the log above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
