"""President score calculator.

Every dimension, for every president, is computed entirely from real
fetched/historical data — there is no seed fallback anywhere in this
file (removed 2026-07, see president_service.py's module docstring for
the full account). A dimension a president has no real data source for
is None, never a hand-set or neutral placeholder.

Independence and Follow-Through removed entirely (2026-07, see
president_service.py's module docstring for the full account) — both were
always 100% hand-set with no live formula and no realistic path to one
(Independence's obvious source, OpenSecrets' revolving-door API, was
discontinued in 2025; Follow-Through needs the same promise-matching
technique already proven unworkable for senators). Rather than keep
presenting a hand-set number as a computed score, they're gone, and their
combined weight was redistributed (see PRESIDENT_SCORE_WEIGHTS in
config_definitions.py) to the four dimensions below.

Metrics that can be dynamically computed:
  - Competence: EO activity rate only (30% of the formula's nominal
    weight — see calc_competence), scored relative to the president's own
    era (see _eo_activity_rate_component) rather than one fixed scale —
    "executive order" as a systematic governance tool is largely a
    20th-century phenomenon, so a single absolute threshold implicitly
    calibrated to modern volume misreads every pre-1901 president as
    "inactive" almost by construction. Court-success rate and cabinet-
    turnover rate are accepted as optional inputs for a future data
    source, but nothing in this pipeline currently fetches them live, so
    in practice they are never passed and Competence runs on EO-activity-
    rate alone, renormalized to 100% of the measured weight (see
    _blend_live_components).
  - Effectiveness: Derived from employment/GDP data — GDP growth uses a
    peak-relative CAGR instead of a plain term average when the term
    begins mid-recovery from a real contraction (see historical_gdp.
    compute_term_gdp_growth), so a depression-rebound's own arithmetic
    isn't mistaken for sustained economic management.
  - Public Mandate (2026-07): average approval + trend, computed from
    real polling data scraped from UCSB's American Presidency Project
    (see app.pipeline.fetch.presidential_approval) — Gallup, this
    platform's original live source, ended presidential approval tracking
    entirely in Feb 2026; UCSB is the replacement, still updated for the
    sitting president post-Gallup (aggregating AP-NORC/CNN-SSRS/Marist/
    Pew/Verasight). Covers Truman-33 onward (15 presidents with a real
    UCSB approval-poll page); earlier presidents use the election-margin
    proxy instead (see calc_public_mandate) — never a seed value.

Metrics that remain static (roadmap, not abandoned):
  - Competence's cabinet-turnover-rate: Wikidata SPARQL (wdt:P39
    position-held with date qualifiers) is a real, precedented candidate
    — direct date math, not fuzzy matching. Not yet built.
  - Competence's court-success-rate: deliberately not pursued. Matching
    an EO to its litigation outcomes needs the same kind of fuzzy
    text-matching that sank Follow-Through — CourtListener has the case
    law but nothing connects "EO 14036" to "the lawsuits that challenged
    it" without it. Treated the same as Independence/Follow-Through:
    don't build an unreliable pipeline just to have a number.
"""

import logging
import math

logger = logging.getLogger(__name__)


def clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def _blend_live_components(components: list[dict]) -> dict:
    """Combine weighted live-data components into a score — live data
    only, never a hand-set fallback.

    Shared by _competence_core / _effectiveness_core / _agency_alignment_
    core / _public_mandate_core, which each gather their own `components`
    list of whatever sub-signals actually have live data this run.

    2026-07: this used to blend missing weight with a hand-set "editorial
    seed" value — a one-time, uncited number with no more standing than a
    guess (see president_service.py's module docstring for the full
    account of why that was removed platform-wide). A component with no
    live source now simply isn't included, and the WEIGHT of whatever IS
    live gets renormalized to 100% of what was actually measured — same
    pattern score_calculator.py already uses when a senator/rep's
    Coalition Breadth is unavailable (breadth_weight=0, party_weight=1.0
    there; the exact same renormalize-onto-available-signal shape here).

    With zero live components, this returns score=None — NOT a neutral
    50. Deliberately different from score_calculator.py's "missing data
    floors at neutral" convention: that convention exists for a signal
    that's temporarily unmeasured for one entity but conceptually always
    applies. A president dimension with zero components isn't temporarily
    unmeasured, it's a case this file's fetchers have identified as
    genuinely inapplicable (e.g. Public Mandate for a president who never
    won an election, Agency Alignment before Federal Register existed) —
    scoring it neutral would still be presenting a number for something
    that isn't measurable even in principle. Callers (president_pipeline.
    py) skip writing a None score, leaving the DB column NULL; compute_
    president_overall_score renormalizes across whichever dimensions
    aren't NULL for that specific president.
    """
    if not components:
        return {
            "score": None, "components": [],
            "note": "Not applicable for this president — no data source exists even in principle, not merely unfetched.",
        }

    total_weight = sum(c["weight"] for c in components)
    weighted_sum = sum(c["score"] * c["weight"] for c in components)
    score = clamp(weighted_sum / total_weight)

    if total_weight < 0.999:
        # Renormalize displayed weights to sum to 1.0 so the "show the
        # math" panel's percentages reflect what was actually measured,
        # not the full formula's nominal split.
        for c in components:
            c["weight"] = round(c["weight"] / total_weight, 2)

    return {"score": score, "components": components}


_PRESIDENT_SCORE_FIELD_MAP = {
    "publicMandate": "score_public_mandate",
    "effectiveness": "score_effectiveness",
    "competence": "score_competence",
    "agencyAlignment": "score_agency_alignment",
}


def compute_president_overall_score(entity) -> float:
    """Weighted overall score from a scored President row, renormalized
    per-president over whichever dimensions actually have a value.

    Single source of truth for the PRESIDENT_SCORE_WEIGHTS-weighted sum —
    previously hand-rolled independently in both president_service.py's
    response builder and its leaderboard sort, with default weight values
    baked into one of the two copies.

    2026-07: every dimension's score field is now nullable (see models.py
    President's comment) — a dimension is None when it's genuinely
    inapplicable for that specific president (e.g. Public Mandate for a
    president who never won an election; Agency Alignment for anyone
    before the Federal Register existed in 1936), never a hand-set
    fallback. Renormalizing PRESIDENT_SCORE_WEIGHTS over just the
    present dimensions is the same "redistribute onto what's actually
    measured" pattern score_calculator.compute_overall_score already uses
    for senators/reps (and the same pattern this file's own
    _blend_live_components uses one level down, within a single
    dimension's components) — applied here one level up, across
    dimensions. A president with zero computable dimensions (should not
    happen given the coverage of the fetchers wired into president_
    pipeline.py, but defensive) returns 0.0 rather than raising.
    """
    from app.config_definitions import PRESIDENT_SCORE_WEIGHTS

    present = [
        (weight, getattr(entity, _PRESIDENT_SCORE_FIELD_MAP[key]))
        for key, weight in PRESIDENT_SCORE_WEIGHTS.items()
        if getattr(entity, _PRESIDENT_SCORE_FIELD_MAP[key]) is not None
    ]
    total_weight = sum(w for w, _ in present)
    if total_weight <= 0:
        return 0.0
    return round(sum(w * score for w, score in present) / total_weight, 2)


# Presidential scoring formula version, same purpose as score_calculator.
# ALGORITHM_VERSION for senators/reps (tags each ScoreSnapshot so trend
# charts can mark a formula change instead of reading it as a behavior
# change) — introduced here (2026-07) rather than retroactively for the
# implicit original 6-dimension formula, since presidents only start
# getting snapshotted at this version. v2 = the 4-dimension formula after
# Independence/Follow-Through were removed and their weight redistributed
# (see PRESIDENT_SCORE_WEIGHTS's own comment).
PRESIDENT_ALGORITHM_VERSION = "v2"


# Full credit/deficit approached asymptotically at this many population
# standard deviations from the mean, via tanh (smooth saturation — see
# _population_zscore_component) rather than a hard clamp. A hard clamp
# (this file's original design) makes every value beyond the threshold
# read identically, which loses real information: computed 2026-07 from
# real election-margin data, Washington (z=3.44), Monroe (z=2.58),
# Harding (z=1.62), and Coolidge (z=1.52) all clamped to the exact same
# 100 under a hard cutoff, flattening a genuinely wide range of landslide
# magnitudes into one indistinguishable ceiling score. tanh still favors
# the biggest landslides (Washington still scores highest) without
# erasing the gap between "historically exceptional" and "merely very
# good." Same "~1.5x stdev" shape as score_calculator.py's
# _LES_CREDIT_SATURATION, now smoothed rather than hard-clamped.
_ZSCORE_SATURATION_STDEV = 1.5


def _population_zscore_component(
    label: str, weight: float, value: float, population_mean: float,
    population_stdev: float, detail: str,
) -> dict:
    z = (value - population_mean) / population_stdev if population_stdev else 0.0
    normalized = math.tanh(z / _ZSCORE_SATURATION_STDEV)
    return {
        "label": label, "weight": weight,
        "score": round(50.0 + 50.0 * normalized, 1),
        "detail": detail,
    }


# EO-activity-rate population statistics, split at 1901 (Theodore
# Roosevelt) rather than one fixed "30-60/year is optimal" scale (this
# component's original design, with no empirical basis found for why 50
# specifically was the threshold). Computed 2026-07 from real UCSB EO-
# count data across every president with a nonzero count: EO rate never
# exceeds ~46/year before 1901 (mean=12.02, stdev=14.04, n=24); from
# Theodore Roosevelt onward every single president is 34.5-310.5/year
# (mean=117.25, stdev=86.10, n=22) — a roughly 10x regime shift, not a
# gradual trend, matching the well-documented history that TR was the
# first president to use executive orders as a systematic governance
# tool. A single fixed absolute scale calibrated to modern EO volume
# scored essentially every pre-TR president as "very low activity" almost
# by construction (e.g. Lincoln's 12/year — right at his own era's
# average — read as a weak 37 under the old scale), regardless of how
# actively they governed relative to the tools and norms of their own
# time. This also drops the old scale's "moderate is optimal, extreme is
# penalized" shape: no empirical basis was found for treating unusually
# high EO usage as evidence of worse administrative execution (as opposed
# to a separate, more political judgment this dimension isn't designed to
# make) — a saturating-but-monotonic population-relative score is more
# defensible than an unvalidated "goldilocks zone."
_EO_RATE_ERA_SPLIT_YEAR = 1901
_EO_RATE_PRE_1901_MEAN = 12.02
_EO_RATE_PRE_1901_STDEV = 14.04
_EO_RATE_POST_1901_MEAN = 117.25
_EO_RATE_POST_1901_STDEV = 86.10


def _eo_activity_rate_component(eo_count: int, term_years: float, term_start_year: int) -> dict:
    eo_per_year = eo_count / term_years
    if term_start_year < _EO_RATE_ERA_SPLIT_YEAR:
        mean, stdev, era_label = _EO_RATE_PRE_1901_MEAN, _EO_RATE_PRE_1901_STDEV, "pre-1901"
    else:
        mean, stdev, era_label = _EO_RATE_POST_1901_MEAN, _EO_RATE_POST_1901_STDEV, "1901-present"
    return _population_zscore_component(
        "EO activity rate", 0.30, eo_per_year, mean, stdev,
        f"{eo_count} executive orders over {term_years:.1f} years = {eo_per_year:.1f}/year, "
        f"vs. {era_label} population mean {mean:.1f}/year",
    )


def calc_competence(
    eo_count: int | None,
    eo_court_success_pct: float | None,
    cabinet_turnover_pct: float | None,
    term_years: float,
    term_start_year: int,
) -> int | None:
    """Calculate competence score from live data only.

    See _competence_core for the full component breakdown — this is a thin
    wrapper kept for existing callers/tests that expect a bare int.
    """
    return _competence_core(
        eo_count, eo_court_success_pct, cabinet_turnover_pct, term_years, term_start_year,
    )["score"]


def _competence_core(
    eo_count: int | None,
    eo_court_success_pct: float | None,
    cabinet_turnover_pct: float | None,
    term_years: float,
    term_start_year: int,
) -> dict:
    """Same math as calc_competence, returning every intermediate value
    alongside the final score.

    Components (weighted):
      - Court success rate (40%): Higher = more legally sound drafting.
        No fetch source is wired up for this yet (2026-07 audit) — see
        the "Metrics that remain static" note in this module's docstring
        for why (CourtListener has case law but no structured EO-to-
        litigation mapping) — so eo_court_success_pct is currently always
        None and this component never contributes.
      - Cabinet stability (30%): Lower turnover = better management. Same
        — cabinet_turnover_pct has a real, identified candidate source
        (Wikidata) but no fetcher built yet, so it's currently always None.
      - EO activity rate (30%): scored relative to the president's own
        era's real population (see _eo_activity_rate_component) rather
        than a single fixed scale — this is the only component genuinely
        computed from live data today (UCSB's EO-count table).

    Any component whose input is None is simply excluded — the weight of
    whatever IS live gets renormalized to 100% of what was measured (see
    _blend_live_components). No hand-set fallback for the other two, ever
    — a missing component means Competence is currently computed from
    less than the full formula, disclosed as such, not papered over with
    a fabricated number.
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
        components.append(_eo_activity_rate_component(eo_count, term_years, term_start_year))

    return _blend_live_components(components)


def calc_effectiveness(
    jobs_created_millions: float | None,
    gdp_growth_avg: float | None,
    term_years: float,
    gdp_growth_adjusted: float | None = None,
) -> int | None:
    """Calculate effectiveness score from economic data only.

    See _effectiveness_core for the full component breakdown — this is a
    thin wrapper kept for existing callers/tests that expect a bare int.
    """
    return _effectiveness_core(
        jobs_created_millions, gdp_growth_avg, term_years, gdp_growth_adjusted,
    )["score"]


def _effectiveness_core(
    jobs_created_millions: float | None,
    gdp_growth_avg: float | None,
    term_years: float,
    gdp_growth_adjusted: float | None = None,
) -> dict:
    """Same math as calc_effectiveness, returning every intermediate value
    alongside the final score.

    Components (weighted):
      - GDP growth (60%): Compared to post-WWII average of ~3.2%. Real for
        every president back to 1790 (gdp_growth_avg is populated from
        BEA/FRED for Truman-33 onward, and from MeasuringWorth's
        historical annual real-GDP series — app.pipeline.fetch.
        historical_gdp — for every president before that; both are the
        same "average annual growth over the term" figure regardless of
        which live source computed it).
      - Jobs created (40%): Normalized per year of term. Only available
        from BLS payroll data (1939 onward, per economic_data.py) — no
        equivalent historical employment series exists for earlier
        presidents, so this component is genuinely absent (not
        defaulted) for anyone before that, and Effectiveness for those
        presidents is 100% GDP growth via _blend_live_components'
        renormalization.

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
    (full-term average — used for historical presidents, where a
    year-1-exclusion adjustment hasn't been computed).

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

    return _blend_live_components(components)


def calc_agency_alignment(
    rulemaking_count: int | None,
    rulemaking_finalized_pct: float | None,
    term_years: float,
) -> int | None:
    """Calculate agency alignment score from Federal Register rulemaking data.

    See _agency_alignment_core for the full component breakdown — this is a
    thin wrapper kept for existing callers/tests that expect a bare int.
    """
    return _agency_alignment_core(
        rulemaking_count, rulemaking_finalized_pct, term_years,
    )["score"]


def _agency_alignment_core(
    rulemaking_count: int | None,
    rulemaking_finalized_pct: float | None,
    term_years: float,
) -> dict:
    """Same math as calc_agency_alignment, returning every intermediate
    value alongside the final score.

    Components (weighted):
      - Rulemaking activity rate (50%): Agencies actively producing rules
        aligned with the agenda. Moderate-to-high rate scores well.
      - Finalization rate (50%): Ratio of final rules to total rulemaking
        (proposed + final). Higher = agencies follow through effectively.

    No historical proxy exists for this dimension before the Federal
    Register itself began in 1936 (Federal Register Act) — unlike Public
    Mandate (election margins) or Effectiveness (MeasuringWorth's GDP
    series), "agency rulemaking" isn't a construct with an equivalent
    that predates the record-keeping mechanism that defines it: the
    modern notice-and-comment regulatory apparatus this dimension
    measures didn't functionally exist yet either. This is a genuine
    conceptual absence, not an unfetched dataset — Agency Alignment is
    fully excluded (not defaulted) for every president before Federal
    Register data exists, via compute_president_overall_score's
    per-president renormalization.
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

    return _blend_live_components(components)


# Population statistics for the term-average-approval and approval-trend
# components below, computed 2026-07 from real UCSB American Presidency
# Project data across all 15 presidents with a live approval-poll page
# (Truman-33 through the current term) via
# app.pipeline.fetch.presidential_approval.fetch_president_approval_history
# — same "fit against real fetched data before shipping" discipline as
# every other calibration constant in this file (see e.g. score_calculator
# .py's _LES_POPULATION_AVG_SENATE/_HOUSE).
#
# Trend is last-quartile-minus-first-quartile average approval across a
# term. The population trend mean is sharply negative (-13.7, i.e. the
# typical president's approval drops ~14 points from term-start to
# term-end) — a well-documented "honeymoon fades" pattern in the
# presidential-approval literature, not this platform's own finding, so
# trend must be scored against that population average, not against zero:
# comparing to zero would count normal, universal decline as a failure for
# nearly every president in the dataset (only Reagan/Clinton/Trump-45 had
# a flat-or-positive raw trend).
_PUBLIC_MANDATE_AVG_APPROVAL_MEAN = 50.93
_PUBLIC_MANDATE_AVG_APPROVAL_STDEV = 9.06
_PUBLIC_MANDATE_TREND_MEAN = -13.72
_PUBLIC_MANDATE_TREND_STDEV = 14.65

# Pre-polling-era (pre-Truman) proxy: average margin of victory (%) across
# a president's own election win(s) — see
# app.pipeline.fetch.presidential_elections. Population stats computed
# 2026-07 from real fetched data across the 42 presidents who won at
# least one presidential election (n=42, mean=9.44, stdev=10.46) — same
# fit-against-real-data discipline as every constant in this file. The
# five presidents who never won a presidential election in their own
# right (succeeded via a predecessor's death, or — Ford — appointed VP
# under the 25th Amendment and never elected to anything nationally) have
# neither this nor approval data; Public Mandate is fully excluded for
# them (see compute_president_overall_score's renormalization), not
# defaulted.
_PUBLIC_MANDATE_ELECTION_MARGIN_MEAN = 9.44
_PUBLIC_MANDATE_ELECTION_MARGIN_STDEV = 10.46


def calc_public_mandate(
    avg_approval: float | None,
    approval_trend: float | None,
    election_margin: float | None,
) -> int | None:
    """Calculate Public Mandate score from real data only — approval
    polling where it exists, election margin as the pre-polling-era
    historical proxy otherwise. None if neither exists for this
    president (see _public_mandate_core).

    See _public_mandate_core for the full component breakdown — this is a
    thin wrapper kept for the same reuse contract as calc_competence/
    calc_effectiveness/calc_agency_alignment.
    """
    return _public_mandate_core(avg_approval, approval_trend, election_margin)["score"]


def _public_mandate_core(
    avg_approval: float | None,
    approval_trend: float | None,
    election_margin: float | None,
) -> dict:
    """Same math as calc_public_mandate, returning every intermediate
    value alongside the final score.

    Two mutually exclusive paths, depending on what data exists for this
    president (never both, never neither-with-a-fallback):

      - Approval polling exists (Truman-33 onward, from UCSB — see
        presidential_approval.py): average approval over the term (70%)
        + approval trend across the term (30%), both z-scored against
        real population stats (see constants above). This is the direct,
        primary "public mandate" measure where it's available.
      - No approval polling (pre-Truman): falls back to election margin
        — the average margin of victory across the president's own
        election win(s), z-scored against its own real population stats
        — the pre-polling-era historical proxy, not a guess.
      - Neither (the five presidents who never won a presidential
        election): zero components, score=None — Public Mandate doesn't
        apply to them, full stop, not "we don't know so it's neutral."
    """
    components: list[dict] = []

    if avg_approval is not None:
        components.append(_population_zscore_component(
            "Average approval", 0.70, avg_approval,
            _PUBLIC_MANDATE_AVG_APPROVAL_MEAN, _PUBLIC_MANDATE_AVG_APPROVAL_STDEV,
            f"{avg_approval:.1f}% average approval over the term vs. "
            f"population mean {_PUBLIC_MANDATE_AVG_APPROVAL_MEAN:.1f}%",
        ))
        if approval_trend is not None:
            components.append(_population_zscore_component(
                "Approval trend", 0.30, approval_trend,
                _PUBLIC_MANDATE_TREND_MEAN, _PUBLIC_MANDATE_TREND_STDEV,
                f"{approval_trend:+.1f}pt change from term-start to term-end vs. "
                f"population average {_PUBLIC_MANDATE_TREND_MEAN:+.1f}pt "
                "(most presidents' approval declines over a term)",
            ))
    elif election_margin is not None:
        components.append(_population_zscore_component(
            "Election margin (pre-polling-era proxy)", 1.0, election_margin,
            _PUBLIC_MANDATE_ELECTION_MARGIN_MEAN, _PUBLIC_MANDATE_ELECTION_MARGIN_STDEV,
            f"{election_margin:+.1f}pt average margin of victory across this president's "
            f"election win(s) vs. population mean {_PUBLIC_MANDATE_ELECTION_MARGIN_MEAN:+.1f}pt "
            "— no approval-polling era data exists for this president, so this is the "
            "historical proxy used instead",
        ))

    return _blend_live_components(components)


def recalculate_president_scores(
    president_id: str, live_data: dict, term_years: float, term_start_year: int,
) -> dict:
    """Recalculate every dimension from live data only, for one president.

    2026-07: this used to bundle "the DYNAMIC_PRESIDENTS cohort's full
    recalculation" specifically, blending with a seed_scores fallback for
    anything unfetched. Now that EO-rate (historical_executive_orders.py)
    and GDP (historical_gdp.py) cover the full presidency rather than
    just Federal-Register/BLS's 1994-plus and 1939/1947-plus windows,
    every president goes through this same function — president_
    pipeline.py calls it once per president in a single unified loop
    rather than splitting DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_PRESIDENTS
    into separate partial-recalculation branches.

    Args:
        president_id: e.g. "obama-44"
        live_data: Dict with keys eo_count, jobs_created_millions,
            gdp_growth_avg, gdp_growth_adjusted, rulemaking_count,
            rulemaking_finalized_pct, eo_court_success_pct,
            cabinet_turnover_pct, avg_approval, approval_trend,
            election_margin — any subset may be present; each calc_*
            function handles its own missing inputs.
        term_start_year: needed by calc_competence to pick the right
            EO-activity-rate era population (see _eo_activity_rate_component).

    Returns:
        Dict with keys score_public_mandate, score_effectiveness,
        score_competence, score_agency_alignment — any value may be None
        (that dimension doesn't apply to this president), never a
        hand-set fallback.
    """
    return {
        "score_public_mandate": calc_public_mandate(
            avg_approval=live_data.get("avg_approval"),
            approval_trend=live_data.get("approval_trend"),
            election_margin=live_data.get("election_margin"),
        ),
        "score_competence": calc_competence(
            eo_count=live_data.get("eo_count"),
            eo_court_success_pct=live_data.get("eo_court_success_pct"),
            cabinet_turnover_pct=live_data.get("cabinet_turnover_pct"),
            term_years=term_years,
            term_start_year=term_start_year,
        ),
        "score_effectiveness": calc_effectiveness(
            jobs_created_millions=live_data.get("jobs_created_millions"),
            gdp_growth_avg=live_data.get("gdp_growth_avg"),
            term_years=term_years,
            gdp_growth_adjusted=live_data.get("gdp_growth_adjusted"),
        ),
        "score_agency_alignment": calc_agency_alignment(
            rulemaking_count=live_data.get("rulemaking_count"),
            rulemaking_finalized_pct=live_data.get("rulemaking_finalized_pct"),
            term_years=term_years,
        ),
    }
