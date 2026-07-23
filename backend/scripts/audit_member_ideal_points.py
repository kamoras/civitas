"""Dry-run audit of the automated Voteview ideal-point ingestion.

NOT a required step — app/pipeline/fetch/voteview.py refreshes
/data/member_ideal_points.json automatically on every pipeline run.
This wrapper exists for the same reason audit_pac_ratio.py does:
inspecting what an ingest WOULD produce (member counts, per-party fits,
r², saturation, gate results) without touching the volume, e.g. when a
run's logs show a gate failure and you want the details, or after a new
congress starts and the fits should be sanity-checked.

Imports the pipeline's own fetch/build/gate functions — no second copy
of any logic, so this audit can never drift from what the pipeline
actually does (the calibrate_les_credit_scale.py convention).

Run from the repo (network required):
    python3 backend/scripts/audit_member_ideal_points.py [congress]
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.pipeline.analyze.score_calculator import _district_pvi, _state_pvi  # noqa: E402
from app.pipeline.fetch.voteview import (  # noqa: E402
    build_chamber_ideal_points,
    fetch_member_rows,
    ingestion_gates,
)


async def main() -> int:
    congress = int(sys.argv[1]) if len(sys.argv) > 1 else settings.CURRENT_CONGRESS
    any_failures = False
    for chamber in ("senate", "house"):
        rows = await fetch_member_rows(chamber, congress)
        if rows is None:
            print(f"{chamber}: FETCH FAILED (congress {congress})")
            any_failures = True
            continue
        data, failures = build_chamber_ideal_points(rows, chamber, _state_pvi(), _district_pvi())
        failures += ingestion_gates(chamber, data)
        fits = ", ".join(
            f"{p}: a={f['a']:+.3f} b={f['b']:+.5f} r2={f['r2']:.2f} n={f['n']}"
            for p, f in data["fit"].items()
        )
        print(f"{chamber} (congress {congress}): {len(data['members'])} members, "
              f"p90 |extremity| {data['extremity_p90']}, fits [{fits}]")
        for f in failures:
            print(f"  GATE FAILED: {f}")
        any_failures |= bool(failures)
    return 1 if any_failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
