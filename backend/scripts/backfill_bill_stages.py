"""Backfill the `stage` column on already-stored sponsored bills.

SponsoredBill/RepSponsoredBill rows created before the bill-stage
classifier existed have no `stage` value. Rather than wait for the next
full pipeline run (Senate ~6h, House ~1-2h) to repopulate it, this script
classifies `latest_action` directly against the same embedding prototypes
used by the pipeline (app.pipeline.analyze.bill_stage). Idempotent and
safe to re-run any time the stage prototypes change.

Run from the repo:
    python3 backend/scripts/backfill_bill_stages.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import RepSponsoredBill, SponsoredBill  # noqa: E402
from app.pipeline.analyze.bill_stage import classify_bill_stage  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        updated = 0
        for model in (SponsoredBill, RepSponsoredBill):
            rows = db.query(model).all()
            for row in rows:
                new_stage = classify_bill_stage(row.latest_action, row.is_law)
                if row.stage != new_stage:
                    row.stage = new_stage
                    updated += 1
            print(f"{model.__tablename__}: classified {len(rows)} rows")
        db.commit()
        print(f"Updated {updated} bill rows with a stage.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
