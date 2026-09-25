"""Regenerate app/data/constituent_reference.json — Constituent
Alignment's bundled pre-first-run seat expectation.

The seat-relative vote component compares a member's break rate with the
break rate same-party members of their chamber show at the same seat lean.
The pipeline measures that expectation from the members it is about to
score on every run (score_calculator.compute_constituent_reference) and
writes /data/constituent_reference.json, which takes precedence; this
bundled file only matters before a deployment's first run. It uses the
SAME constituent_reference_inputs + compute_constituent_reference the
pipeline does, on the same member dicts the score-breakdown API builds, so
the two can't differ. Per AGENTS.md §3a it writes the JSON directly —
nothing is pasted into code.

Run inside the backend container against a populated database, then
commit the regenerated file:
    docker compose run --rm --no-deps -v "$(pwd)/backend/scripts:/app/scripts" \\
        -e PYTHONPATH=/app backend python scripts/calibrate_constituent_reference.py
"""

import datetime
import json
import pathlib

from app.database import SessionLocal
from app.models import Representative, Senator
from app.pipeline.analyze.score_calculator import (
    compute_constituent_reference,
    constituent_reference_inputs,
)
from app.services._scorecard_common import build_score_breakdown_entity

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "constituent_reference.json"


def member_dicts(db, model, donation_attr: str) -> list[dict]:
    rows = db.query(model).filter(model.is_current.is_(True)).all()
    return [build_score_breakdown_entity(r, lobbying_donation_attr=donation_attr) for r in rows]


def main() -> None:
    existing = json.loads(OUT.read_text()) if OUT.exists() else {}
    out = {
        "_as_of": datetime.date.today().isoformat(),
        "_source": (
            "Pre-first-run fallback only: the pipeline recomputes this per chamber every run "
            "(score_calculator.compute_constituent_reference) and writes "
            "/data/constituent_reference.json, which takes precedence. Measured from the "
            "current members' stored votes by backend/scripts/calibrate_constituent_reference.py."
        ),
    }
    db = SessionLocal()
    try:
        chambers = {
            "senate": member_dicts(db, Senator, "donation_to_senator"),
            "house": member_dicts(db, Representative, "donation_to_representative"),
        }
    finally:
        db.close()
    for chamber, members in chambers.items():
        ref = compute_constituent_reference(constituent_reference_inputs(members))
        if ref is None:
            print(f"{chamber}: too few members with party-labeled votes; left unchanged")
            out[chamber] = existing.get(chamber)
            continue
        out[chamber] = ref
        print(f"{chamber}: {ref}")
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
