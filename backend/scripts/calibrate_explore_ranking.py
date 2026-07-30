"""CLI over app/pipeline/calibrate_ranking.compute_calibration.

The explore pipeline recalibrates on every run, so this exists for two
narrower jobs: regenerating the bundled bootstrap file that covers a fresh
deploy before its first pipeline run, and inspecting a calibration without
waiting for one.

    cd backend && .venv/bin/python scripts/calibrate_explore_ranking.py
    cd backend && .venv/bin/python scripts/calibrate_explore_ranking.py --write

See app/pipeline/calibrate_ranking.py for what each value is derived from.
"""

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUTPUT = (pathlib.Path(__file__).resolve().parent.parent
          / "app" / "data" / "explore_ranking.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="write app/data/explore_ranking.json")
    parser.add_argument("--samples", type=int, default=None)
    args = parser.parse_args()

    from app.database import SessionLocal
    from app.pipeline.calibrate_ranking import DEFAULT_SAMPLES, compute_calibration

    db = SessionLocal()
    try:
        payload = compute_calibration(db, samples=args.samples or DEFAULT_SAMPLES)
    finally:
        db.close()

    if not payload:
        print("No explore documents indexed — run the explore pipeline first.")
        return 1

    for key in ("field_weights", "field_weight_mrr", "retriever_resolution_ranks",
                "prior_coverage", "prior_weights", "filter_survival",
                "candidate_pool", "source_diversity_cap", "fingerprint",
                "text_shape"):
        print(f"{key:28} {payload[key]}")

    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {OUTPUT}")
    else:
        print("\n(dry run — pass --write to update the bundled bootstrap file)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
