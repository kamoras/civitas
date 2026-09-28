"""CLI over app/pipeline/analyze/action_thresholds.fit_all.

The hourly refresh recalibrates once a day, so this exists to regenerate
the bundled file a fresh deploy reads before its first calibration, and
to inspect the labelled distributions without waiting for one.

    cd backend && .venv/bin/python scripts/calibrate_action_thresholds.py
    cd backend && .venv/bin/python scripts/calibrate_action_thresholds.py --write

A name without MIN_PER_CLASS labelled pairs on each side keeps its bundled
value; the printed support says which.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.pipeline.analyze import action_thresholds  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write app/data/action_thresholds.json")
    args = parser.parse_args()

    bundled = json.loads(action_thresholds._BUNDLED.read_text())
    db = SessionLocal()
    try:
        result = action_thresholds.fit_all(db, bundled["values"])
    finally:
        db.close()

    print(f"runs read: {result['runs_read']}")
    for name in action_thresholds.NAMES:
        print(f"{name:16} {result['values'][name]:.2f}   support {result['support'][name]}")

    if args.write:
        fitted = result["fitted"]
        payload = {
            "_source": (
                f"scripts/calibrate_action_thresholds.py, {datetime.date.today().isoformat()}, "
                f"from {result['runs_read']} action-metrics runs. Fitted: {', '.join(fitted) or 'none'}; "
                "any other value is carried over from the previous file. Support per name: "
                + json.dumps(result["support"])
            ),
            "values": result["values"],
        }
        action_thresholds._BUNDLED.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {action_thresholds._BUNDLED}")
    else:
        print("\n(dry run — pass --write to update the bundled file)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
