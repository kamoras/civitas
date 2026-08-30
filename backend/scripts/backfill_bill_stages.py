"""Backfill the `stage` column on already-stored sponsored bills.

Fetches each bill's actual action history from Congress.gov
(GET /bill/{congress}/{type}/{number}/actions) and classifies stage from
the structured actionCode, via app.pipeline.analyze.bill_stage. Responses
are cached (api_cache), so a re-run only re-fetches bills the cache
doesn't already have. Idempotent — safe to re-run any time the actionCode
mapping table changes.

This makes one Congress.gov API call per bill that isn't already cached,
rate-limited to CONGRESS_RPS (~1.2 req/s) — expect this to take a few
hours cold against a large existing dataset. Progress commits every 200
rows so an interruption doesn't lose prior work.

Run from the repo:
    python3 backend/scripts/backfill_bill_stages.py
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import RepSponsoredBill, SponsoredBill  # noqa: E402
from app.pipeline.analyze.bill_stage import classify_bill_stage_from_actions  # noqa: E402
from app.pipeline.fetch.congress import fetch_bill_actions  # noqa: E402


async def _backfill_model(client: httpx.AsyncClient, db, model) -> tuple[int, int]:
    rows = db.query(model).filter(model.congress > 0).all()
    updated = 0
    for i, row in enumerate(rows, start=1):
        bill_number_str = "".join(ch for ch in row.bill_id.split(".")[-1] if ch.isdigit())
        if not bill_number_str or not row.bill_type:
            continue
        actions = await fetch_bill_actions(
            client, db, row.congress, row.bill_type.lower(), int(bill_number_str),
        )
        new_stage = classify_bill_stage_from_actions(actions, row.is_law)
        if row.stage != new_stage:
            row.stage = new_stage
            updated += 1
        if i % 200 == 0:
            db.commit()
            print(f"  {model.__tablename__}: {i}/{len(rows)} ({updated} changed so far)", flush=True)
    db.commit()
    return len(rows), updated


async def main() -> None:
    db = SessionLocal()
    try:
        total_updated = 0
        async with httpx.AsyncClient(timeout=30) as client:
            for model in (SponsoredBill, RepSponsoredBill):
                n, updated = await _backfill_model(client, db, model)
                total_updated += updated
                print(f"{model.__tablename__}: classified {n} rows, {updated} changed", flush=True)
        print(f"Updated {total_updated} bill rows with a stage.")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
