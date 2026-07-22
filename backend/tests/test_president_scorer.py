"""Tests for president score calculation.

No dimension has a seed fallback anymore (2026-07) — a component/
dimension with no live data is simply excluded and the weight of
whatever IS live renormalizes to 100% of what was measured. Zero live
components means score=None, never a fabricated or neutral number.
"""

from app.pipeline.analyze.president_scorer import (
    calc_agency_alignment,
    calc_competence,
    calc_effectiveness,
    calc_historical_legacy,
    calc_public_mandate,
    recalculate_president_scores,
)


class TestCalcCompetence:
    def test_no_live_data_returns_none(self):
        score = calc_competence(
            eo_count=None, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, term_start_year=2000,
        )
        assert score is None

    def test_eo_rate_only_component_moves_the_score(self):
        low_activity = calc_competence(
            eo_count=2, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, term_start_year=2000,
        )
        moderate_activity = calc_competence(
            eo_count=160, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, term_start_year=2000,
        )
        assert low_activity is not None
        assert moderate_activity is not None
        assert moderate_activity != low_activity

    def test_full_live_data_still_supported_for_a_future_source(self):
        """Court-success/cabinet-turnover params remain accepted (just
        never populated today by any fetcher) so a real data source can
        be wired in later without a signature change."""
        score = calc_competence(
            eo_count=200, eo_court_success_pct=90.0,
            cabinet_turnover_pct=5.0, term_years=4.0, term_start_year=2000,
        )
        assert score is not None and score > 50

    def test_zero_eo_count_excludes_the_component(self):
        # eo_count=0 is falsy, same branch as None — with no other live
        # component either, the whole dimension is inapplicable.
        score = calc_competence(
            eo_count=0, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, term_start_year=2000,
        )
        assert score is None

    def test_eo_rate_scored_against_its_own_era_not_a_fixed_scale(self):
        """Lincoln's real EO rate (48 over ~4 years = 12/year) sits almost
        exactly at his own pre-1901 era's real population mean (12.02/year)
        — scored near-neutral. The old fixed "30-60/year is optimal" scale
        (calibrated to modern EO volume) scored this same rate as a weak
        ~37 (2026-07 fix: see _eo_activity_rate_component)."""
        pre_1901_near_average = calc_competence(
            eo_count=48, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, term_start_year=1861,
        )
        assert pre_1901_near_average is not None
        assert 45 <= pre_1901_near_average <= 55

    def test_same_absolute_rate_scores_differently_across_eras(self):
        # ~12/year is near-average pre-1901 but far below average post-1901.
        rate_pre = calc_competence(
            eo_count=48, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, term_start_year=1861,
        )
        rate_post = calc_competence(
            eo_count=48, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, term_start_year=2001,
        )
        assert rate_pre is not None and rate_post is not None
        assert rate_pre > rate_post


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
    def test_missing_eo_court_and_turnover_keys_do_not_crash(self):
        """live_data intentionally omits eo_court_success_pct and
        cabinet_turnover_pct in production (president_pipeline.py) —
        .get() on the missing keys must resolve to None without error."""
        result = recalculate_president_scores(
            president_id="test-1",
            live_data={"eo_count": 120},
            term_years=4.0,
            term_start_year=2000,
        )
        assert set(result) == {
            "score_public_mandate", "score_competence",
            "score_effectiveness", "score_agency_alignment",
            "score_historical_legacy",
        }
        assert result["score_competence"] is not None
        assert result["score_public_mandate"] is None

    def test_empty_live_data_returns_all_none(self):
        result = recalculate_president_scores(
            president_id="test-1", live_data={}, term_years=4.0, term_start_year=2000,
        )
        assert all(v is None for v in result.values())
