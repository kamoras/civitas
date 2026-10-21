"""The Action Center's precision rule, for a calibration script to share.

A gate whose pass is shown or posted as fact is set where its precision's
exact (Clopper-Pearson) lower 95% bound is highest, and used only when that
bound is at least PRECISION_LOWER_BOUND (AGENTS.md: committee routing, the
related-vote links). Taking the lowest threshold that clears the bar put it
at the curve's edge, where held-out data fell under it
(scripts/calibrate_roll_call_links.py, which this generalizes).
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


def choose(scores: np.ndarray, correct: np.ndarray, grid: list[float]) -> tuple[float | None, float]:
    """(threshold, the threshold with the best bound): the grid point whose
    precision's exact lower bound is highest (the lowest on a tie), or None
    for the threshold when even that bound misses PRECISION_LOWER_BOUND."""
    curve = [(t, summary(scores >= t, correct)["precision_exact_lower"]) for t in grid]
    best_t, best_b = max(curve, key=lambda c: (c[1], -c[0]))
    return (best_t if best_b >= PRECISION_LOWER_BOUND else None), best_t


def held_out(scores: np.ndarray, correct: np.ndarray, days: np.ndarray, grid: list[float], folds: int) -> dict:
    """The choice made `folds` times, each on all but one block of
    consecutive days, scoring the block left out; pooled, with each block."""
    order = sorted(set(days.tolist()))
    edges = [order[int(len(order) * f / folds)] for f in range(1, folds)]
    fold = np.searchsorted(np.array(edges), days, side="right")
    passed = np.zeros(len(scores), bool)
    blocks = []
    for f in range(folds):
        test = fold == f
        if not test.any() or test.all():
            continue
        t_meet, t_best = choose(scores[~test], correct[~test], grid)
        t = t_meet if t_meet is not None else t_best
        passed[test] = scores[test] >= t if t_meet is not None else False
        blocks.append({"from": str(min(days[test])), "to": str(max(days[test])), "threshold": t,
                       "threshold_met_bound": t_meet is not None, **summary(passed[test], correct[test])})
    return {"pooled": summary(passed, correct), "blocks": blocks}
