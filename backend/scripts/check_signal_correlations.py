"""Measure the two disclosed component overlaps against the live deployment.

The pipeline runs this check itself after every chamber run
(app/pipeline/analyze/signal_overlap.py, which holds the pairs, the math and
the report bands). This script is the same measurement from outside: it
reads the public score-breakdown API, the reasoning audit_pac_ratio.py and
fetch_district_pvi.py give for reading the live deployment rather than a
local DB. With --write it records the result as the bundled fallback
(app/data/signal_overlap.json) that the API serves before a deployment's
first run.

Run from the repo (network required):
    python3 backend/scripts/check_signal_correlations.py [--write]

Exit status 1 when a pair is in the action band.
"""

import argparse
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import CONTACT_EMAIL  # noqa: E402
from app.pipeline.analyze.signal_overlap import measure  # noqa: E402
from app.time_utils import utcnow  # noqa: E402

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": f"CivitasCivicPlatform/1.0 (signal-correlation audit; contact: {CONTACT_EMAIL})"}
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "signal_overlap.json"


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_breakdowns(branch: str) -> list[dict]:
    kind = "senators" if branch == "senate" else "representatives"
    listing = _fetch_json(f"{API_BASE}/politicians?branch={branch}")
    out = []
    for d in listing:
        # The pipeline measures currently serving members only; the listing
        # also has departed members inside the removal grace window.
        if not d.get("hasScorecard") or d.get("isCurrent") is False:
            continue
        try:
            out.append(_fetch_json(f"{API_BASE}/{kind}/{d['id']}/score-breakdown"))
        except Exception as e:  # a single missing member shouldn't kill the audit
            print(f"  skip {d.get('id')}: {e}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true", help=f"record the result in {OUT.name}")
    args = parser.parse_args()

    results = {}
    for chamber in ("senate", "house"):
        entries = fetch_breakdowns(chamber)
        results[chamber] = measure(entries)
        print(f"\n{chamber} ({len(entries)} members with breakdowns):")
        for pair in results[chamber].values():
            first, second = pair["labels"]
            r = "no signal" if pair["r"] is None else f"r={pair['r']:+.3f}"
            print(f"  {first} vs {second}: {r} (n={pair['n']}) [{pair['band']}]")

    if args.write:
        now = utcnow().isoformat(timespec="seconds")
        out = {
            "_source": (
                "Pre-first-run fallback only: every chamber run measures this from the "
                "members it scored (analyze/signal_overlap.record_signal_overlap) and writes "
                "/data/signal_overlap.json, which takes precedence. Recorded by "
                f"scripts/check_signal_correlations.py --write from {API_BASE} score breakdowns."
            ),
            **{chamber: {"pairs": pairs, "computed_at": now} for chamber, pairs in results.items()},
        }
        OUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {OUT}")

    if any(p["band"] == "action" for pairs in results.values() for p in pairs.values()):
        print("\nACTION items above: see score_calculator.py's v6.8/v6.11 notes "
              "for the established fix pattern (reduce the redundant weight or "
              "restructure, don't recalibrate around it).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
