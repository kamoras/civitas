"""Tests for president score calculation.

calc_competence is the one that matters most here: eo_court_success_pct
and cabinet_turnover_pct must never be treated as live data, since no
fetch source populates them (2026-07 audit) — passing the stored seed
value back in as if freshly computed made Competence look far more
data-derived than it actually is.
"""

from app.pipeline.analyze.president_scorer import (
    calc_agency_alignment,
    calc_competence,
    calc_effectiveness,
    recalculate_president_scores,
)


class TestCalcCompetence:
    def test_no_live_data_returns_seed(self):
        score = calc_competence(
            eo_count=None, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, seed_score=62,
        )
        assert score == 62

    def test_court_success_and_turnover_never_passed_by_the_real_pipeline(self):
        """The real caller (president_pipeline.py) never has fetched
        values for these two — this locks in that calc_competence still
        falls back to seed correctly when they're absent, which is the
        only way they're ever actually invoked in production."""
        with_only_eo_rate = calc_competence(
            eo_count=200, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, seed_score=50,
        )
        # EO rate is 30% of the formula; the other 70% blends with seed.
        # A seed of 50 with any EO-rate component should land close to
        # 50, not swing to an extreme the way full live data could.
        assert 30 <= with_only_eo_rate <= 70

    def test_eo_rate_only_component_still_moves_the_score(self):
        """Confirms the one genuinely-live component (EO activity rate)
        still has an effect, so this isn't secretly 100% seed either."""
        low_activity = calc_competence(
            eo_count=2, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, seed_score=50,
        )
        moderate_activity = calc_competence(
            eo_count=160, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, seed_score=50,
        )
        assert moderate_activity != low_activity

    def test_full_live_data_still_supported_for_a_future_source(self):
        """The parameters remain accepted (just never populated today) so
        a real data source can be wired in later without a signature
        change — this pins that the blending math itself still works."""
        score = calc_competence(
            eo_count=200, eo_court_success_pct=90.0,
            cabinet_turnover_pct=5.0, term_years=4.0, seed_score=50,
        )
        assert score > 50  # strong court success + low turnover should pull up

    def test_zero_eo_count_falls_back_to_seed_component(self):
        score = calc_competence(
            eo_count=0, eo_court_success_pct=None,
            cabinet_turnover_pct=None, term_years=4.0, seed_score=77,
        )
        assert score == 77


class TestCalcEffectiveness:
    def test_no_data_returns_seed(self):
        score = calc_effectiveness(
            jobs_created_millions=None, gdp_growth_avg=None,
            term_years=4.0, seed_score=55,
        )
        assert score == 55

    def test_positive_gdp_and_jobs_score_above_seed_when_seed_is_low(self):
        score = calc_effectiveness(
            jobs_created_millions=10.0, gdp_growth_avg=4.5,
            term_years=4.0, seed_score=20,
        )
        assert score > 20


class TestCalcAgencyAlignment:
    def test_no_data_returns_seed(self):
        score = calc_agency_alignment(
            rulemaking_count=None, rulemaking_finalized_pct=None,
            term_years=4.0, seed_score=48,
        )
        assert score == 48


class TestRecalculatePresidentScores:
    def test_missing_eo_court_and_turnover_keys_do_not_crash(self):
        """live_data intentionally omits eo_court_success_pct and
        cabinet_turnover_pct in production (president_pipeline.py) —
        .get() on the missing keys must resolve to None without error."""
        result = recalculate_president_scores(
            president_id="test-1",
            seed_scores={
                "score_competence": 60, "score_effectiveness": 55,
                "score_agency_alignment": 50,
            },
            live_data={"eo_count": 120},
            term_years=4.0,
        )
        assert "score_competence" in result
        assert "score_effectiveness" in result
        assert "score_agency_alignment" in result
