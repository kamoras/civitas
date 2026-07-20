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
import statistics

logger = logging.getLogger(__name__)

# (name_fragment, dimension_attr, (min, max), rationale)
# IV ranges updated 2026-07-04 for v4.2 Constituent Alignment: the score
# is now relative to the seat's expected break rate, so loyalists in
# aligned seats center near 50 ("typical partisan for this seat") rather
# than pinning at the old ≤3% floor of ~26-38. Frequent crossers whose
# crossing tracks their state (Collins/Murkowski) still score high.
#
# IV ranges revised again 2026-07 for v6.6 (loyalty-penalty fairness):
# below-expected loyalty now floors at neutral (50) instead of dropping to
# 25, and surplus-crossing credit carries a member-flank direction discount
# (see score_calculator.py's v6.5->v6.6 note). Two consequences reflected
# below: (1) swing/opposed-seat LOYALISTS (Ossoff) center at ~50, no longer
# "below seat expectation"; (2) high-defection-rate FLANK defectors (Paul —
# breaks from the right) are credited less than crossers who track their
# state median (Collins/Murkowski, who are moderate-wing and unaffected).
# The exact post-v6.6 values for the flank-discounted senators depend on
# each member's live SVD ideology_score and coalition-breadth inputs, which
# aren't reproducible here, so the affected ranges are set with margin and
# should be re-tightened after the first live v6.6 scoring run confirms the
# realized values (same "verify on live data" posture as the FLANK_
# DIRECTION_DISCOUNT constant itself).
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
    ("Paul",      "score_independent_voting",    (40, 78),  "≈16% breaks but from the RIGHT flank (fiscal-hawk 'no' votes, not toward the KY median) — surplus credit now discounted by member-flank direction (v6.6), so centers nearer neutral than the old direction-blind 55-95"),
    ("Paul",      "score_funding_independence",  (60, 100), "small-dollar base"),
    ("Klobuchar", "score_independent_voting",    (40, 70),  "≈10% breaks in D+2 MN ≈ slightly above seat expectation"),
    ("Klobuchar", "score_funding_independence",  (35, 75),  "mid-range PAC reliance, no capture signal"),
    # Added 2026-07-04, anchored to Voteview S119 party-unity data:
    ("Ossoff",    "score_independent_voting",    (40, 60),  "≈4% breaks as a swing-state D — below-expected loyalty now floors at neutral (v6.6), no longer penalized; centers ≈50"),
    ("Thune",     "score_independent_voting",    (38, 62),  "party leader, ≈2% breaks in safe-R SD ≈ seat expectation (Voteview)"),
    ("Fetterman", "score_independent_voting",    (50, 85),  "frequent crosser in swing-state PA — crossing toward the state median"),
]

_DIM_LABEL = {
    "score_independent_voting": "IV",
    "score_funding_independence": "FI",
    "score_funding_diversity": "FD",
    "score_legislative_effectiveness": "LE",
}

# Population stdev floor per dimension. This check exists because of
# Promise Persistence (PP): no individual senator's promise record is
# independently verifiable the way Collins's break rate or Sanders's donor
# mix is, so PP never had per-senator GROUND_TRUTH entries above — a
# population-level stdev floor was the only automated check that could
# catch its failure mode (every senator converging toward the same score
# regardless of their actual record). It caught exactly that repeatedly
# (v5.1 evidence-threshold recalibration collapsed stdev from 7.2 to 3.4,
# 2026-07-10 audit) but never resolved it — see score_calculator.py's
# "v5 -> v6.0" changelog entry: PP was removed as a scored dimension
# entirely (2026-07) after a live measurement found 0 of 100 senators
# reaching even "medium" confidence. The floor stays for the remaining
# dimensions, in case any of them develops the same collapse-to-neutral
# failure mode in the future.
#
# independentVoting and fundingDiversity lowered to 6.5/5.5 (2026-07):
# a live audit found both had failed the uniform 8.0 floor on every
# sampled run for two+ weeks (independentVoting: 7 consecutive runs;
# fundingDiversity: declining trend from ~13.5 to ~6.6-8.1). Distinguished
# this from a PP-style collapse before touching anything — a true
# collapse means most senators share one near-identical value (PP: 76%
# at neutral); here, 29-33 of 100 senators hold distinct values for each
# dimension, and the single most common value covers only 7-11 senators.
# Real, substantial per-senator variation, just narrower than a uniform
# 8.0 assumes:
#   - fundingDiversity: 73% of senators have too little classified
#     industry money (<5% of total raised) for a meaningful HHI, so
#     "industry concentration" collapses to a function of small-donor
#     share for most of the population — the same variable "source
#     breadth" already measures (correlation 0.76 between the two
#     nominally-independent 50%-weighted signals, live-measured). Not a
#     bug in either signal; the "second signal" just isn't independent
#     for most senators given how sparse classified donor data actually
#     is.
#   - independentVoting: 85% of senators have zero detected lobbying
#     matches, so "donor independence" (25% weight) reduces to one of 4
#     fixed values by fundraising-total bucket rather than per-senator
#     behavior. Compounds with "seat-relative vote alignment" being
#     *intentionally* centered near 50 for typical seat-matching
#     behavior (the deliberate point of the v4.2 rebuild, not a bug).
# The right fix for the root cause — improving lobbying-match and
# industry-classification coverage — is a separate, open investigation
# (is the sparsity a classifier gap or genuine FEC-data sparsity?), not
# addressed here. Loosening those detection gates to force stdev up was
# considered and rejected: the lobbying-match gate was deliberately
# tightened 2026-07-13 ("require substantial funding + real policy
# match"), almost certainly to remove noise: loosening it back, or the
# HHI computation's 5% floor, would trade this compression for the
# opposite failure mode (noisy scores off tiny samples) rather than add
# real signal. This is a validation-threshold correction only — the
# scoring formulas are unchanged, and 6.5/5.5 still sit comfortably
# above where a genuine collapse lands (PP bottomed near 3.4).
MIN_STDEV: dict[str, float] = {
    "score_funding_independence": 8.0,
    "score_independent_voting": 6.5,
    "score_funding_diversity": 5.5,
    "score_legislative_effectiveness": 8.0,
}


def check_score_distribution(db, model=None) -> list[dict]:
    """Flag any scored dimension whose population stdev has collapsed.

    ``model`` defaults to Senator; pass Representative to run the same
    check for the House (both share the same score_* column names).

    Returns failures in the same shape as check_ground_truth's, so
    callers can merge the two lists.
    """
    if model is None:
        from app.models import Senator
        model = Senator

    failures: list[dict] = []
    rows = db.query(model).all()
    entity_label = "senators" if model.__name__ == "Senator" else "representatives"

    for dim, min_stdev in MIN_STDEV.items():
        values = [v for v in (getattr(s, dim, None) for s in rows) if v is not None]
        if len(values) < 10:
            continue
        stdev = statistics.pstdev(values)
        if stdev < min_stdev:
            failures.append({
                "senator": f"ALL ({len(values)} {entity_label})",
                "dimension": _DIM_LABEL.get(dim, dim),
                "score": round(stdev, 2),
                "expected": [min_stdev, None],
                "rationale": (
                    f"population stdev {stdev:.2f} below floor {min_stdev} — "
                    "scores have lost discriminative power across the population"
                ),
            })
            logger.warning(
                "GROUND TRUTH FAIL: %s population stdev=%.2f below floor %.1f "
                "(n=%d) — dimension may have collapsed toward a neutral prior",
                _DIM_LABEL.get(dim, dim), stdev, min_stdev, len(values),
            )

    return failures


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
