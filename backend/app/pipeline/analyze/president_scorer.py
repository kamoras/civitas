"""Dynamic president score calculator.

For presidents with live API data (Clinton onward), recalculates affected
metric scores from real data. Falls back to seed values for metrics
where live data is unavailable.

Metrics that can be dynamically computed:
  - Competence: Derived from EO activity patterns and cabinet stability
  - Effectiveness: Derived from employment/GDP data

Metrics that remain static (until more data sources are added):
  - Independence: Requires cabinet composition analysis
  - Follow-Through: Requires promise tracking (PolitiFact-style)
  - Public Mandate: Requires polling data aggregation
"""

import logging

logger = logging.getLogger(__name__)


def clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def calc_competence(
    eo_count: int | None,
    eo_court_success_pct: float | None,
    cabinet_turnover_pct: float | None,
    term_years: float,
    seed_score: float,
) -> int:
    """Calculate competence score from live data.

    Components (weighted):
      - Court success rate (40%): Higher = more legally sound drafting
      - Cabinet stability (30%): Lower turnover = better management
      - EO activity rate (30%): Moderate rate is ideal; very high or
        very low rates suggest either overreliance on EOs or inaction
    """
    components: list[tuple[float, float]] = []

    if eo_court_success_pct is not None:
        components.append((eo_court_success_pct, 0.40))

    if cabinet_turnover_pct is not None:
        stability_score = max(0, 100 - cabinet_turnover_pct * 1.3)
        components.append((stability_score, 0.30))

    if eo_count is not None and term_years > 0:
        eo_per_year = eo_count / term_years
        # Moderate rate (30-60/year) scores highest; extremes penalized
        if eo_per_year <= 50:
            rate_score = min(eo_per_year / 50 * 70, 70) + 20
        else:
            rate_score = max(90 - (eo_per_year - 50) * 0.3, 30)
        components.append((rate_score, 0.30))

    if not components:
        return clamp(seed_score)

    total_weight = sum(w for _, w in components)
    weighted_sum = sum(s * w for s, w in components)
    live_score = weighted_sum / total_weight

    remaining_weight = 1.0 - total_weight
    if remaining_weight > 0.01:
        return clamp(live_score * total_weight + seed_score * remaining_weight)
    return clamp(live_score)


def calc_effectiveness(
    jobs_created_millions: float | None,
    gdp_growth_avg: float | None,
    term_years: float,
    seed_score: float,
) -> int:
    """Calculate effectiveness score from economic data.

    Components (weighted):
      - GDP growth (60%): Compared to post-WWII average of ~3.2%
      - Jobs created (40%): Normalized per year of term
    """
    components: list[tuple[float, float]] = []

    if gdp_growth_avg is not None:
        # Scale: 3.2% avg = 55 (slightly above average), 5% = 80, 0% = 25
        gdp_score = 25 + (gdp_growth_avg / 5.0) * 55
        gdp_score = max(0, min(100, gdp_score))
        components.append((gdp_score, 0.60))

    if jobs_created_millions is not None and term_years > 0:
        jobs_per_year = jobs_created_millions / term_years
        # 2.5M/year = strong (75), negative = very low
        if jobs_per_year >= 0:
            job_score = min(30 + jobs_per_year / 3.0 * 50, 95)
        else:
            job_score = max(30 + jobs_per_year * 15, 5)
        components.append((job_score, 0.40))

    if not components:
        return clamp(seed_score)

    total_weight = sum(w for _, w in components)
    weighted_sum = sum(s * w for s, w in components)
    live_score = weighted_sum / total_weight

    remaining_weight = 1.0 - total_weight
    if remaining_weight > 0.01:
        return clamp(live_score * total_weight + seed_score * remaining_weight)
    return clamp(live_score)


def calc_agency_alignment(
    rulemaking_count: int | None,
    rulemaking_finalized_pct: float | None,
    term_years: float,
    seed_score: float,
) -> int:
    """Calculate agency alignment score from Federal Register rulemaking data.

    Components (weighted):
      - Rulemaking activity rate (50%): Agencies actively producing rules
        aligned with the agenda. Moderate-to-high rate scores well.
      - Finalization rate (50%): Ratio of final rules to total rulemaking
        (proposed + final). Higher = agencies follow through effectively.
    """
    components: list[tuple[float, float]] = []

    if rulemaking_count is not None and term_years > 0:
        rules_per_year = rulemaking_count / term_years
        # 500-2000 rules/year is typical modern rate; scale accordingly
        if rules_per_year <= 1500:
            activity_score = min(25 + rules_per_year / 1500 * 55, 80)
        else:
            activity_score = min(80 + (rules_per_year - 1500) / 2000 * 15, 95)
        components.append((activity_score, 0.50))

    if rulemaking_finalized_pct is not None:
        # Higher finalization = agencies completing the rulemaking process
        final_score = min(20 + rulemaking_finalized_pct * 0.7, 95)
        components.append((final_score, 0.50))

    if not components:
        return clamp(seed_score)

    total_weight = sum(w for _, w in components)
    weighted_sum = sum(s * w for s, w in components)
    live_score = weighted_sum / total_weight

    remaining_weight = 1.0 - total_weight
    if remaining_weight > 0.01:
        return clamp(live_score * total_weight + seed_score * remaining_weight)
    return clamp(live_score)


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
    )

    scores["score_agency_alignment"] = calc_agency_alignment(
        rulemaking_count=live_data.get("rulemaking_count"),
        rulemaking_finalized_pct=live_data.get("rulemaking_finalized_pct"),
        term_years=term_years,
        seed_score=seed_scores.get("score_agency_alignment", 50),
    )

    return scores
