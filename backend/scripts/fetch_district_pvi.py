"""Regenerate the bundled app/data/district_pvi.json fallback.

The primary data path is app/pipeline/fetch/district_pvi.py, which the
Supplementary pipeline runs weekly (or immediately when the persistent
volume has no data yet) and which writes /data/district_pvi.json. The
bundled copy is served only in the window before a fresh deployment's first
automated ingest completes.

This script runs that same refresh — the same House Clerk apportionment, the
same Wikipedia infobox parse and the same ingestion gates — and points its
output at the bundled file, so there is one implementation and no second
copy of the seat table or the state names to drift.

Run from backend/ (network required):
    python3 scripts/fetch_district_pvi.py [output.json]

Exits 1 when the refresh keeps the previous data (a district failed to
parse, a gate failed, or a source was unreachable; the log says which).
"""

import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.pipeline.fetch import district_pvi  # noqa: E402

DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parents[1] / "app" / "data" / "district_pvi.json"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    district_pvi._PVI_PATH = str(output)
    ok = asyncio.run(district_pvi.refresh_district_pvi())
    print(f"wrote {output}" if ok else "kept the previous file; see the log above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
