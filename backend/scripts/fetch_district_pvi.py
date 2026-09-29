"""Regenerate the bundled app/data/district_pvi.json.

The primary data path is app/pipeline/fetch/district_pvi.py, which the
Supplementary pipeline runs weekly (and scheduler.py's pre-checks run
whenever the persistent file lacks the sitting Congress's table), writing
/data/district_pvi.json. The bundled copy this script writes is only the
fallback served before a fresh deployment's first ingest completes.

It runs the exact same fetch and gates, from the same pinned sources
(app/data/district_pvi_sources.json): one immutable revision of
Wikipedia's "Cook Partisan Voting Index" article per Congress's district
lines. See the fetch module's docstring for why the source is pinned
rather than scraped from each district's live infobox.

Output: "districts" is the sitting Congress's table (what member scoring
reads — "ST-N" -> signed int, positive = R lean, at-large seats "ST-0");
"congresses" holds every configured Congress's table with its provenance.

Run from the repo (network required):
    python3 backend/scripts/fetch_district_pvi.py --congress N [output.json]

--congress (required) names the Congress whose table goes in the top-level
"districts" (the newest pinned table at or below it when it has none of
its own). It is explicit, not read from the clock, so the checked-in file
does not change meaning with the day someone happens to regenerate it:
the fallback's top level is only read before a deployment's first ingest,
every House run re-selects the sitting Congress's table from "congresses"
(fetch/district_pvi._reselect), and the tests check the file against the
Congress it names. Give it the Congress in office when you regenerate
(app.config.sitting_congress). Exits 1 if any gate fails, writing nothing.
"""

import argparse
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.ordinals import ordinal  # noqa: E402
from app.pipeline.fetch import district_pvi as dp  # noqa: E402

DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "district_pvi.json"


async def _build(congress: int) -> tuple[dict | None, list[str]]:
    return await dp.build_payload(dp.load_sources(), congress)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", nargs="?", default=str(DEFAULT_OUTPUT))
    ap.add_argument(
        "--congress", type=int, required=True,
        help="the Congress whose table goes in the top-level \"districts\" (the one in office)",
    )
    args = ap.parse_args()

    payload, failures = asyncio.run(_build(args.congress))
    for f in failures:
        print("GATE FAILED:", f)
    if payload is None:
        return 1
    for c, block in sorted(payload["congresses"].items()):
        vals = list(block["districts"].values())
        print(
            f"{ordinal(int(c))} Congress: {len(vals)} districts, R {sum(v > 0 for v in vals)}, "
            f"D {sum(v < 0 for v in vals)}, EVEN {sum(v == 0 for v in vals)} — {block['_lines']}"
        )
    pathlib.Path(args.output).write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    )
    print(f"wrote {args.output} (member lines: the {ordinal(payload['congress'])} Congress's)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
