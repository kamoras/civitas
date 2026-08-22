"""Retire redundant ActionIssue rows already live under is_current=True.

2026-08-22: _find_matching_issue's near-identical-title check (see
_NEAR_IDENTICAL_TITLE_THRESHOLD in action_center.py) was added after a
cluster-matching gap let a reworded-but-same-story headline create a
second row instead of updating the first — e.g. "Trump defends beef
import plan amid GOP criticism" got three near-duplicate rows over a few
hours. That fix only prevents NEW duplicates; this is the one-time
cleanup for rows already created before it shipped.

Within each cluster of currently-live near-duplicate titles (same
dedupe_near_identical_issues logic the live matcher and the homepage's
recent-issues endpoint use), keeps the most recently created row — the
freshest write-up of the story — and retires (is_current=False) the
rest. Does NOT touch anything already posted to Bluesky; a post that
already went out publicly stays as-is, this only changes what the
website currently lists as live.

Run from the repo:
    python3 backend/scripts/retire_duplicate_current_issues.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import ActionIssue  # noqa: E402
from app.pipeline.analyze.action_center import dedupe_near_identical_issues  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(ActionIssue)
            .filter(ActionIssue.is_current == True)  # noqa: E712
            .all()
        )
        if len(rows) < 2:
            print(f"Only {len(rows)} current issue(s) — nothing to dedupe.")
            return

        kept = set(dedupe_near_identical_issues(rows))
        retired = 0
        for row in rows:
            if row in kept:
                continue
            print(f"  retiring [{row.id}] {row.title!r}")
            row.is_current = False
            retired += 1
        db.commit()
        print(f"Retired {retired} duplicate row(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
