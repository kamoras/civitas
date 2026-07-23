"""Derived consistency gate for scoring algorithms.

Runs at the end of each pipeline run and flags results that have come
unmoored from the raw public records they are computed from. A failure
means either a data-fetch regression (e.g., PAC totals silently dropping
to zero) or an algorithm change with unintended effects, and should be
investigated before trusting the run. Failures are logged as warnings
(non-fatal — data still publishes, but the run is flagged).

Every expectation here is DERIVED from the current population's own raw
data at check time — no politician is named and no score range is
hand-typed (AGENTS.md principle 1 / 3a). A prior version of this module
kept a hand-maintained GROUND_TRUTH table of reference senators
(Collins/Sanders/McConnell/...) with per-senator score ranges, plus a
second drifting copy in scripts/rescore.py; that table went stale with
membership churn, encoded developer priors about specific people, and
violated the no-hardcoded-values principle. See git history for the
table and the 2026-06/2026-07 audit notes that motivated each range.

Three families of checks, all population-level:

1. Raw-input integrity — existence checks on the inputs scoring depends
   on (any receipts at all, any nonzero PAC totals among funded members,
   any party-labeled votes). Catches the fetch regressions the old named
   checks caught — a feed silently zeroing out — directly at the source,
   for whoever is currently in office.

2. Direction-of-effect — Spearman rank correlation between each score
   and an upstream raw metric it must track: Funding Independence must
   fall as the PAC share of receipts rises and rise with small-donor
   share; Independent Voting must rise with the observed party-break
   rate. "The most PAC-free members must score high on FI" is exactly
   what the old Sanders/Warren rows asserted, computed fresh each run
   for whoever currently holds that profile.

3. Extremes — Mann-Whitney U on the top/bottom decile by each raw
   metric: the currently most independent decile must score
   stochastically higher than the rest, and the least independent decile
   lower. The lower-tail test is the derived form of the old "McConnell
   must NOT exceed 60" audit trap, without naming a leader who will
   eventually retire.

check_score_distribution guards the failure mode per-member checks
can't see — the whole population collapsing toward one value (Promise
Persistence did exactly this before being removed as a scored dimension,
see score_calculator.py's v5→v6.0 changelog). The old fixed stdev floors
(8.0/6.5/5.5 — themselves hand-calibrated from live audits) are replaced
by two derived tests: a point-mass check (a strict majority sharing one
value is a collapse by definition) and a self-history check (today's
stdev an extreme low outlier vs. this algorithm version's own snapshot
history, by modified z-score).

The named constants below are statistical conventions and sample-size
guards (significance level, decile definition, Iglewicz–Hoaglin outlier
cutoff), not calibrated domain values — nothing in them encodes who is
in office or what any member should score.
"""

import logging
import math
import statistics
import warnings
from collections import Counter, defaultdict

from scipy import stats as scipy_stats

logger = logging.getLogger(__name__)

# ── Statistical conventions and sample-size guards ──────────────────────────
# One-sided significance level for rank tests (Fisher's conventional 0.05).
ALPHA = 0.05
# Minimum population for any check — too few rows to judge (same n≥10 guard
# the previous version of this module used).
MIN_POPULATION = 10
# Minimum party-labeled votes before a member's break rate is a usable
# denominator (same n≥10 sample-size convention).
MIN_LABELED_VOTES = 10
# Extremes tests need enough members for a meaningful decile split
# (rule-of-thumb minimum for the rank-sum test's normal approximation).
MIN_EXTREMES_POPULATION = 30
# "Extreme group" = a decile (ordinal definition, not a tuned value).
EXTREME_FRACTION = 0.10
# Modified z-score outlier cutoff (Iglewicz & Hoaglin 1993, "How to Detect
# and Handle Outliers", recommend 3.5) and its MAD normalization constant.
MODIFIED_Z_CUTOFF = 3.5
MAD_Z_SCALE = 0.6745
# Minimum distinct snapshot dates before the self-history stdev check runs.
MIN_HISTORY_DATES = 5

_DIM_LABEL = {
    "score_independent_voting": "IV",
    "score_funding_independence": "FI",
    "score_funding_diversity": "FD",
    "score_legislative_effectiveness": "LE",
}

# (metric key, score attr, expected rank direction, what the metric measures)
# direction +1: score must RISE with the metric; -1: score must FALL.
_CONSISTENCY_CHECKS: list[tuple[str, str, int, str]] = [
    ("pac_ratio", "score_funding_independence", -1,
     "PAC share of receipts (FEC)"),
    ("small_donor_pct", "score_funding_independence", +1,
     "small-donor share of receipts (FEC unitemized)"),
    ("party_break_rate", "score_independent_voting", +1,
     "observed party-break rate on labeled roll-call votes"),
]


def evaluate_derived_checks(members: list[dict], entity_label: str = "senators") -> dict:
    """Run the integrity + consistency checks over plain member records.

    Pure-data entry point shared by ``check_ground_truth`` (ORM) and
    ``scripts/rescore.py`` (shadow-scored payloads), so the shadow harness
    exercises the same gate instead of keeping a second copy.

    Each member record:
        {"name": str,
         "scores": {score_attr: float | None},
         "metrics": {"pac_ratio": float | None,
                     "small_donor_pct": float | None,
                     "party_break_rate": float | None},
         "raw": {"total_raised": float, "total_from_pacs": float,
                 "labeled_votes": int}}

    Returns {"checked": int, "failures": [{senator, dimension, score,
    expected, rationale}, ...]} — same failure shape the pipelines persist.
    """
    failures: list[dict] = []
    checked = 0
    n_all = len(members)

    if n_all < MIN_POPULATION:
        logger.info(
            "Derived checks: only %d %s — below n=%d minimum, skipping",
            n_all, entity_label, MIN_POPULATION,
        )
        return {"checked": 0, "failures": []}

    # ── 1. Raw-input integrity (existence checks — no thresholds) ──────────
    funded = [m for m in members if (m["raw"].get("total_raised") or 0) > 0]
    integrity_probes = [
        (
            not funded,
            "FI", "total_raised > 0 for someone",
            "every current {label} has zero total receipts — the funding "
            "fetch produced no data",
        ),
        (
            bool(funded) and all(
                (m["raw"].get("total_from_pacs") or 0) == 0 for m in funded
            ),
            "FI", "total_from_pacs > 0 for someone",
            "PAC totals are zero for every funded {label} — the exact "
            "silent-fetch regression this gate exists to catch",
        ),
        (
            bool(funded) and all(
                (m["metrics"].get("small_donor_pct") or 0) == 0 for m in funded
            ),
            "FI", "small_donor_pct > 0 for someone",
            "small-donor share is zero for every funded {label} — "
            "unitemized-contribution data missing from the fetch",
        ),
        (
            sum(m["raw"].get("labeled_votes") or 0 for m in members) == 0,
            "IV", "party-labeled votes exist",
            "no {label} has any party-labeled vote — vote fetch or "
            "party-labeling is producing nothing",
        ),
    ]
    for failed, dim_label, expectation, rationale in integrity_probes:
        checked += 1
        if failed:
            failures.append({
                "senator": f"ALL ({n_all} {entity_label})",
                "dimension": dim_label,
                "score": 0,
                "expected": [expectation, None],
                "rationale": rationale.format(label=entity_label[:-1]),
            })
            logger.warning(
                "DERIVED CHECK FAIL [%s]: %s", dim_label,
                rationale.format(label=entity_label[:-1]),
            )

    # ── 2 & 3. Rank consistency between scores and raw metrics ─────────────
    for metric, dim, direction, describes in _CONSISTENCY_CHECKS:
        label = _DIM_LABEL.get(dim, dim)
        pairs = [
            (m["metrics"].get(metric), m["scores"].get(dim), m["name"])
            for m in members
        ]
        pairs = [(x, y, nm) for x, y, nm in pairs if x is not None and y is not None]
        n = len(pairs)
        if n < MIN_POPULATION:
            logger.info(
                "Derived checks: %s vs %s has only %d usable pairs — skipping",
                label, metric, n,
            )
            continue

        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]

        checked += 1
        with warnings.catch_warnings():
            # A constant input (e.g. every PAC ratio identical) is handled
            # explicitly below via the NaN result — no need for the warning.
            warnings.simplefilter("ignore", scipy_stats.ConstantInputWarning)
            res = scipy_stats.spearmanr(xs, ys)
        rho = float(res.statistic)
        if math.isnan(rho):
            failures.append({
                "senator": f"ALL ({n} {entity_label})",
                "dimension": label,
                "score": 0,
                "expected": [f"rank variation in {metric} and {label}", None],
                "rationale": (
                    f"{label} vs {describes}: no rank variation (constant "
                    "input) — scores or raw data have degenerated"
                ),
            })
            logger.warning(
                "DERIVED CHECK FAIL [%s]: constant input for %s correlation",
                label, metric,
            )
            continue

        # One-sided p in the expected direction.
        p_one = res.pvalue / 2 if rho * direction > 0 else 1 - res.pvalue / 2
        if rho * direction <= 0 or p_one > ALPHA:
            failures.append({
                "senator": f"ALL ({n} {entity_label})",
                "dimension": label,
                "score": round(rho, 3),
                "expected": [
                    f"spearman rho {'>' if direction > 0 else '<'} 0",
                    f"one-sided p < {ALPHA}",
                ],
                "rationale": (
                    f"{label} no longer tracks {describes}: "
                    f"rho={rho:.2f}, one-sided p={p_one:.3g}"
                ),
            })
            logger.warning(
                "DERIVED CHECK FAIL [%s]: rho=%.2f (expected sign %+d), "
                "one-sided p=%.3g vs %s — score decoupled from %s",
                label, rho, direction, p_one, describes, metric,
            )

        if n < MIN_EXTREMES_POPULATION:
            continue

        # Extreme deciles by the raw metric, both tails. "most" = the decile
        # the metric says should be scored most independent.
        k = max(int(n * EXTREME_FRACTION), MIN_POPULATION // 2)
        ordered = sorted(pairs, key=lambda p: p[0])
        most, most_rest = (
            (ordered[-k:], ordered[:-k]) if direction > 0
            else (ordered[:k], ordered[k:])
        )
        least, least_rest = (
            (ordered[:k], ordered[k:]) if direction > 0
            else (ordered[-k:], ordered[:-k])
        )
        for group, rest, alternative, side in (
            (most, most_rest, "greater", "most-independent"),
            (least, least_rest, "less", "least-independent"),
        ):
            checked += 1
            group_scores = [y for _, y, _ in group]
            rest_scores = [y for _, y, _ in rest]
            mw = scipy_stats.mannwhitneyu(
                group_scores, rest_scores, alternative=alternative,
            )
            if math.isnan(mw.pvalue) or mw.pvalue > ALPHA:
                names = ", ".join(nm for _, _, nm in group)
                failures.append({
                    "senator": f"{side} decile by {metric} ({k} of {n} {entity_label})",
                    "dimension": label,
                    "score": round(float(statistics.median(group_scores)), 1),
                    "expected": [
                        f"stochastically {alternative} than the rest",
                        f"one-sided p < {ALPHA}",
                    ],
                    "rationale": (
                        f"the {side} decile by {describes} does not score "
                        f"{'above' if alternative == 'greater' else 'below'} "
                        f"the rest of the chamber on {label} "
                        f"(Mann-Whitney p={float(mw.pvalue):.3g}; members: {names})"
                    ),
                })
                logger.warning(
                    "DERIVED CHECK FAIL [%s]: %s decile by %s not %s rest "
                    "(p=%.3g)",
                    label, side, metric, alternative, float(mw.pvalue),
                )

    return {"checked": checked, "failures": failures}


def _vote_query_for(model):
    """Return (vote_model, member-id column) for a chamber's member model."""
    from app.models import KeyVote, RepKeyVote

    if model.__name__ == "Senator":
        return KeyVote, KeyVote.senator_id
    return RepKeyVote, RepKeyVote.representative_id


def _member_records(db, model) -> list[dict]:
    """Build the plain member records ``evaluate_derived_checks`` consumes
    from the chamber's current members and their labeled votes."""
    vote_model, fk_col = _vote_query_for(model)
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # id -> [breaks, labeled]
    for member_id, with_party in (
        db.query(fk_col, vote_model.voted_with_party)
        .filter(vote_model.voted_with_party.isnot(None))
        .all()
    ):
        counts[member_id][0] += 0 if with_party else 1
        counts[member_id][1] += 1

    records = []
    for m in db.query(model).filter(model.is_current.is_(True)).all():
        raised = m.total_raised or 0
        breaks, labeled = counts[m.id]
        records.append({
            "id": m.id,
            "name": m.name,
            "scores": {dim: getattr(m, dim, None) for dim in _DIM_LABEL},
            "metrics": {
                "pac_ratio": (m.total_from_pacs or 0) / raised if raised > 0 else None,
                "small_donor_pct": m.small_donor_percentage if raised > 0 else None,
                "party_break_rate": (
                    breaks / labeled if labeled >= MIN_LABELED_VOTES else None
                ),
            },
            "raw": {
                "total_raised": raised,
                "total_from_pacs": m.total_from_pacs or 0,
                "labeled_votes": labeled,
            },
        })
    return records


def check_ground_truth(db, model=None) -> dict:
    """Check the chamber's scores for consistency with its own raw data.

    Args:
        db: SQLAlchemy session.
        model: Senator (default) or Representative — the derived checks
            are chamber-agnostic, unlike the named reference table they
            replaced (which is why the House previously had no gate).

    Returns:
        {"checked": int, "failures": [ {senator, dimension, score,
         expected, rationale}, ... ]}
    """
    if model is None:
        from app.models import Senator
        model = Senator
    entity_label = "senators" if model.__name__ == "Senator" else "representatives"

    report = evaluate_derived_checks(_member_records(db, model), entity_label)

    if not report["failures"]:
        logger.info(
            "Derived consistency gate: %d/%d checks passed (%s)",
            report["checked"], report["checked"], entity_label,
        )
    else:
        logger.warning(
            "Derived consistency gate: %d/%d checks FAILED (%s) — "
            "investigate before trusting this run's scores",
            len(report["failures"]), report["checked"], entity_label,
        )
    return report


# ScoreSnapshot stores dimensions positionally — same mapping as
# score_calibration.DIMENSIONS (score_2 is retired Promise Persistence).
_SNAPSHOT_COLUMN = {
    "score_funding_independence": "score_1",
    "score_independent_voting": "score_3",
    "score_funding_diversity": "score_4",
    "score_legislative_effectiveness": "score_5",
}


def check_score_distribution(db, model=None) -> list[dict]:
    """Flag any scored dimension whose population has collapsed to a point.

    ``model`` defaults to Senator; pass Representative to run the same
    check for the House (both share the same score_* column names).

    Two derived tests per dimension, replacing the hand-calibrated stdev
    floors a previous version kept (8.0/6.5/5.5 — see git history for the
    audits that produced them):

    - Point-mass: a strict majority of the population sharing one
      (integer-rounded) value is a collapse by definition, with no
      threshold to tune. Promise Persistence's historical collapse (76%
      of senators at the neutral prior) trips this immediately.
    - Self-history: today's stdev is compared against this algorithm
      version's own per-date snapshot stdevs; a modified z-score below
      -3.5 (Iglewicz & Hoaglin) flags a sudden within-version collapse.
      Cross-version shifts are deliberate algorithm changes and are
      annotated on the trend chart instead of alarmed here; gradual
      drift is score_calibration.py's job.

    Returns failures in the same shape as check_ground_truth's, so
    callers can merge the two lists.
    """
    from app.models import ScoreSnapshot
    from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION
    from app.time_utils import utcnow

    if model is None:
        from app.models import Senator
        model = Senator

    failures: list[dict] = []
    rows = db.query(model).all()
    is_senate = model.__name__ == "Senator"
    entity_type = "senator" if is_senate else "representative"
    entity_label = "senators" if is_senate else "representatives"
    today = utcnow().strftime("%Y-%m-%d")

    # Per-date historical values for this chamber under the CURRENT
    # algorithm version, excluding today's just-written snapshot.
    snapshot_cols = [getattr(ScoreSnapshot, col) for col in _SNAPSHOT_COLUMN.values()]
    history_rows = (
        db.query(ScoreSnapshot.date, *snapshot_cols)
        .filter(
            ScoreSnapshot.entity_type == entity_type,
            ScoreSnapshot.algorithm_version == ALGORITHM_VERSION,
            ScoreSnapshot.date < today,
        )
        .all()
    )
    by_date: dict[str, list[tuple]] = defaultdict(list)
    for row in history_rows:
        by_date[row[0]].append(row[1:])

    for idx, (dim, _col) in enumerate(_SNAPSHOT_COLUMN.items()):
        label = _DIM_LABEL.get(dim, dim)
        values = [v for v in (getattr(s, dim, None) for s in rows) if v is not None]
        if len(values) < MIN_POPULATION:
            continue
        stdev = statistics.pstdev(values)

        mode_count = max(Counter(round(v) for v in values).values())
        if mode_count * 2 > len(values):
            failures.append({
                "senator": f"ALL ({len(values)} {entity_label})",
                "dimension": label,
                "score": round(mode_count / len(values), 2),
                "expected": ["no single value held by a majority", None],
                "rationale": (
                    f"{mode_count} of {len(values)} {entity_label} share one "
                    f"value — scores have collapsed toward a point mass"
                ),
            })
            logger.warning(
                "DERIVED CHECK FAIL: %s point-mass collapse — %d/%d share "
                "one value (stdev=%.2f)",
                label, mode_count, len(values), stdev,
            )
            continue

        history_stdevs = [
            statistics.pstdev(vals)
            for vals in (
                [t[idx] for t in tuples if t[idx] is not None]
                for tuples in by_date.values()
            )
            if len(vals) >= MIN_POPULATION
        ]
        if len(history_stdevs) < MIN_HISTORY_DATES:
            continue
        med = statistics.median(history_stdevs)
        mad = statistics.median(abs(h - med) for h in history_stdevs)
        if mad == 0:
            # Perfectly flat history gives the modified z-score no scale;
            # the point-mass check above still guards catastrophic collapse.
            continue
        z = MAD_Z_SCALE * (stdev - med) / mad
        if z < -MODIFIED_Z_CUTOFF:
            derived_floor = med - MODIFIED_Z_CUTOFF * mad / MAD_Z_SCALE
            failures.append({
                "senator": f"ALL ({len(values)} {entity_label})",
                "dimension": label,
                "score": round(stdev, 2),
                "expected": [round(derived_floor, 2), None],
                "rationale": (
                    f"population stdev {stdev:.2f} is an extreme low outlier "
                    f"vs this algorithm version's own history (median "
                    f"{med:.2f} over {len(history_stdevs)} snapshot dates, "
                    f"modified z={z:.1f}) — scores are losing discriminative "
                    "power"
                ),
            })
            logger.warning(
                "DERIVED CHECK FAIL: %s population stdev=%.2f vs history "
                "median %.2f (modified z=%.1f, n=%d dates) — dimension may "
                "have collapsed",
                label, stdev, med, z, len(history_stdevs),
            )

    return failures
