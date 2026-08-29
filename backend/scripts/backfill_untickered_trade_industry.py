"""Backfill `industry` on already-stored untickered stock trade rows.

2026-08: untickered-line classification (crypto has no SEC ticker to
resolve at all) used to be an opt-in flag, on only for presidential
278-Ts — House/Senate rows never attempted it, and the CRYPTO industry
prototype itself was too thin to catch several real coin names (see
industry_classifier.py's CRYPTO entry and stock_pipeline.py's
_classify_rows_industry). Both are now fixed going forward for newly
ingested rows; this backfills rows already sitting in the database as
UNCLASSIFIED. Idempotent and safe to re-run — it only ever touches rows
still UNCLASSIFIED, so a name the classifier still can't place (an
obscure coin, a genuinely non-tradeable holding) is left exactly as it
was, not overwritten with a bad guess.

Run from the repo:
    python3 backend/scripts/backfill_untickered_trade_industry.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import PresidentTrade, RepStockTrade, StockTrade  # noqa: E402
from app.pipeline.transform.industry_classifier import classify_batch_with_learning  # noqa: E402


def _backfill_model(db, model) -> tuple[int, int]:
    rows = (
        db.query(model)
        .filter(model.ticker.is_(None), model.industry == "UNCLASSIFIED")
        .all()
    )
    if not rows:
        return 0, 0

    names = list({r.asset_name.strip() for r in rows if r.asset_name and r.asset_name.strip()})
    industries, _unknowns = classify_batch_with_learning(names, db)

    updated = 0
    for row in rows:
        asset = (row.asset_name or "").strip()
        new_industry = industries.get(asset)
        if new_industry and new_industry != "UNCLASSIFIED":
            row.industry = new_industry
            updated += 1
    db.commit()
    return len(rows), updated


def main() -> None:
    db = SessionLocal()
    try:
        total_updated = 0
        for model in (PresidentTrade, RepStockTrade, StockTrade):
            n, updated = _backfill_model(db, model)
            total_updated += updated
            print(f"{model.__tablename__}: {n} UNCLASSIFIED untickered rows, {updated} reclassified", flush=True)
        print(f"Updated {total_updated} trade rows with a real industry label.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
