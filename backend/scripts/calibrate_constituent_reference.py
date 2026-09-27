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
from app.pipeline.analyze.population_reference import CONSTITUENT_REFERENCE
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
            "/data/constituent_reference.json, which takes precedence. Each chamber's entry is "
            "either measured from the current members' stored votes by "
            "backend/scripts/calibrate_constituent_reference.py or kept from the previous file; "
            "_provenance says which. A kept hand-set prior carries the current statistic stamp "
            "on purpose (it is not a measurement of any statistic, so it stays usable)."
        ),
        "_provenance": {},
    }
    db = SessionLocal()
    try:
        chambers = {
            "senate": member_dicts(db, Senator, "donation_to_senator"),
            "house": member_dicts(db, Representative, "donation_to_representative"),
        }
    finally:
        db.close()
    measured_now = False
    for chamber, members in chambers.items():
        ref = compute_constituent_reference(constituent_reference_inputs(members))
        if ref is None:
            kept = existing.get(chamber)
            if not CONSTITUENT_REFERENCE.usable(kept):
                # Keep the other chamber's fresh measurement; leave this one
                # out rather than write an entry load() would skip anyway.
                print(
                    f"WARNING {chamber}: too few full-confidence members to measure, and the "
                    f"previous entry isn't usable (missing, or measured on "
                    f"{(kept or {}).get('statistic')!r}, not {CONSTITUENT_REFERENCE.statistic!r}); "
                    "left out — before its first measured run this chamber scores neutral"
                )
                out["_provenance"][chamber] = "missing: no usable entry to keep"
                continue
            print(f"{chamber}: too few full-confidence members; kept the previous entry")
            out[chamber] = kept
            out["_provenance"][chamber] = (existing.get("_provenance") or {}).get(
                chamber, f"kept from the file as of {existing.get('_as_of', 'unknown')}"
            )
            continue
        out[chamber] = ref
        out["_provenance"][chamber] = f"measured {out['_as_of']}"
        measured_now = True
        print(f"{chamber}: {ref}")
    if not measured_now:
        # Nothing was measured: keep the file's own date and description.
        out["_as_of"] = existing.get("_as_of", out["_as_of"])
        out["_source"] = existing.get("_source", out["_source"])
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
