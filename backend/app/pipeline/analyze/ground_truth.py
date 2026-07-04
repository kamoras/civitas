"""Ground-truth regression gate for scoring algorithms.

Checks a small set of reference senators — chosen because their public
record makes certain score ranges externally verifiable — against the
freshly computed scores at the end of each pipeline run. A failure means
either a data-fetch regression (e.g., PAC totals silently dropping to
zero) or an algorithm change with unintended effects, and should be
investigated before trusting the run.

This exists because the 2026-06 score audit found that reference senators
had been failing these expectations across two algorithm versions without
anyone noticing: the checks lived in a manual audit playbook rather than
in the pipeline. Run automatically after score snapshots; failures are
logged as warnings (non-fatal — data still publishes, but the run is
flagged).

Range rationale (see the score-audit skill for the investigation):
- Collins / Murkowski: the two most frequent party crossers in the
  chamber (33-36% break rates on contested votes) — IV must be high.
- Sanders / Warren: famously small-donor-funded, rejected corporate PAC
  money — FI must be high. Neither is a frequent party-line breaker, so
  IV is mid-range.
- Cruz: high party loyalty (≈4% break rate) — IV must be low-to-mid.
  Large small-dollar base keeps FI mid-to-high.
- McConnell: party leader with a single-digit break rate — IV must be
  low-to-mid. NOTE: his *recent-window* candidate-committee profile
  (7% PAC ratio, 36% unitemized, minimal supporting Schedule E) is
  genuinely mid-pack, so the FI ceiling here is deliberately loose;
  leadership-PAC/party-apparatus influence is not visible in candidate
  FEC data and should not be faked into the score.
- Paul: libertarian with a well-documented cross-party streak (≈16%
  break rate) and small-dollar base — IV and FI both above the median.
- Klobuchar: moderate profile on both dimensions; her prior FI of 31 was
  an artifact of counting her own victory committees as top donors.
"""

import logging

logger = logging.getLogger(__name__)

# (name_fragment, dimension_attr, (min, max), rationale)
# IV ranges updated 2026-07-04 for v4.2 Constituent Alignment: the score
# is now relative to the seat's expected break rate, so loyalists in
# aligned seats center near 50 ("typical partisan for this seat") rather
# than pinning at the old ≤3% floor of ~26-38. Frequent crossers whose
# crossing tracks their state (Collins/Murkowski) still score high.
GROUND_TRUTH: list[tuple[str, str, tuple[int, int], str]] = [
    ("Collins",   "score_independent_voting",    (70, 100), "≈36% breaks, D-lean state — crossing IS representation"),
    ("Murkowski", "score_independent_voting",    (70, 100), "≈33% breaks, independent-streak state"),
    ("Sanders",   "score_independent_voting",    (35, 75),  "caucuses D, ≈8% breaks in deep-D VT ≈ slightly above expectation"),
    ("Sanders",   "score_funding_independence",  (70, 100), "small-donor base, ~0% PAC"),
    ("Warren",    "score_independent_voting",    (35, 70),  "≈7% breaks in deep-D MA ≈ slightly above expectation"),
    ("Warren",    "score_funding_independence",  (70, 100), "rejected corporate PAC money"),
    ("Cruz",      "score_independent_voting",    (30, 60),  "≈4% breaks in R+8 TX ≈ typical partisan for the seat"),
    ("Cruz",      "score_funding_independence",  (50, 95),  "large small-dollar base"),
    ("McConnell", "score_independent_voting",    (30, 60),  "party leader, ≈9% breaks in R+16 KY ≈ at/above seat expectation; must NOT exceed 60 (2026-06 audit trap)"),
    ("McConnell", "score_funding_independence",  (30, 90),  "recent-window profile is mid-pack; see module docstring"),
    ("Paul",      "score_independent_voting",    (55, 95),  "≈16% breaks, far beyond safe-seat expectation (discounted credit)"),
    ("Paul",      "score_funding_independence",  (60, 100), "small-dollar base"),
    ("Klobuchar", "score_independent_voting",    (40, 70),  "≈10% breaks in D+2 MN ≈ slightly above seat expectation"),
    ("Klobuchar", "score_funding_independence",  (35, 75),  "mid-range PAC reliance, no capture signal"),
]

_DIM_LABEL = {
    "score_independent_voting": "IV",
    "score_funding_independence": "FI",
}


def check_ground_truth(db) -> dict:
    """Check reference senators against expected score ranges.

    Args:
        db: SQLAlchemy session.

    Returns:
        {"checked": int, "failures": [ {senator, dimension, score,
         expected: [lo, hi], rationale}, ... ]}
    """
    from app.models import Senator

    failures: list[dict] = []
    checked = 0

    for fragment, dim, (lo, hi), rationale in GROUND_TRUTH:
        senator = (
            db.query(Senator)
            .filter(Senator.name.like(f"%{fragment}%"))
            .first()
        )
        if senator is None:
            logger.warning(
                "GROUND TRUTH: reference senator %r not found — skipping",
                fragment,
            )
            continue

        score = getattr(senator, dim, None)
        if score is None:
            logger.warning(
                "GROUND TRUTH: %s has no %s — skipping", senator.name, dim,
            )
            continue

        checked += 1
        if not (lo <= score <= hi):
            failures.append({
                "senator": senator.name,
                "dimension": _DIM_LABEL.get(dim, dim),
                "score": score,
                "expected": [lo, hi],
                "rationale": rationale,
            })
            logger.warning(
                "GROUND TRUTH FAIL: %s %s=%.0f outside [%d, %d] (%s)",
                senator.name, _DIM_LABEL.get(dim, dim), score, lo, hi,
                rationale,
            )

    if not failures:
        logger.info(
            "Ground truth: %d/%d reference checks passed", checked, checked,
        )
    else:
        logger.warning(
            "Ground truth: %d/%d reference checks FAILED — investigate "
            "before trusting this run's scores",
            len(failures), checked,
        )

    return {"checked": checked, "failures": failures}
