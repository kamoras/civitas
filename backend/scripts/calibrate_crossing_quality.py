"""Calibrate CROSSING_QUALITY_DISCOUNT (score_calculator.py) via grid
search against the derived consistency gate.

No natural continuous target exists for "how partisan was this crossing"
(unlike the FI small-donor baseline, which fits an OLS regression against
a real percentage), so this grid-searches candidate discount values and
reports a pass/fail matrix instead of fitting a formula. Each candidate
is judged by the same population-level checks the pipeline runs
(app/pipeline/analyze/ground_truth.py): recomputed IV scores must still
rank-track the observed break rate, the extreme deciles must still land
on the right side of the population, and the distribution must not
collapse toward a point mass — no named reference senators, no
hand-typed ranges (AGENTS.md 1/3a).

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

import copy
import statistics

from app.database import SessionLocal
from app.models import KeyVote, Senator
from app.pipeline.analyze import score_calculator
from app.pipeline.analyze.ground_truth import _member_records, evaluate_derived_checks
from app.services.senator_service import get_senator_score_breakdown

# The 0.65-1.0 unity band is fixed by compute_party_vote_split's 65/35
# labeling threshold, not a free parameter — only the discount magnitude
# is swept. 0.0 is included as the current/no-op anchor.
CANDIDATE_DISCOUNTS = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8]


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

    base_records = _member_records(db, Senator)

    print(f"{'discount':>8}  {'iv checks':>9}  {'stdev':>8}  {'verdict'}")
    results = []
    for discount in CANDIDATE_DISCOUNTS:
        score_calculator.CROSSING_QUALITY_DISCOUNT = discount

        records = copy.deepcopy(base_records)
        for r in records:
            breakdown = get_senator_score_breakdown(db, r["id"])
            r["scores"]["score_independent_voting"] = breakdown["independentVoting"]["score"]

        report = evaluate_derived_checks(records, "senators")
        failures = [f for f in report["failures"] if f["dimension"] == "IV"]

        all_scores = [r["scores"]["score_independent_voting"] for r in records]
        stdev = statistics.pstdev(all_scores)

        verdict = "PASS" if not failures else "FAIL"
        print(f"{discount:>8.2f}  {len(failures):>4} fail  {stdev:>8.2f}  {verdict}")
        for f in failures:
            print(f"           - {f['senator']}: {f['rationale']}")
        results.append((discount, verdict))

    passing = [d for d, v in results if v == "PASS"]
    if passing:
        print(f"\nLargest passing discount: {max(passing):.2f}")
    else:
        print(
            "\nNo candidate discount passed every check — do not ship a "
            "nonzero discount until this is resolved; bring the failures "
            "back for a design review rather than weakening the gate."
        )

    db.close()


if __name__ == "__main__":
    main()
