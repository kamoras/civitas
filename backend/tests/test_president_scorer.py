"""Tests for president score calculation.

No dimension has a seed fallback anymore (2026-07) — a component/
dimension with no live data is simply excluded and the weight of
whatever IS live renormalizes to 100% of what was measured. Zero live
components means score=None, never a fabricated or neutral number.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import app.config_definitions as config_definitions
from app.pipeline.analyze.president_scorer import (
    _public_mandate_core,
    approval_window,
    calc_agency_alignment,
    calc_effectiveness,
    calc_historical_legacy,
    calc_public_mandate,
    compute_president_overall_score,
    compute_president_reference,
    fit_trend_on_start,
    recalculate_president_scores,
    sitting_window_reference,
)


def _entity(mandate=None, effectiveness=None, agency=None, legacy=None):
    return SimpleNamespace(
        score_public_mandate=mandate,
        score_effectiveness=effectiveness,
        score_agency_alignment=agency,
        score_historical_legacy=legacy,
    )


class TestCalcEffectiveness:
    def test_no_data_returns_none(self):
        score = calc_effectiveness(
            jobs_created_millions=None, gdp_growth_avg=None, term_years=4.0,
        )
        assert score is None

    def test_positive_gdp_and_jobs_score_above_neutral(self):
        score = calc_effectiveness(
            jobs_created_millions=10.0, gdp_growth_avg=4.5, term_years=4.0,
        )
        assert score is not None and score > 50

    def test_gdp_only_still_scores_renormalized_to_full_weight(self):
        gdp_only = calc_effectiveness(
            jobs_created_millions=None, gdp_growth_avg=4.5, term_years=4.0,
        )
        assert gdp_only is not None


class TestCalcAgencyAlignment:
    def test_no_data_returns_none(self):
        score = calc_agency_alignment(rulemaking_finalized_pct=None)
        assert score is None


class TestCalcHistoricalLegacy:
    def test_no_data_returns_none(self):
        # Any currently-serving or just-departed president — C-SPAN's
        # 2025 cycle was postponed entirely.
        assert calc_historical_legacy(historical_legacy_score=None) is None

    def test_lincoln_real_score_lands_well_above_neutral(self):
        # Real 2021 C-SPAN score (897), the highest of any president —
        # should score near the top of the 0-100 scale.
        score = calc_historical_legacy(historical_legacy_score=897)
        assert score is not None and score > 80

    def test_buchanan_real_score_lands_well_below_neutral(self):
        # Real 2021 C-SPAN score (227), near the bottom.
        score = calc_historical_legacy(historical_legacy_score=227)
        assert score is not None and score < 30

    def test_population_mean_score_lands_near_neutral(self):
        score = calc_historical_legacy(historical_legacy_score=549)
        assert score is not None and 45 <= score <= 55


class TestCalcPublicMandate:
    def test_no_data_returns_none(self):
        # The five presidents who never won a presidential election.
        score = calc_public_mandate(avg_approval=None, approval_trend=None, election_margin=None)
        assert score is None

    def test_approval_path_used_when_present(self):
        score = calc_public_mandate(avg_approval=60.0, approval_trend=5.0, election_margin=None)
        assert score is not None and score > 50

    def test_election_margin_is_the_pre_polling_era_fallback(self):
        score = calc_public_mandate(avg_approval=None, approval_trend=None, election_margin=20.0)
        assert score is not None and score > 50

    def test_approval_takes_priority_over_election_margin_when_both_present(self):
        approval_only = calc_public_mandate(avg_approval=60.0, approval_trend=None, election_margin=None)
        both = calc_public_mandate(avg_approval=60.0, approval_trend=None, election_margin=-99.0)
        assert approval_only == both


class TestRecalculatePresidentScores:
    def test_missing_keys_do_not_crash(self):
        """.get() on missing live_data keys must resolve to None without
        error — production (president_pipeline.py) only ever populates a
        subset."""
        result = recalculate_president_scores(
            president_id="test-1",
            live_data={"gdp_growth_avg": 4.5},
            term_years=4.0,
        )
        assert set(result) == {
            "score_public_mandate", "score_effectiveness",
            "score_agency_alignment", "score_historical_legacy",
        }
        assert result["score_effectiveness"] is not None
        assert result["score_public_mandate"] is None

    def test_empty_live_data_returns_all_none(self):
        result = recalculate_president_scores(
            president_id="test-1", live_data={}, term_years=4.0,
        )
        assert all(v is None for v in result.values())


class TestComputePresidentOverallScoreTiering:
    """2026-07: two-tier renormalization — Legacy is held at its
    configured weight whenever >= 2 mechanical dimensions are present
    (fixing the old flat scheme, which let Legacy's effective weight
    balloon to ~44.7%/~61.8% for presidents missing mechanical data —
    see compute_president_overall_score's docstring). Below that
    mechanical-dimension floor, falls back to flat renormalization so a
    single mechanical number (e.g. Fillmore's GDP-boom-driven
    Effectiveness=100) can't swamp a real Historical Legacy score."""

    WEIGHTS = {
        "publicMandate": 0.2167, "effectiveness": 0.2167,
        "agencyAlignment": 0.2167, "historicalLegacy": 0.35,
    }

    def _set_weights(self, monkeypatch):
        monkeypatch.setattr(config_definitions, "PRESIDENT_SCORE_WEIGHTS", self.WEIGHTS)

    def test_legacy_held_at_configured_weight_with_two_mechanical_present(self, monkeypatch):
        self._set_weights(monkeypatch)
        e = _entity(mandate=80.0, effectiveness=60.0, legacy=20.0)
        overall = compute_president_overall_score(e)
        expected = 0.35 * 20.0 + 0.65 * ((80.0 + 60.0) / 2)
        assert overall == round(expected, 2)

    def test_legacy_held_at_configured_weight_with_three_mechanical_present(self, monkeypatch):
        self._set_weights(monkeypatch)
        e = _entity(mandate=80.0, effectiveness=60.0, agency=40.0, legacy=20.0)
        overall = compute_president_overall_score(e)
        expected = 0.35 * 20.0 + 0.65 * ((80.0 + 60.0 + 40.0) / 3)
        assert overall == round(expected, 2)

    def test_single_mechanical_dimension_falls_back_to_flat_renormalization(self, monkeypatch):
        """The Fillmore case: only Effectiveness present alongside Legacy.
        A flat 35%/65% split would let a single GDP number override a
        near-bottom historian rating entirely — verify it doesn't."""
        self._set_weights(monkeypatch)
        e = _entity(effectiveness=100.0, legacy=19.0)
        overall = compute_president_overall_score(e)

        total = 0.2167 + 0.35
        flat_expected = round((0.2167 * 100.0 + 0.35 * 19.0) / total, 2)
        fixed_tier_would_be = round(0.35 * 19.0 + 0.65 * 100.0, 2)

        assert overall == flat_expected
        assert overall != fixed_tier_would_be
        assert overall < 70.0  # nowhere near effectiveness's raw 100

    def test_legacy_absent_renormalizes_mechanical_only_unaffected(self, monkeypatch):
        self._set_weights(monkeypatch)
        e = _entity(mandate=60.0, effectiveness=40.0)
        assert compute_president_overall_score(e) == 50.0

    def test_nothing_present_returns_zero(self):
        assert compute_president_overall_score(_entity()) == 0.0


# ── Approval trend against the starting level (v6) ──────────────────

# The 14 completed polling-era presidencies' (start, trend), 2026-10-01:
# the higher the start, the steeper the fall (r = -0.67).
_HISTORY = [
    (55.4, -26.8), (47.9, -10.8), (48.7, -4.3), (57.8, -22.2), (49.3, -2.7), (65.0, -34.0),
    (70.1, -37.5), (52.2, 0.0), (49.5, 12.0), (71.7, -31.4), (74.9, -26.9), (66.9, -4.1),
    (77.7, -14.0), (38.5, 3.8),
]


def test_the_trend_fit_finds_that_high_starters_fall_further():
    fit = fit_trend_on_start(_HISTORY)
    assert fit["n"] == 14 and fit["slope"] < -0.5
    assert fit_trend_on_start(_HISTORY[:9]) is None


def test_a_small_decline_from_near_the_floor_is_not_rewarded():
    """A small decline from a low start is judged against what presidents
    starting that low went on to do, which is a gain, not against the
    average fall of all presidents."""
    reference = {
        "avg_approval": {"mean": 51.9, "stdev": 9.0, "n": 14},
        "approval_trend": {"mean": -14.2, "stdev": 15.6, "n": 14},
        "approval_trend_fit": fit_trend_on_start(_HISTORY),
    }
    out = _public_mandate_core(37.3, -5.6, None, reference, approval_start=41.0)
    trend = next(c for c in out["components"] if c["label"] == "Approval trend")
    assert trend["score"] < 50
    assert out["facts"]["trendExpected"] > 0
    # The same change from a high start beats expectation.
    high = _public_mandate_core(60.0, -5.6, None, reference, approval_start=70.0)
    assert next(c for c in high["components"] if c["label"] == "Approval trend")["score"] > 70


def _series(start_day: date, values: list[float], step: int = 30) -> list[tuple[date, float]]:
    return [(start_day + timedelta(days=i * step), v) for i, v in enumerate(values)]


def test_approval_window_reads_only_the_first_days():
    s = _series(date(2001, 1, 20), [60, 60, 60, 60, 40, 40, 40, 40])
    assert approval_window(s)["trend"] == -20
    early = approval_window(s, days=90)  # the first four polls
    assert early["avg"] == 60 and early["trend"] == 0


def test_the_sitting_president_is_compared_over_the_same_elapsed_time():
    first = date(2001, 1, 20)
    # Twelve predecessors who started high and fell after day 90, and one
    # whose term ended before the window closed.
    completed = [_series(first, [70 + i, 70 + i, 70 + i, 70 + i, 50, 50, 50, 50, 50, 50]) for i in range(12)]
    completed.append(_series(first, [55, 55]))
    current = _series(date(2025, 1, 20), [45, 45, 45, 45])  # 90 days in
    window = sitting_window_reference(current, completed)
    assert window["days"] == 90
    # Over their first 90 days predecessors averaged 70-81, not their
    # full-term ~58, and the short presidency is left out.
    assert window["avg_approval"]["n"] == 12
    assert window["avg_approval"]["mean"] > 70


def test_a_sitting_presidents_partial_term_stays_out_of_the_full_term_population():
    pres = [
        {"id": f"p{i}", "avg_approval": 50.0 + i, "approval_trend": -10.0 - i, "approval_start": 60.0 + i}
        for i in range(12)
    ]
    pres.append({"id": "now", "avg_approval": 10.0, "approval_trend": 40.0, "approval_start": 5.0, "is_current": True})
    ref = compute_president_reference(pres)
    assert ref["avg_approval"]["n"] == 12
    assert ref["approval_trend_fit"]["n"] == 12


def test_the_sitting_president_uses_the_elapsed_window_reference():
    window = {
        "days": 598,
        "avg_approval": {"mean": 57.5, "stdev": 8.0, "n": 14},
        "approval_trend_fit": fit_trend_on_start(_HISTORY),
    }
    reference = {"avg_approval": {"mean": 40.0, "stdev": 8.0, "n": 14}, "sitting_window": window}
    sitting = _public_mandate_core(45.0, -5.0, None, reference, approval_start=45.0, is_current=True)
    assert sitting["facts"]["comparedOverDays"] == 598
    assert sitting["facts"]["approvalMean"] == 57.5
    past = _public_mandate_core(45.0, -5.0, None, reference, approval_start=45.0)
    assert past["facts"]["approvalMean"] == 40.0
