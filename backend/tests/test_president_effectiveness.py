"""Presidential Effectiveness against measured populations (president v5;
docs/research/president-scores.md). Agency Alignment was removed in v7."""

import inspect

from app.models import President
from app.pipeline.analyze import president_scorer
from app.pipeline.analyze.president_scorer import (
    _effectiveness_core,
    compute_president_reference,
    jobs_per_attributed_year,
    peer_relative,
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


def test_postwar_gdp_is_scored_relative_to_peer_economies():
    # 3.0% per person against peers' 2.0%: one point above, one standard
    # deviation over the pinned mean of 0 (conftest).
    # 0.5 of the 1.5-point difference was the peers' catch-up, set aside.
    core = _effectiveness_core(None, 9.9, 4.0, 1990, gdp_per_person=3.5, gdp_peer_median=2.0, gdp_relative=1.0)
    gdp = core["components"][0]
    assert gdp["label"] == "GDP growth vs. peer economies"
    assert gdp["score"] > 50.0
    assert core["facts"]["gdpRelative"] == 1.0 and core["facts"]["gdpRelativeMean"] == 0.0
    assert core["facts"]["gdpCatchUp"] == 0.5
    # The relative figure is what is scored: not the raw difference, and not
    # total growth.
    same = _effectiveness_core(None, 0.1, 4.0, 1990, gdp_per_person=1.0, gdp_peer_median=1.0, gdp_relative=1.0)
    assert core["score"] == same["score"]


def test_peer_figures_do_not_apply_before_1947_or_when_missing():
    assert peer_relative(1890, 1.0) is None
    assert peer_relative(1990, None) is None
    assert gdp_score(3.317, 1890) == _effectiveness_core(
        None, 3.317, 4.0, 1890, gdp_per_person=9.0, gdp_peer_median=0.0, gdp_relative=9.0)["components"][0]["score"]
    # Postwar without peer figures yet: total growth against postwar terms.
    assert _effectiveness_core(None, 2.8371, 4.0, 1990)["components"][0]["label"] == "GDP growth"


def test_jobs_use_the_attributed_window_and_the_measured_population():
    assert jobs_per_attributed_year(7.0, 8.0) == 1.0
    core = _effectiveness_core(1.2351 * 7, None, 8.0, 2009)
    assert core["components"][0]["label"] == "Jobs created"
    assert abs(core["components"][0]["score"] - 50.0) < 0.1


def test_agency_alignment_is_gone():
    """Administrations since 1994 finalize 59.6-61.8% of their rulemakings
    (2026-10-06): too little difference to score."""
    assert not hasattr(president_scorer, "calc_agency_alignment")
    assert "agencyAlignment" not in president_scorer._PRESIDENT_SCORE_FIELD_MAP


def test_reference_measures_each_new_stat_from_the_population():
    rows = [
        {"id": f"p{i}", "name": f"P{i}", "gdp_growth_avg": 2.0 + i % 3, "term_start_year": 1800 + 10 * i,
         "jobs_created_millions": 4.0 + i % 2, "term_years": 4.0}
        for i in range(24)
    ]
    ref = compute_president_reference(rows)
    assert ref["gdp_growth_prewar"]["n"] == 15  # 1800..1940
    # 1950-2030 is only nine presidencies: below the minimum, so it is left
    # out and the caller keeps the last persisted value.
    assert "gdp_growth_postwar" not in ref
    assert ref["jobs_per_year"]["n"] == 24
    assert "gdp_growth_relative" not in ref  # no peer figures given


def test_reference_measures_relative_growth_over_postwar_terms_only():
    rows = [
        {"id": f"p{i}", "term_start_year": 1890 + 8 * i, "gdp_growth_relative": float(i % 2)}
        for i in range(20)
    ]
    ref = compute_president_reference(rows)
    # Terms start 1890, 1898, ... 2042: the 12 from 1954 on are postwar.
    assert ref["gdp_growth_relative"]["n"] == 12
    assert "rulemaking_finalized_pct" not in ref


def test_the_stale_adjusted_gdp_column_and_hand_curves_are_gone():
    assert not hasattr(President, "gdp_growth_adjusted")
    src = inspect.getsource(president_scorer)
    for literal in ("25 + (effective_gdp / 5.0) * 55", "jobs_per_year / 3.0 * 50", "rules_per_year / 1500"):
        assert literal not in src, literal
