"""Calibrate FLANK_DIRECTION_DISCOUNT (score_calculator.py) via grid search
against GROUND_TRUTH and the population stdev floor.

Constituent Alignment's v6.6 crossing-reward discount scales surplus-crossing
credit down for members on their party's ideological FLANK (whose crossings
most plausibly point away from the state median — Kirkland & Slapin 2017),
using the party-blind SVD ideology_score. Like CROSSING_QUALITY_DISCOUNT,
there is no natural continuous target to regress against ("how far toward the
flank should fully neutralize the directional presumption?" is a judgment),
so this grid-searches candidate magnitudes and reports a pass/fail matrix —
the same grid-search-against-ground-truth approach the file's calibration
history uses whenever no continuous target exists.

Unlike calibrate_crossing_quality.py, this does NOT require a fresh pipeline
run first: ideology_score is populated on every scoring run already, so every
candidate magnitude below is immediately live. The shipped default (0.5) is a
research-informed prior; run this against a real scored database to confirm it
best separates known flank-defectors (Paul, Sanders) from known
median-directed crossers (Collins, Murkowski) without collapsing IV spread
below its stdev floor, and adjust the constant + the flank-affected
GROUND_TRUTH ranges (Paul, etc.) to the realized values.

Run inside the backend container (needs DB access):
    docker compose -f docker-compose.yml -f docker-compose.dev.yml \\
        run --rm --no-deps -v "$(pwd)/backend/scripts:/app/scripts" \\
        -e PYTHONPATH=/app backend python scripts/calibrate_flank_direction.py

After confirming the best value, paste it into FLANK_DIRECTION_DISCOUNT in
score_calculator.py with a comment citing this script and the date, matching
this file's existing convention.
"""

import statistics

from app.database import SessionLocal
from app.models import Senator
from app.pipeline.analyze import score_calculator
from app.pipeline.analyze.ground_truth import GROUND_TRUTH, MIN_STDEV
from app.services.senator_service import get_senator_score_breakdown

# 0.0 is the no-op anchor (direction-blind, pre-v6.6 reward behavior); 0.5 is
# the shipped prior. Only the magnitude is swept — the flank mapping itself
# (_flank_extremity, measured against the 0.5 center) is fixed.
CANDIDATE_DISCOUNTS = [0.0, 0.25, 0.5, 0.6, 0.75, 1.0]

IV_GROUND_TRUTH = [
    (fragment, lo, hi, rationale)
    for fragment, dim, (lo, hi), rationale in GROUND_TRUTH
    if dim == "score_independent_voting"
]


def main() -> None:
    db = SessionLocal()

    scored = db.query(Senator).filter(Senator.ideology_score.isnot(None)).count()
    total = db.query(Senator).count()
    if scored == 0:
        print(
            "No senators have ideology_score populated — every candidate "
            "discount below is a no-op (flank_extremity is 0.0 for everyone). "
            "Run a full pipeline pass so sponsorship_analysis.compute_ideology_"
            "scores populates it, then re-run this script."
        )
        db.close()
        return
    print(f"{scored}/{total} senators have ideology_score populated.\n")

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
        score_calculator.FLANK_DIRECTION_DISCOUNT = discount

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
        print(f"\nPassing discounts: {[f'{d:.2f}' for d in passing]}")
        print(
            "Pick the value that best separates known flank-defectors from "
            "median-directed crossers among the passing set (not necessarily "
            "the largest); confirm against the score-audit outlier check."
        )
    else:
        print(
            "\nNo candidate discount passed every check — bring the failures "
            "back for a design review (re-check the flank-affected GROUND_TRUTH "
            "ranges first; they were set with margin pending this run) rather "
            "than silently loosening ground truth."
        )

    db.close()


if __name__ == "__main__":
    main()
