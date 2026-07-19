"""Calibrate CROSSING_QUALITY_DISCOUNT (score_calculator.py) via grid
search against GROUND_TRUTH and the population stdev floor.

No natural continuous target exists for "how partisan was this crossing"
(unlike the FI small-donor baseline, which fits an OLS regression against
a real percentage), so this grid-searches candidate discount values and
reports a pass/fail matrix instead of fitting a formula — same
grid-search-against-ground-truth approach this file's own calibration
history uses whenever no continuous target is available.

REQUIRES a full pipeline run to have populated opposing_party_unity_pct
on KeyVote/RepKeyVote rows first (see CROSSING_QUALITY_DISCOUNT's
docstring in score_calculator.py — every existing vote row has this NULL
until then, making every candidate discount a no-op). This script will
report that plainly rather than produce a misleading result if run too
early.

Run inside the backend container (needs DB access):
    docker compose -f docker-compose.yml -f docker-compose.dev.yml \\
        run --rm --no-deps -v "$(pwd)/backend/scripts:/app/scripts" \\
        -e PYTHONPATH=/app backend python scripts/calibrate_crossing_quality.py

After finding the largest passing value, paste it into
CROSSING_QUALITY_DISCOUNT in score_calculator.py with a comment citing
this script and the date, matching this file's existing convention.
"""

import statistics

from app.database import SessionLocal
from app.models import KeyVote, Senator
from app.pipeline.analyze import score_calculator
from app.pipeline.analyze.ground_truth import GROUND_TRUTH, MIN_STDEV
from app.services.senator_service import get_senator_score_breakdown

# The 0.65-1.0 unity band is fixed by compute_party_vote_split's 65/35
# labeling threshold, not a free parameter — only the discount magnitude
# is swept. 0.0 is included as the current/no-op anchor.
CANDIDATE_DISCOUNTS = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8]

IV_GROUND_TRUTH = [
    (fragment, lo, hi, rationale)
    for fragment, dim, (lo, hi), rationale in GROUND_TRUTH
    if dim == "score_independent_voting"
]


def main() -> None:
    db = SessionLocal()

    populated = db.query(KeyVote).filter(KeyVote.opposing_party_unity_pct.isnot(None)).count()
    if populated == 0:
        print(
            "No KeyVote rows have opposing_party_unity_pct populated yet — "
            "every candidate discount below is mathematically a no-op "
            "(avg_crossing_unity is None for everyone). Run a full pipeline "
            "pass first, then re-run this script."
        )
        db.close()
        return
    print(f"{populated} key_votes rows have opposing_party_unity_pct populated.\n")

    gt_senators = []
    for fragment, lo, hi, rationale in IV_GROUND_TRUTH:
        senator = db.query(Senator).filter(Senator.name.like(f"%{fragment}%")).first()
        if senator is None:
            print(f"SKIP: ground-truth senator {fragment!r} not found")
            continue
        gt_senators.append((senator.id, senator.name, lo, hi, rationale))

    all_senator_ids = [s.id for s in db.query(Senator).all()]
    floor = MIN_STDEV["score_independent_voting"]

    print(f"{'discount':>8}  {'gt pass':>8}  {'stdev':>8}  {'verdict'}")
    results = []
    for discount in CANDIDATE_DISCOUNTS:
        score_calculator.CROSSING_QUALITY_DISCOUNT = discount

        failures = []
        for sid, name, lo, hi, rationale in gt_senators:
            breakdown = get_senator_score_breakdown(db, sid)
            iv = breakdown["independentVoting"]["score"]
            if not (lo <= iv <= hi):
                failures.append((name, iv, lo, hi, rationale))

        all_scores = [
            get_senator_score_breakdown(db, sid)["independentVoting"]["score"]
            for sid in all_senator_ids
        ]
        stdev = statistics.pstdev(all_scores)

        gt_pass = len(gt_senators) - len(failures)
        verdict = "PASS" if not failures and stdev >= floor else "FAIL"
        print(f"{discount:>8.2f}  {gt_pass:>4}/{len(gt_senators):<3}  {stdev:>8.2f}  {verdict}")
        for name, iv, lo, hi, rationale in failures:
            print(f"           - {name}: IV={iv:.0f} outside [{lo},{hi}] ({rationale})")
        results.append((discount, verdict))

    passing = [d for d, v in results if v == "PASS"]
    if passing:
        print(f"\nLargest passing discount: {max(passing):.2f}")
    else:
        print(
            "\nNo candidate discount passed every check — do not ship a "
            "nonzero discount until this is resolved; bring the failures "
            "back for a design review rather than loosening ground truth."
        )

    db.close()


if __name__ == "__main__":
    main()
