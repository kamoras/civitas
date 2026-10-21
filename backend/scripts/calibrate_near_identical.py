"""Fit the Action Center's near-identical title cut from pairs the data
labels, and write it to app/data/action_thresholds.json (AGENTS.md §3a).

The cut decides when two issues' titles alone make them one story
(action_center._same_story). Labels come only from what the stored issues
themselves record (action_center.near_identical_pairs): the same source
article, the same national monitor or the same bill is one story; issues
in different monitors on different days are two. A pair none of those
decides is left out, never guessed. The cut is fitted the way the article
and claim cuts are (action_center.fit_near_identical_cut): the lowest
cosine no different-story pair reaches, plus CUT_MARGIN, and only with at
least MIN_HARD_NEGATIVES different-story pairs. The refresh refits it daily
on the last three weeks; this writes the bundled fallback from a whole
database.

When the data hold too few different-story pairs, the cut in use is kept
and the file says how many there are: the bundled 0.92 was hand-measured
on 120 pairs in 2026-07 and its labels were not kept, so it can't be
recomputed until the data supply enough pairs of their own.

Usage (loads the similarity model; no network once it is cached):
    DATABASE_URL=sqlite:////path/to/snapshot.db python backend/scripts/calibrate_near_identical.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import ActionIssue  # noqa: E402
from app.pipeline.analyze import action_center as ac  # noqa: E402
from app.time_utils import utcnow  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        issues = db.query(ActionIssue).filter(ActionIssue.source_type.is_(None)).order_by(ActionIssue.id).all()
        pairs = ac.near_identical_pairs(issues)
        labels = sorted((issues[i].id, issues[j].id, same) for i, j, same in pairs)
        fitted = ac.fit_near_identical_cut(issues)
        vectors = ac._embed_texts_sim([r.title or "" for r in issues])
    current = json.loads(ac._NEAR_IDENTICAL_PATH.read_text())
    in_use = current["values"]["near_identical"]
    cut = fitted["threshold"] if fitted else in_use
    positives = [(i, j) for i, j, same in pairs if same]
    negatives = [(i, j) for i, j, same in pairs if not same]
    measured = {
        "issues": len(issues),
        "labelled_pairs": len(pairs),
        "same_story_pairs": len(positives),
        "different_story_pairs": len(negatives),
        "different_story_pairs_needed": ac.MIN_HARD_NEGATIVES,
        "fitted": fitted,
        "same_story_joined_at_cut": sum(float(vectors[i] @ vectors[j]) >= cut for i, j in positives),
        "different_story_joined_at_cut": sum(float(vectors[i] @ vectors[j]) >= cut for i, j in negatives),
        "labels_sha256": hashlib.sha256(json.dumps(labels).encode()).hexdigest(),
    }
    source = (
        "near_identical: the title cosine (similarity model) at or above which action_center._same_story calls "
        "two issues one story. Fitted by backend/scripts/calibrate_near_identical.py (action_center."
        "fit_near_identical_cut) on pairs the stored issues label themselves: the same source article, national "
        "monitor or bill is one story, different monitors on different days are two, other pairs left out; the "
        "lowest cosine no different-story pair reaches, plus CUT_MARGIN, with at least MIN_HARD_NEGATIVES such "
        "pairs. The refresh refits it daily. "
        + ("Fitted on this snapshot." if fitted else
           f"This snapshot held {len(negatives)} different-story pairs, under {ac.MIN_HARD_NEGATIVES}, so the "
           f"value in use ({in_use}, hand-measured on 120 pairs in 2026-07, labels not kept) stands until the data "
           f"supply enough.")
    )
    out = {"_source": source, "_as_of": utcnow().date().isoformat(), "values": {"near_identical": cut},
           "near_identical": measured}
    print(json.dumps(out, indent=1))
    if not args.dry_run:
        ac._NEAR_IDENTICAL_PATH.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
