"""Strip raw HTML out of RaceCoverageItem.summary rows ingested before
_strip_html() existed (news_feeds.py, added 2026-07-31).

election_coverage.py's ingestion has applied _strip_html to every summary
since that date, but _store_if_new dedupes permanently by (race_id, url) —
an already-stored row is never re-fetched, so anything ingested in the
one-week window before the fix (2026-07-24 onward) still holds raw feed
markup (e.g. a WordPress <img> lead) verbatim. _strip_html is idempotent,
so this is safe to re-run any time.

Only source_type="news" needs this — Bluesky summaries are post text, never
HTML.

Run from the repo:
    python3 backend/scripts/backfill_race_coverage_summary_html.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import RaceCoverageItem  # noqa: E402
from app.pipeline.fetch.news_feeds import MAX_SUMMARY_CHARS, _strip_html  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.query(RaceCoverageItem).filter(RaceCoverageItem.source_type == "news").all()
        updated = 0
        for row in rows:
            cleaned = _strip_html(row.summary or "")[:MAX_SUMMARY_CHARS]
            if cleaned != row.summary:
                row.summary = cleaned
                updated += 1
        db.commit()
        print(f"Checked {len(rows)} news coverage rows, cleaned {updated}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
