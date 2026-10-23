"""Fit the Action Center's near-identical title cut from pairs the data
labels, and write it to app/data/action_thresholds.json (AGENTS.md §3a).

The cut decides when two issues' titles alone make them one story
(action_center._same_story). Labels come only from what the stored issues
themselves record (action_center.near_identical_pairs): the same source
article, the same national monitor or the same bill is one story; issues
in different monitors on different days are two; and two feed articles
published within the rematch window that share a distinctive entity but
whose own text dates them days apart are two occasions
(action_center.stated_day_negatives, the clustering fit's label): an
issue's title is an outlet's headline. A pair none of those decides is left
out, never guessed. The cut is fitted the way the article
and claim cuts are (action_center.fit_near_identical_cut): the lowest
cosine no different-story pair reaches, plus CUT_MARGIN, and only with at
least MIN_HARD_NEGATIVES different-story pairs. The refresh refits it daily
on the last three weeks; this writes the bundled fallback from a whole
database.

The refit is compared with the cut in use on the same pairs, held out on
blocks of consecutive days, and adopted only under precision_rule.accept
(its held-out precision's lower bound no lower, its recall no more than one
standard error lower); otherwise the cut in use stays and the file says
why. Until 2026-10-23 the issues alone held too few different-story pairs
(1), and the cut in use was 0.92, hand-measured on 120 pairs in 2026-07
whose labels were not kept.

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
from app.models import ActionIssue, FeedArticle  # noqa: E402
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
        articles = [(r.title, r.description, r.published or r.seen_at)
                    for r in db.query(FeedArticle).filter(FeedArticle.kept.is_(True))]
        current = json.loads(ac._NEAR_IDENTICAL_PATH.read_text())
        in_use = current["values"]["near_identical"]
        fitted = ac.fit_near_identical_cut(issues, articles, in_use=in_use)
        vectors = ac._embed_texts_sim([r.title or "" for r in issues])
    adopted = fitted is not None and not fitted.get("kept")
    cut = fitted["threshold"] if adopted else in_use
    positives = [(i, j) for i, j, same in pairs if same]
    negatives = [(i, j) for i, j, same in pairs if not same]
    measured = {
        "issues": len(issues),
        "labelled_pairs": len(pairs),
        "same_story_pairs": len(positives),
        "different_story_pairs": len(negatives),
        "article_different_story_pairs": fitted["article_negatives"] if fitted else None,
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
        "monitor or bill is one story, different monitors on different days are two, and so are two feed articles "
        "about one actor whose own text dates them days apart (stated_day_negatives); other pairs left out. The "
        "lowest cosine no different-story pair reaches, plus CUT_MARGIN, with at least MIN_HARD_NEGATIVES such "
        "pairs, adopted only under precision_rule.accept against the cut in use (held out on blocks of days). "
        "The refresh refits it daily. "
        + ("Fitted and adopted on this snapshot." if adopted else
           f"Not adopted on this snapshot ({fitted['kept']}); the value in use ({in_use}) stands." if fitted else
           f"This snapshot held too few different-story pairs (under {ac.MIN_HARD_NEGATIVES}), so the value in use "
           f"({in_use}) stands.")
    )
    out = {"_source": source, "_as_of": utcnow().date().isoformat(), "values": {"near_identical": cut},
           "near_identical": measured}
    print(json.dumps(out, indent=1))
    if not args.dry_run:
        ac._NEAR_IDENTICAL_PATH.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
