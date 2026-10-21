"""The Action Center's precision rule as the calibration scripts share it."""

import numpy as np
import pytest

from app.precision_rule import PRECISION_LOWER_BOUND, choose, exact_lower, held_out, summary


def test_exact_lower_is_clopper_pearson():
    assert exact_lower(0, 10) == 0.0
    # 10 of 10 right: the exact lower bound is 0.025 ** (1/10).
    assert exact_lower(10, 10) == pytest.approx(0.025**0.1)
    assert exact_lower(8, 10) < 0.8 < exact_lower(80, 81)


def test_summary_counts_only_what_passes():
    passed = np.array([True, True, True, False])
    correct = np.array([True, False, True, True])
    s = summary(passed, correct)
    assert (s["passed"], s["correct"], s["precision"]) == (3, 2, 0.667)
    assert s["precision_exact_lower"] == round(exact_lower(2, 3), 3)
    assert summary(np.zeros(4, bool), correct) == {
        "passed": 0,
        "correct": 0,
        "precision": None,
        "precision_exact_lower": 0.0,
    }


def _separable():
    # 200 clean positives at 0.9, 200 negatives at 0.3, 50 coin flips at 0.6.
    scores = np.concatenate([np.full(200, 0.9), np.full(200, 0.3), np.full(50, 0.6)])
    correct = np.concatenate([np.ones(200, bool), np.zeros(200, bool), np.arange(50) % 2 == 0])
    return scores, correct


def test_choose_takes_the_best_bound_and_uses_it_only_past_the_bar():
    scores, correct = _separable()
    # 0.95 passes nothing (bound 0); 0.7 passes only the clean 200.
    assert choose(scores, correct, [0.2, 0.5, 0.7, 0.95]) == (0.7, 0.7)
    assert summary(scores >= 0.7, correct)["precision_exact_lower"] >= PRECISION_LOWER_BOUND


def test_choose_returns_none_when_no_threshold_meets_the_bar():
    scores = np.linspace(0, 1, 100)
    correct = np.arange(100) % 2 == 0  # coin flips at every score
    threshold, best = choose(scores, correct, [0.1, 0.5, 0.9])
    assert threshold is None
    assert best in (0.1, 0.5, 0.9)


def test_choose_breaks_a_tie_toward_the_lower_threshold():
    assert choose(np.full(50, 0.9), np.ones(50, bool), [0.5, 0.8]) == (0.5, 0.5)


def test_held_out_scores_each_block_with_a_threshold_fitted_without_it():
    scores, correct = _separable()
    days = np.arange(len(scores)) % 4  # four days, every day the same mix
    result = held_out(scores, correct, days, [0.5, 0.7], folds=4)
    assert len(result["blocks"]) == 4
    assert all(b["threshold"] == 0.7 and b["threshold_met_bound"] for b in result["blocks"])
    assert result["pooled"]["passed"] == 200 and result["pooled"]["precision"] == 1.0
    assert [b["from"] for b in result["blocks"]] == ["0", "1", "2", "3"]


def test_held_out_passes_nothing_where_the_fit_missed_the_bar():
    scores = np.linspace(0, 1, 120)
    correct = np.arange(120) % 2 == 0
    days = np.arange(120) // 40  # three days
    result = held_out(scores, correct, days, [0.1, 0.5], folds=3)
    assert result["pooled"]["passed"] == 0
    assert all(not b["threshold_met_bound"] and b["passed"] == 0 for b in result["blocks"])
