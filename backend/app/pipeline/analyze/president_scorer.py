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

Competence (EO-activity-rate) was removed entirely (2026-07) — see
config_definitions.py's PRESIDENT_SCORE_WEIGHTS comment for the full
account. In short: EO-activity-rate, the only component ever populated
(court-success rate and cabinet-turnover rate never had a fetch source),
measured Spearman 0.097 (p=0.53) against C-SPAN's own "Administrative
Skill" category — statistically indistinguishable from no relationship
to the real construct it claimed to represent.

Metrics that can be dynamically computed:
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
"""

import logging
import math

logger = logging.getLogger(__name__)


def clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def _blend_live_components(components: list[dict]) -> dict:
    """Combine weighted live-data components into a score — live data
    only, never a hand-set fallback.

    Shared by _effectiveness_core / _agency_alignment_core /
    _public_mandate_core, which each gather their own `components`
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
    "agencyAlignment": "score_agency_alignment",
    "historicalLegacy": "score_historical_legacy",
}


_HISTORICAL_LEGACY_KEY = "historicalLegacy"

# The fixed-Legacy-weight tier (see compute_president_overall_score) only
# applies with at least this many mechanical dimensions present. Below
# this, a single mechanical data point isn't reliable enough to anchor
# 65% of a score on its own — verified concretely on Fillmore: his only
# present mechanical dimension, Effectiveness, is 100/100 purely from a
# ~9.6%/year GDP boom (Gold Rush-era antebellum expansion, not clearly
# attributable to his own governance), while C-SPAN's historians rate him
# 19/100 — one of the worst-regarded presidents. A flat "Legacy always
# 35%" rule would let that single GDP number override his actual
# reputation entirely (Fillmore jumped to #8 in testing). Below this
# threshold, falls back to flat renormalization across whatever combo of
# Legacy + mechanical IS present (the pre-2026-07 behavior) — for
# Fillmore/Tyler/Arthur/Andrew Johnson specifically, that means Legacy
# effectively carries ~62%, diluting the single noisy mechanical signal
# rather than being swamped by it.
_MIN_MECHANICAL_DIMENSIONS_FOR_FIXED_LEGACY_WEIGHT = 2


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
    before this platform's real government sources have machine-readable
    rulemaking data), never a hand-set fallback.

    Two-tier renormalization (2026-07, replacing a flat single-tier
    renormalize-over-everything-present scheme): when at least
    _MIN_MECHANICAL_DIMENSIONS_FOR_FIXED_LEGACY_WEIGHT mechanical
    dimensions are present alongside Legacy, Legacy is held at its
    configured weight (35%) exactly — the mechanical dimensions
    renormalize only among THEMSELVES for the remaining 65%. The old flat
    scheme let Legacy's EFFECTIVE weight balloon well past its documented
    35% for any president missing a mechanical dimension — verified
    against the real 47-president dataset: ~44.7% for the ~36 presidents
    missing only Agency Alignment (everyone before Clinton). 35% was
    never actually the operative number for most presidents under the old
    scheme; this fixes that silently-drifting weight rather than just
    disclosing it. Below the mechanical-dimension floor (see that
    constant's own comment — Fillmore's case), falls back to the old flat
    renormalization instead, since a single mechanical number isn't a
    reliable enough anchor for a fixed 65% share. When Legacy itself is
    absent (any currently-serving or just-departed president), this has
    no effect either way: the mechanical dimensions renormalize to 100%
    of whatever's present, same as always. A president with zero
    computable dimensions (should not happen given the coverage of the
    fetchers wired into president_pipeline.py, but defensive) returns 0.0
    rather than raising.
    """
    from app.config_definitions import PRESIDENT_SCORE_WEIGHTS

    legacy_weight = PRESIDENT_SCORE_WEIGHTS[_HISTORICAL_LEGACY_KEY]
    legacy_score = getattr(entity, _PRESIDENT_SCORE_FIELD_MAP[_HISTORICAL_LEGACY_KEY])

    mechanical_present = [
        (weight, getattr(entity, _PRESIDENT_SCORE_FIELD_MAP[key]))
        for key, weight in PRESIDENT_SCORE_WEIGHTS.items()
        if key != _HISTORICAL_LEGACY_KEY
        and getattr(entity, _PRESIDENT_SCORE_FIELD_MAP[key]) is not None
    ]

    if legacy_score is not None and len(mechanical_present) >= _MIN_MECHANICAL_DIMENSIONS_FOR_FIXED_LEGACY_WEIGHT:
        mechanical_weight_sum = sum(w for w, _ in mechanical_present)
        mechanical_component = sum(w * score for w, score in mechanical_present) / mechanical_weight_sum
        return round(legacy_weight * legacy_score + (1 - legacy_weight) * mechanical_component, 2)

    # Flat renormalization fallback: Legacy absent, or fewer than
    # _MIN_MECHANICAL_DIMENSIONS_FOR_FIXED_LEGACY_WEIGHT mechanical
    # dimensions present alongside it.
    present = list(mechanical_present)
    if legacy_score is not None:
        present.append((legacy_weight, legacy_score))
    total_weight = sum(w for w, _ in present)
    if total_weight <= 0:
        return 0.0
    return round(sum(w * score for w, score in present) / total_weight, 2)


def dimensions_available(entity) -> int:
    """How many of the 4 possible dimensions actually have a score for
    this president (0-4) — surfaced to the reader so a composite built
    from partial data isn't presented with the same implied confidence as
    one built from all 4. A short-tenure or currently-serving president
    (missing Effectiveness's GDP data, or Historical Legacy's not-yet-run
    C-SPAN survey) has a real, disclosed reason for a lower count, never
    padded to look complete.
    """
    return sum(
        1 for field in _PRESIDENT_SCORE_FIELD_MAP.values()
        if getattr(entity, field) is not None
    )


# Presidential scoring formula version, same purpose as score_calculator.
# ALGORITHM_VERSION for senators/reps (tags each ScoreSnapshot so trend
# charts can mark a formula change instead of reading it as a behavior
# change) — introduced here (2026-07) rather than retroactively for the
# implicit original 6-dimension formula, since presidents only start
# getting snapshotted at this version. v2 = the 4-dimension formula after
# Independence/Follow-Through were removed and their weight redistributed;
# v3 = Competence also removed (see PRESIDENT_SCORE_WEIGHTS's own comment
# for both); v4 = two-tier renormalization holds Historical Legacy at its
# configured 35% instead of letting it silently float up to ~45%/~62% for
# presidents missing mechanical data (see compute_president_overall_score's
# docstring).
PRESIDENT_ALGORITHM_VERSION = "v4"


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

    No historical proxy exists for this dimension before Clinton-42
    (2026-07: checked both real candidate sources rather than assumed —
    federalregister.gov's API returns zero results for any pre-1994
    president, e.g. Reagan; govinfo.gov's own structured/bulk Federal
    Register data starts at year 2000, not earlier). This is a
    digitization wall, not the conceptual absence this docstring used to
    claim ("before the Federal Register Act of 1936") — notice-and-comment
    rulemaking was a real, functioning practice well before 1994, this
    platform just has no machine-readable record of it that far back.
    Federal Register issues before ~2000 exist only as scanned PDF page
    images with no structured document-type/agency tagging; reconstructing
    rulemaking counts from that would mean OCR'ing and classifying decades
    of raw scanned text, the same category of fragile, unreliable pipeline
    already rejected for Follow-Through and Competence's court-success-rate
    (see PRESIDENT_SCORE_WEIGHTS's comment in config_definitions.py) — not
    attempted here for the same reason. Agency Alignment is fully excluded
    (not defaulted) for every president outside this real coverage window,
    via compute_president_overall_score's per-president renormalization.
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
    thin wrapper kept for the same reuse contract as calc_effectiveness/
    calc_agency_alignment.
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


# Population stats for C-SPAN's 2021 Presidential Historians Survey point
# totals, computed 2026-07 from the real fetched data across all 44 rated
# presidents (Grover Cleveland's single real score counted once, not
# double-counted across this platform's cleveland-22/cleveland-24 id
# split) via app.pipeline.fetch.cspan_historians_survey.
_HISTORICAL_LEGACY_MEAN = 549.14
_HISTORICAL_LEGACY_STDEV = 157.61


def calc_historical_legacy(historical_legacy_score: int | None) -> int | None:
    """Calculate Historical Legacy score from C-SPAN's Presidential
    Historians Survey only.

    See _historical_legacy_core for the full component breakdown — this
    is a thin wrapper kept for the same reuse contract as
    calc_effectiveness/calc_agency_alignment/calc_public_mandate.
    """
    return _historical_legacy_core(historical_legacy_score)["score"]


def _historical_legacy_core(historical_legacy_score: int | None) -> dict:
    """Same math as calc_historical_legacy, returning every intermediate
    value alongside the final score.

    Covers what none of this platform's other three president dimensions
    can: crisis leadership, moral authority, vision, and similar
    historical-consequence judgments that don't reduce to GDP growth,
    approval polling, or rulemaking volume (added 2026-07 after review
    found presidents like Lincoln landing in the
    bottom half of the overall ranking — every individual number was
    defensible on its own terms, but nothing in the formula could credit
    "preserved the Union, ended slavery" at all).

    Sourced from C-SPAN's Presidential Historians Survey — ~142
    professional historians in the most recent (2021) cycle, scored
    across ten categories and aggregated into one point total. This is
    categorically different from the hand-set Independence/Follow-
    Through values removed elsewhere in this rewrite: a real, external,
    periodically-run survey with a documented methodology, not a single
    number invented for this platform — the same "trust a well-
    documented external institution" category as citing BLS or Federal
    Register data, just survey-based rather than administrative-record-
    based. See app.pipeline.fetch.cspan_historians_survey for the full
    account, including why the 2025 cycle doesn't exist (C-SPAN
    explicitly postponed it) and why every currently-serving or just-
    departed president has no score here at all — genuinely unrated by
    the survey's own cadence, not a fetch gap this pipeline could close.
    """
    components: list[dict] = []
    if historical_legacy_score is not None:
        components.append(_population_zscore_component(
            "Historians' assessment", 1.0, historical_legacy_score,
            _HISTORICAL_LEGACY_MEAN, _HISTORICAL_LEGACY_STDEV,
            f"{historical_legacy_score} points in C-SPAN's 2021 Presidential Historians Survey "
            f"vs. population mean {_HISTORICAL_LEGACY_MEAN:.0f}",
        ))
    return _blend_live_components(components)


def recalculate_president_scores(
    president_id: str, live_data: dict, term_years: float,
) -> dict:
    """Recalculate every dimension from live data only, for one president.

    2026-07: this used to bundle "the DYNAMIC_PRESIDENTS cohort's full
    recalculation" specifically, blending with a seed_scores fallback for
    anything unfetched. Now that GDP (historical_gdp.py) covers the full
    presidency rather than just BLS's 1939/1947-plus window, every
    president goes through this same function — president_pipeline.py
    calls it once per president in a single unified loop rather than
    splitting DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_PRESIDENTS into separate
    partial-recalculation branches.

    Args:
        president_id: e.g. "obama-44"
        live_data: Dict with keys jobs_created_millions, gdp_growth_avg,
            gdp_growth_adjusted, rulemaking_count, rulemaking_finalized_pct,
            avg_approval, approval_trend, election_margin,
            historical_legacy_score — any subset may be present; each
            calc_* function handles its own missing inputs.

    Returns:
        Dict with keys score_public_mandate, score_effectiveness,
        score_agency_alignment, score_historical_legacy — any value may
        be None (that dimension doesn't apply to this president), never
        a hand-set fallback.
    """
    return {
        "score_public_mandate": calc_public_mandate(
            avg_approval=live_data.get("avg_approval"),
            approval_trend=live_data.get("approval_trend"),
            election_margin=live_data.get("election_margin"),
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
        "score_historical_legacy": calc_historical_legacy(
            historical_legacy_score=live_data.get("historical_legacy_score"),
        ),
    }
