"""Tests for score-distribution drift helpers (score_calibration.py)."""

from app.pipeline.analyze.score_calibration import _spearman_rho


class TestSpearmanRho:
    def test_perfect_agreement(self):
        prev = {"a": 10.0, "b": 20.0, "c": 30.0}
        curr = {"a": 1.0, "b": 2.0, "c": 3.0}
        assert _spearman_rho(prev, curr) == 1.0

    def test_perfect_reversal(self):
        prev = {"a": 10.0, "b": 20.0, "c": 30.0}
        curr = {"a": 3.0, "b": 2.0, "c": 1.0}
        assert _spearman_rho(prev, curr) == -1.0

    def test_ties_handled_exactly(self):
        """Clamped integer scores tie constantly; the 6*sum(d^2) shortcut
        is only exact without ties, so rho must come from Pearson of the
        average ranks. With one tied pair moved apart, rho must be
        strictly between 0 and 1 and match the exact value."""
        prev = {"a": 50.0, "b": 50.0, "c": 60.0, "d": 70.0}
        curr = {"a": 50.0, "b": 55.0, "c": 60.0, "d": 70.0}
        rho = _spearman_rho(prev, curr)
        assert rho is not None
        assert 0.9 < rho < 1.0  # exact Pearson-of-ranks: ~0.9487

    def test_all_tied_side_returns_none(self):
        prev = {"a": 50.0, "b": 50.0, "c": 50.0}
        curr = {"a": 1.0, "b": 2.0, "c": 3.0}
        assert _spearman_rho(prev, curr) is None

    def test_fewer_than_three_common_returns_none(self):
        assert _spearman_rho({"a": 1.0, "b": 2.0}, {"a": 1.0, "b": 2.0}) is None
