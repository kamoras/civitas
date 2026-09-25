"""Presidential Effectiveness / Agency Alignment against measured
populations (president v5; docs/research/president-scores.md)."""

import inspect

from app.models import President
from app.pipeline.analyze import president_scorer
from app.pipeline.analyze.president_scorer import (
    _agency_alignment_core,
    _effectiveness_core,
    compute_president_reference,
    jobs_per_attributed_year,
)


def gdp_score(growth, start_year):
    core = _effectiveness_core(None, growth, 4.0, start_year)
    return core["components"][0]["score"]


def test_gdp_is_compared_within_its_data_regime():
    # The same 5% growth is ordinary in the volatile prewar record and
    # exceptional after 1947 (conftest pins the research fallback stats).
    assert gdp_score(5.0, 1890) < gdp_score(5.0, 1990)
    assert gdp_score(3.317, 1890) == 50.0
    assert gdp_score(2.8371, 1990) == 50.0


def test_jobs_use_the_attributed_window_and_the_measured_population():
    assert jobs_per_attributed_year(7.0, 8.0) == 1.0
    core = _effectiveness_core(1.2351 * 7, None, 8.0, 2009)
    assert core["components"][0]["label"] == "Jobs created"
    assert abs(core["components"][0]["score"] - 50.0) < 0.1


def test_agency_alignment_scores_finalization_only():
    core = _agency_alignment_core(60.0)
    assert [c["label"] for c in core["components"]] == ["Finalization rate"]
    assert core["score"] == 50
    assert "rulemaking_count" not in inspect.signature(president_scorer.calc_agency_alignment).parameters


def test_reference_measures_each_new_stat_from_the_population():
    rows = [
        {"id": f"p{i}", "name": f"P{i}", "gdp_growth_avg": 2.0 + i % 3, "term_start_year": 1800 + 10 * i,
         "jobs_created_millions": 4.0 + i % 2, "term_years": 4.0,
         "rulemaking_finalized_pct": 50.0 + i if i < 6 else None}
        for i in range(24)
    ]
    ref = compute_president_reference(rows)
    assert ref["gdp_growth_prewar"]["n"] == 15  # 1800..1940
    # 1950-2030 is only nine presidencies: below the minimum, so it is left
    # out and the caller keeps the last persisted value.
    assert "gdp_growth_postwar" not in ref
    assert ref["jobs_per_year"]["n"] == 24
    assert ref["rulemaking_finalized_pct"]["n"] == 6  # measured from five


def test_the_stale_adjusted_gdp_column_and_hand_curves_are_gone():
    assert not hasattr(President, "gdp_growth_adjusted")
    src = inspect.getsource(president_scorer)
    for literal in ("25 + (effective_gdp / 5.0) * 55", "jobs_per_year / 3.0 * 50", "rules_per_year / 1500"):
        assert literal not in src, literal
