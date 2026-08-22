"""Retire redundant ActionIssue rows already live under is_current=True.

2026-08-22: _find_matching_issue's near-identical-title check (see
_NEAR_IDENTICAL_TITLE_THRESHOLD in action_center.py) was added after a
cluster-matching gap let a reworded-but-same-story headline create a
second row instead of updating the first — e.g. "Trump defends beef
import plan amid GOP criticism" got three near-duplicate rows over a few
hours. That fix only prevents NEW duplicates; this is the one-time
cleanup for rows already created before it shipped.

Within each cluster of currently-live near-duplicate titles (same
_is_exact_content_duplicate / near-identical-title logic the live
matcher uses), keeps the most recently created row — the freshest
write-up of the story — and retires (is_current=False) the rest. Does
NOT touch anything already posted to Bluesky; a post that already went
out publicly stays as-is, this only changes what the website currently
lists as live.

Run from the repo:
    python3 backend/scripts/retire_duplicate_current_issues.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import json  # noqa: E402

import numpy as np  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ActionIssue  # noqa: E402
from app.pipeline.analyze.action_center import (  # noqa: E402
    _NEAR_IDENTICAL_TITLE_THRESHOLD,
    _embed_texts_sim,
    _is_exact_content_duplicate,
)


def _is_duplicate(a: ActionIssue, b: ActionIssue, sim: float) -> bool:
    try:
        a_facts, b_facts = json.loads(a.facts or "[]"), json.loads(b.facts or "[]")
    except (ValueError, TypeError):
        a_facts, b_facts = [], []
    return sim >= _NEAR_IDENTICAL_TITLE_THRESHOLD or _is_exact_content_duplicate(
        a.title or "", a_facts, b.title or "", b_facts,
    )


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

        embs = np.array(_embed_texts_sim([r.title or "" for r in rows]))
        sims = embs @ embs.T

        # Union-find: a chain of pairwise near-duplicates (603~604, 604~605)
        # must collapse into one cluster even if 603~605 alone were just
        # under the bar.
        parent = list(range(len(rows)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if _is_duplicate(rows[i], rows[j], float(sims[i, j])):
                    union(i, j)

        clusters: dict[int, list[int]] = {}
        for idx in range(len(rows)):
            clusters.setdefault(find(idx), []).append(idx)

        retired = 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            group = [rows[i] for i in members]
            group.sort(key=lambda r: r.created_at or r.id, reverse=True)
            keep, drop = group[0], group[1:]
            print(f"Keeping [{keep.id}] {keep.title!r}")
            for row in drop:
                print(f"  retiring [{row.id}] {row.title!r} (duplicate of {keep.id})")
                row.is_current = False
                retired += 1
        db.commit()
        print(f"Retired {retired} duplicate row(s) across {sum(1 for m in clusters.values() if len(m) > 1)} cluster(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
