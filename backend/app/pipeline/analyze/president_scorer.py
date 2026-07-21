"""Dynamic president score calculator.

For presidents with live API data (Clinton onward), recalculates affected
metric scores from real data. Falls back to seed values for metrics
where live data is unavailable.

Metrics that can be dynamically computed:
  - Competence: EO activity rate only (30% of the formula's weight — see
    calc_competence). Court-success rate and cabinet-turnover rate are
    accepted as optional inputs for a future data source, but nothing in
    this pipeline currently fetches them live, so in practice they are
    never passed and the remaining 70% blends with the seed score.
  - Effectiveness: Derived from employment/GDP data

Metrics that remain static (until more data sources are added):
  - Independence: Requires cabinet composition analysis
  - Follow-Through: Requires promise tracking (PolitiFact-style)
  - Public Mandate: Requires polling data aggregation
  - Competence's court-success-rate and cabinet-turnover-rate components
    (70% of the formula's weight): no structured, machine-readable data
    source for EO litigation outcomes or cabinet tenure is wired up
"""

import logging

logger = logging.getLogger(__name__)


def clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def _blend_components_with_seed(components: list[dict], seed_score: float) -> dict:
    """Combine weighted live-data components with the editorial seed value.

    Shared by _competence_core / _effectiveness_core / _agency_alignment_core,
    which each gathered their own `components` list and then ran this identical
    blending math. With no components it's the pure seed; otherwise the live
    score fills its own weight and the seed fills any remaining (unfetched)
    weight, appended as a visible component.
    """
    if not components:
        return {
            "score": clamp(seed_score), "components": [],
            "note": "No live data available — pure editorial seed value.",
        }

    total_weight = sum(c["weight"] for c in components)
    weighted_sum = sum(c["score"] * c["weight"] for c in components)
    live_score = weighted_sum / total_weight

    remaining_weight = 1.0 - total_weight
    if remaining_weight > 0.01:
        score = clamp(live_score * total_weight + seed_score * remaining_weight)
        components.append({
            "label": "Editorial seed (unfetched components)", "weight": round(remaining_weight, 2),
            "score": round(seed_score, 1),
            "detail": "no live source for the remaining weight — blended with the editorial seed value",
        })
    else:
        score = clamp(live_score)
    return {"score": score, "components": components}


def compute_president_overall_score(entity) -> float:
    """Weighted overall score from a scored President row.

    Single source of truth for the PRESIDENT_SCORE_WEIGHTS-weighted sum —
    previously hand-rolled independently in both president_service.py's
    response builder and its leaderboard sort, with default weight values
    baked into one of the two copies. Mirrors score_calculator.
    compute_overall_score's reasoning for senators/reps: summing
    dynamically over PRESIDENT_SCORE_WEIGHTS.items() means a future
    weight-table change can't silently desync from this formula again.
    """
    from app.config_definitions import PRESIDENT_SCORE_WEIGHTS

    _FIELD_MAP = {
        "independence": "score_independence",
        "followThrough": "score_follow_through",
        "publicMandate": "score_public_mandate",
        "effectiveness": "score_effectiveness",
        "competence": "score_competence",
        "agencyAlignment": "score_agency_alignment",
    }
    return round(
        sum(
            getattr(entity, _FIELD_MAP[key], 0) * weight
            for key, weight in PRESIDENT_SCORE_WEIGHTS.items()
        ),
        2,
    )


def calc_competence(
    eo_count: int | None,
    eo_court_success_pct: float | None,
    cabinet_turnover_pct: float | None,
    term_years: float,
    seed_score: float,
) -> int:
    """Calculate competence score, blending live data with the seed score.

    See _competence_core for the full component breakdown — this is a thin
    wrapper kept for existing callers/tests that expect a bare int.
    """
    return _competence_core(
        eo_count, eo_court_success_pct, cabinet_turnover_pct, term_years, seed_score,
    )["score"]


def _competence_core(
    eo_count: int | None,
    eo_court_success_pct: float | None,
    cabinet_turnover_pct: float | None,
    term_years: float,
    seed_score: float,
) -> dict:
    """Same math as calc_competence, returning every intermediate value
    alongside the final score.

    Components (weighted):
      - Court success rate (40%): Higher = more legally sound drafting.
        No fetch source is wired up for this (2026-07 audit) — the
        pipeline never passes eo_court_success_pct, so this weight
        always falls through to seed_score via the blending below.
      - Cabinet stability (30%): Lower turnover = better management.
        Same — cabinet_turnover_pct is never fetched live either.
      - EO activity rate (30%): Moderate rate is ideal; very high or
        very low rates suggest either overreliance on EOs or inaction.
        This is the only component genuinely computed from live data
        (Federal Register EO counts).

    Any component whose input is None is excluded and its weight blends
    with seed_score instead (see the blending step below) — this is what
    keeps the two permanently-unfetched components honest rather than
    silently defaulting to some fixed number.
    """
    components: list[dict] = []

    if eo_court_success_pct is not None:
        components.append({
            "label": "Court success rate", "weight": 0.40,
            "score": round(eo_court_success_pct, 1),
            "detail": "share of executive orders that survived legal challenge",
        })

    if cabinet_turnover_pct is not None:
        stability_score = max(0, 100 - cabinet_turnover_pct * 1.3)
        components.append({
            "label": "Cabinet stability", "weight": 0.30,
            "score": round(stability_score, 1),
            "detail": f"{cabinet_turnover_pct:.0f}% cabinet turnover",
        })

    if eo_count and term_years > 0:
        eo_per_year = eo_count / term_years
        # Moderate rate (30-60/year) scores highest; extremes penalized
        if eo_per_year <= 50:
            rate_score = min(eo_per_year / 50 * 70, 70) + 20
        else:
            rate_score = max(90 - (eo_per_year - 50) * 0.3, 30)
        components.append({
            "label": "EO activity rate", "weight": 0.30,
            "score": round(rate_score, 1),
            "detail": f"{eo_count} executive orders over {term_years:.1f} years = {eo_per_year:.1f}/year",
        })

    return _blend_components_with_seed(components, seed_score)


def calc_effectiveness(
    jobs_created_millions: float | None,
    gdp_growth_avg: float | None,
    term_years: float,
    seed_score: float,
    gdp_growth_adjusted: float | None = None,
) -> int:
    """Calculate effectiveness score from economic data.

    See _effectiveness_core for the full component breakdown — this is a
    thin wrapper kept for existing callers/tests that expect a bare int.
    """
    return _effectiveness_core(
        jobs_created_millions, gdp_growth_avg, term_years, seed_score, gdp_growth_adjusted,
    )["score"]


def _effectiveness_core(
    jobs_created_millions: float | None,
    gdp_growth_avg: float | None,
    term_years: float,
    seed_score: float,
    gdp_growth_adjusted: float | None = None,
) -> dict:
    """Same math as calc_effectiveness, returning every intermediate value
    alongside the final score.

    Components (weighted):
      - GDP growth (60%): Compared to post-WWII average of ~3.2%
      - Jobs created (40%): Normalized per year of term

    GDP adjustment — first-year exclusion
    ----------------------------------------
    When per-year GDP data is available, uses gdp_growth_adjusted which
    excludes the first calendar year of the term.  The first year's GDP
    primarily reflects the preceding administration's fiscal policy,
    legislation, and macroeconomic inheritance.  Romer & Romer (2010,
    AER 100(3), 763–801) document a 6–18 month transmission lag for
    fiscal policy changes.  Blinder & Watson (2016, AER 106(4), 1015–1045)
    and Bartels (2008, 'Unequal Democracy,' Princeton UP, Table 2.1)
    both start measuring presidential economic performance from the second
    year of the term on this basis.

    When gdp_growth_adjusted is None, falls back to gdp_growth_avg
    (full-term average, used for historical presidents and as a seed).

    References:
      Blinder, A.S., & Watson, M.W. (2016). AER 106(4), 1015–1045.
      Bartels, L.M. (2008). Unequal Democracy. Princeton UP.
      Romer, C.D., & Romer, D.H. (2010). AER 100(3), 763–801.
    """
    components: list[dict] = []

    effective_gdp = gdp_growth_adjusted if gdp_growth_adjusted is not None else gdp_growth_avg

    if effective_gdp is not None:
        # Scale: post-WWII average 3.2% → ~55 (slightly above mid-point).
        # 5% → ~80; 0% → ~25; negative values score below 25.
        gdp_score = 25 + (effective_gdp / 5.0) * 55
        gdp_score = max(0.0, min(100.0, gdp_score))
        components.append({
            "label": "GDP growth", "weight": 0.60, "score": round(gdp_score, 1),
            "detail": (
                f"{effective_gdp:.1f}% average annual growth "
                f"({'year-1-excluded' if gdp_growth_adjusted is not None else 'full-term average'}) "
                "vs. post-WWII average 3.2%"
            ),
        })

    if jobs_created_millions is not None and term_years > 0:
        jobs_per_year = jobs_created_millions / term_years
        # 2.5M/year = strong (75), negative = very low
        if jobs_per_year >= 0:
            job_score = min(30 + jobs_per_year / 3.0 * 50, 95)
        else:
            job_score = max(30 + jobs_per_year * 15, 5)
        components.append({
            "label": "Jobs created", "weight": 0.40, "score": round(job_score, 1),
            "detail": f"{jobs_created_millions:.1f}M jobs over {term_years:.1f} years = {jobs_per_year:.2f}M/year",
        })

    return _blend_components_with_seed(components, seed_score)


def calc_agency_alignment(
    rulemaking_count: int | None,
    rulemaking_finalized_pct: float | None,
    term_years: float,
    seed_score: float,
) -> int:
    """Calculate agency alignment score from Federal Register rulemaking data.

    See _agency_alignment_core for the full component breakdown — this is a
    thin wrapper kept for existing callers/tests that expect a bare int.
    """
    return _agency_alignment_core(
        rulemaking_count, rulemaking_finalized_pct, term_years, seed_score,
    )["score"]


def _agency_alignment_core(
    rulemaking_count: int | None,
    rulemaking_finalized_pct: float | None,
    term_years: float,
    seed_score: float,
) -> dict:
    """Same math as calc_agency_alignment, returning every intermediate
    value alongside the final score.

    Components (weighted):
      - Rulemaking activity rate (50%): Agencies actively producing rules
        aligned with the agenda. Moderate-to-high rate scores well.
      - Finalization rate (50%): Ratio of final rules to total rulemaking
        (proposed + final). Higher = agencies follow through effectively.
    """
    components: list[dict] = []

    if rulemaking_count and term_years > 0:
        rules_per_year = rulemaking_count / term_years
        # 500-2000 rules/year is typical modern rate; scale accordingly
        if rules_per_year <= 1500:
            activity_score = min(25 + rules_per_year / 1500 * 55, 80)
        else:
            activity_score = min(80 + (rules_per_year - 1500) / 2000 * 15, 95)
        components.append({
            "label": "Rulemaking activity rate", "weight": 0.50, "score": round(activity_score, 1),
            "detail": f"{rulemaking_count} rulemakings over {term_years:.1f} years = {rules_per_year:.0f}/year",
        })

    if rulemaking_finalized_pct is not None:
        # Higher finalization = agencies completing the rulemaking process
        final_score = min(20 + rulemaking_finalized_pct * 0.7, 95)
        components.append({
            "label": "Finalization rate", "weight": 0.50, "score": round(final_score, 1),
            "detail": f"{rulemaking_finalized_pct:.0f}% of rulemakings reached a final rule",
        })

    return _blend_components_with_seed(components, seed_score)


def recalculate_president_scores(
    president_id: str,
    seed_scores: dict,
    live_data: dict,
    term_years: float,
) -> dict:
    """Recalculate scores blending live data with seed values.

    Args:
        president_id: e.g. "obama-44"
        seed_scores: Dict with keys independence, follow_through, etc.
        live_data: Dict with keys eo_count, jobs_created_millions, etc.
        term_years: Length of term in years

    Returns:
        Dict with updated score values.
    """
    scores = dict(seed_scores)

    scores["score_competence"] = calc_competence(
        eo_count=live_data.get("eo_count"),
        eo_court_success_pct=live_data.get("eo_court_success_pct"),
        cabinet_turnover_pct=live_data.get("cabinet_turnover_pct"),
        term_years=term_years,
        seed_score=seed_scores.get("score_competence", 50),
    )

    scores["score_effectiveness"] = calc_effectiveness(
        jobs_created_millions=live_data.get("jobs_created_millions"),
        gdp_growth_avg=live_data.get("gdp_growth_avg"),
        term_years=term_years,
        seed_score=seed_scores.get("score_effectiveness", 50),
        gdp_growth_adjusted=live_data.get("gdp_growth_adjusted"),
    )

    scores["score_agency_alignment"] = calc_agency_alignment(
        rulemaking_count=live_data.get("rulemaking_count"),
        rulemaking_finalized_pct=live_data.get("rulemaking_finalized_pct"),
        term_years=term_years,
        seed_score=seed_scores.get("score_agency_alignment", 50),
    )

    return scores
