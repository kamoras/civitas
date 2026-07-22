"""Tests for president score calculation.

No dimension has a seed fallback anymore (2026-07) — a component/
dimension with no live data is simply excluded and the weight of
whatever IS live renormalizes to 100% of what was measured. Zero live
components means score=None, never a fabricated or neutral number.
"""

from types import SimpleNamespace

import app.config_definitions as config_definitions
from app.pipeline.analyze.president_scorer import (
    calc_agency_alignment,
    calc_effectiveness,
    calc_historical_legacy,
    calc_public_mandate,
    compute_president_overall_score,
    recalculate_president_scores,
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
        score = calc_agency_alignment(
            rulemaking_count=None, rulemaking_finalized_pct=None, term_years=4.0,
        )
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
