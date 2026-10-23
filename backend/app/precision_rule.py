"""The Action Center's precision rule, for a calibration script to share.

A gate whose pass is shown or posted as fact is used only when its
precision's exact (Clopper-Pearson) lower 95% bound is at least
PRECISION_LOWER_BOUND (AGENTS.md: committee routing, the related-vote links).
Which threshold among those is a named variant, and each calibration file
written through this module records the one it used (`precision_rule`):

- BEST_BOUND (the default): where the bound is highest. Taking the lowest
  threshold that clears the bar put it at the curve's edge, where held-out
  data fell under it (scripts/calibrate_roll_call_links.py, which this
  generalizes). For a gate whose pass is shown as fact.
- LOWEST_PASSING: the lowest threshold whose bound clears the bar, i.e. the
  most passes the bar allows. For a gate whose volume is the point and
  whose pass is someone else's words, not a claim of ours: the outlet
  reposts (bluesky_engagement), where the best bound cut reposts from about
  37 a week to 9 (the owner's decision, 2026-10-21). It sits at the edge
  BEST_BOUND avoids, so its held-out figures matter more; a daily refit on
  fresh labels is what keeps it honest.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import beta

# The Action Center's bar for anything shown as fact.
PRECISION_LOWER_BOUND = 0.80


def exact_lower(correct: int, named: int) -> float:
    """The exact lower 95% bound of correct / named (0 with none correct)."""
    return float(beta.ppf(0.025, correct, named - correct + 1)) if correct else 0.0


def summary(passed: np.ndarray, correct: np.ndarray) -> dict:
    """Counts and precision of the items a threshold passes."""
    k, n = int((passed & correct).sum()), int(passed.sum())
    return {"passed": n, "correct": k, "precision": round(k / n, 3) if n else None,
            "precision_exact_lower": round(exact_lower(k, n), 3)}


BEST_BOUND = "best_bound"
LOWEST_PASSING = "lowest_passing"
VARIANTS = (BEST_BOUND, LOWEST_PASSING)


def choose(scores: np.ndarray, correct: np.ndarray, grid: list[float],
           variant: str = BEST_BOUND) -> tuple[float | None, float]:
    """(threshold, the threshold with the best bound). The threshold is the
    grid point the variant picks among those whose precision's exact lower
    bound reaches PRECISION_LOWER_BOUND (BEST_BOUND: the highest bound, the
    lowest point on a tie; LOWEST_PASSING: the lowest point), or None when
    none does."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown precision-rule variant {variant!r}")
    curve = [(t, summary(scores >= t, correct)["precision_exact_lower"]) for t in grid]
    best_t, best_b = max(curve, key=lambda c: (c[1], -c[0]))
    if best_b < PRECISION_LOWER_BOUND:
        return None, best_t
    if variant == LOWEST_PASSING:
        return min(t for t, b in curve if b >= PRECISION_LOWER_BOUND), best_t
    return best_t, best_t


def fold_of(days: np.ndarray, folds: int) -> np.ndarray:
    """Each item's block of consecutive days, 0 to folds - 1."""
    order = sorted(set(days.tolist()))
    edges = [order[int(len(order) * f / folds)] for f in range(1, folds)]
    return np.searchsorted(np.array(edges), days, side="right")


def held_out_passed(scores: np.ndarray, correct: np.ndarray, days: np.ndarray, grid: list[float], folds: int,
                    variant: str = BEST_BOUND) -> np.ndarray:
    """Which items pass when each block of consecutive days is scored by the
    choice made on the other blocks (none in a block whose choice misses the
    bar): held_out's pooled passes."""
    fold = fold_of(days, folds)
    passed = np.zeros(len(scores), bool)
    for f in range(folds):
        test = fold == f
        if not test.any() or test.all():
            continue
        t_meet, _ = choose(scores[~test], correct[~test], grid, variant)
        if t_meet is not None:
            passed[test] = scores[test] >= t_meet
    return passed


def held_out(scores: np.ndarray, correct: np.ndarray, days: np.ndarray, grid: list[float], folds: int,
             variant: str = BEST_BOUND) -> dict:
    """The choice made `folds` times, each on all but one block of
    consecutive days, scoring the block left out; pooled, with each block."""
    fold = fold_of(days, folds)
    passed = np.zeros(len(scores), bool)
    blocks = []
    for f in range(folds):
        test = fold == f
        if not test.any() or test.all():
            continue
        t_meet, t_best = choose(scores[~test], correct[~test], grid, variant)
        t = t_meet if t_meet is not None else t_best
        passed[test] = scores[test] >= t if t_meet is not None else False
        blocks.append({"from": str(min(days[test])), "to": str(max(days[test])), "threshold": t,
                       "threshold_met_bound": t_meet is not None, **summary(passed[test], correct[test])})
    return {"pooled": summary(passed, correct), "blocks": blocks}


# A refit from data-supplied labels replaces the fit in use only when, on
# the same held-out labelled items, its precision's exact lower bound is no
# lower and its recall is no more than one standard error of the paired
# difference lower (the owner's rule for every calibrated gate that refits
# itself, 2026-10-23). The comparison is recorded either way, so a fit kept
# says why.
def compare(correct: np.ndarray, candidate_passed: np.ndarray, in_use_passed: np.ndarray) -> dict:
    """Both fits on the same held-out labelled items: each one's passes,
    precision and exact lower bound and recall, and the paired recall
    difference (candidate minus in use) with its standard error."""
    correct = np.asarray(correct, bool)

    def side(passed: np.ndarray) -> dict:
        passed = np.asarray(passed, bool)
        found = int((passed & correct).sum())
        return {**summary(passed, correct), "recall": round(found / int(correct.sum()), 3) if correct.any() else None}

    diff = (np.asarray(candidate_passed, bool)[correct].astype(float)
            - np.asarray(in_use_passed, bool)[correct].astype(float))
    se = float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 1 else 0.0
    return {"candidate": side(candidate_passed), "in_use": side(in_use_passed),
            "recall_delta": round(float(diff.mean()), 4) if len(diff) else 0.0, "recall_se": round(se, 4)}


def accept(comparison: dict) -> str | None:
    """None to adopt the candidate (rule above), else why the fit in use
    stays."""
    cand, cur = comparison["candidate"], comparison["in_use"]
    if not cand["passed"] and not cur["passed"]:
        return "no held-out item reaches either fit: nothing to compare them on"
    if cand["precision_exact_lower"] < cur["precision_exact_lower"]:
        return (f"held-out precision's lower bound would fall: {cand['precision_exact_lower']} against "
                f"{cur['precision_exact_lower']} in use")
    if comparison["recall_delta"] < -comparison["recall_se"]:
        return (f"held-out recall would drop by more than one standard error: {comparison['recall_delta']:+.4f}, "
                f"SE {comparison['recall_se']:.4f}")
    return None
