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
import statistics
from datetime import date

from app.config_definitions import PRESIDENT_SCORE_WEIGHTS
from app.pipeline.analyze.population_reference import PRESIDENT_REFERENCE
from app.pipeline.analyze.score_bounds import clamp

logger = logging.getLogger(__name__)


def _blend_live_components(components: list[dict]) -> dict:
    """Combine weighted live-data components into a score — live data
    only, never a hand-set fallback.

    Shared by _effectiveness_core / _public_mandate_core, which each gather their own `components`
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
    won an election) —
    scoring it neutral would still be presenting a number for something
    that isn't measurable even in principle. Callers (president_pipeline.
    py) skip writing a None score, leaving the DB column NULL; compute_
    president_overall_score renormalizes across whichever dimensions
    aren't NULL for that specific president.
    """
    if not components:
        return {
            "score": None, "components": [],
            "note": "Not applicable for this president: no data source exists even in principle, not merely unfetched.",
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
    "historicalLegacy": "score_historical_legacy",
}


_HISTORICAL_LEGACY_KEY = "historicalLegacy"

# The fixed-Legacy-weight tier (see compute_president_overall_score) only
# applies with at least this many mechanical dimensions present. Below
# this, a single mechanical data point isn't reliable enough to anchor
# the other half of a score on its own — verified concretely on Fillmore: his only
# present mechanical dimension, Effectiveness, is 100/100 purely from a
# ~9.6%/year GDP boom (Gold Rush-era antebellum expansion, not clearly
# attributable to his own governance), while C-SPAN's historians rate him
# 19/100 — one of the worst-regarded presidents. A fixed Legacy weight
# would let that single GDP number override his actual
# reputation entirely (Fillmore jumped to #8 in testing). Below this
# threshold, falls back to flat renormalization across whatever combo of
# Legacy + mechanical IS present (the pre-2026-07 behavior) — for
# Fillmore/Tyler/Arthur/Andrew Johnson specifically, that means Legacy
# carries two thirds (50 of 75 points of weight since President v7), diluting the single noisy mechanical signal
# rather than being swamped by it.
_MIN_MECHANICAL_DIMENSIONS_FOR_FIXED_LEGACY_WEIGHT = 2


def president_effective_weights(entity) -> dict[str, float]:
    """Each scored dimension's actual share of this president's overall
    score, {dimension key: weight} summing to 1, for the dimensions that
    have a value (an absent one has no entry). The scorecard shows these,
    so the percentages beside a president's scores are the ones their
    overall was computed with, not the nominal PRESIDENT_SCORE_WEIGHTS.

    Two-tier renormalization (2026-07): when at least
    _MIN_MECHANICAL_DIMENSIONS_FOR_FIXED_LEGACY_WEIGHT mechanical
    dimensions are present alongside Legacy, Legacy keeps its configured
    weight exactly and the mechanical dimensions share the rest in
    proportion to their configured weights. Otherwise (Legacy absent, as
    for a sitting president, or too few mechanical dimensions beside it)
    every present dimension is renormalized flat. See
    compute_president_overall_score."""
    legacy_weight = PRESIDENT_SCORE_WEIGHTS[_HISTORICAL_LEGACY_KEY]
    present = {
        key: weight for key, weight in PRESIDENT_SCORE_WEIGHTS.items()
        if getattr(entity, _PRESIDENT_SCORE_FIELD_MAP[key]) is not None
    }
    mechanical = {k: w for k, w in present.items() if k != _HISTORICAL_LEGACY_KEY}
    if _HISTORICAL_LEGACY_KEY in present and len(mechanical) >= _MIN_MECHANICAL_DIMENSIONS_FOR_FIXED_LEGACY_WEIGHT:
        mechanical_sum = sum(mechanical.values())
        return {
            _HISTORICAL_LEGACY_KEY: legacy_weight,
            **{k: (1 - legacy_weight) * w / mechanical_sum for k, w in mechanical.items()},
        }
    total = sum(present.values())
    return {k: w / total for k, w in present.items()} if total > 0 else {}


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
    president who never won an election; Effectiveness's jobs component
    before 1939), never a hand-set fallback.

    Two-tier renormalization (2026-07, replacing a flat single-tier
    renormalize-over-everything-present scheme): when at least
    _MIN_MECHANICAL_DIMENSIONS_FOR_FIXED_LEGACY_WEIGHT mechanical
    dimensions are present alongside Legacy, Legacy is held at its
    configured weight (50% since President v7) exactly — the mechanical
    dimensions renormalize only among THEMSELVES for the rest. The old flat
    scheme let Legacy's EFFECTIVE weight balloon well past its documented
    weight for any president missing a mechanical dimension (verified
    against the real 47-president dataset when Legacy was 35%: ~44.7% for
    the ~36 presidents then missing only the since-removed Agency
    Alignment); this fixes that silently-drifting weight rather than just
    disclosing it. Below the mechanical-dimension floor (see that
    constant's own comment — Fillmore's case), falls back to the old flat
    renormalization instead, since a single mechanical number isn't a
    reliable enough anchor for a fixed share. When Legacy itself is
    absent (any currently-serving or just-departed president), this has
    no effect either way: the mechanical dimensions renormalize to 100%
    of whatever's present, same as always. A president with zero
    computable dimensions (should not happen given the coverage of the
    fetchers wired into president_pipeline.py, but defensive) returns 0.0
    rather than raising.
    """
    weights = president_effective_weights(entity)
    return round(sum(
        w * getattr(entity, _PRESIDENT_SCORE_FIELD_MAP[k]) for k, w in weights.items()
    ), 2)


def dimensions_available(entity) -> int:
    """How many of the 3 possible dimensions actually have a score for
    this president (0-3) — surfaced to the reader so a composite built
    from partial data isn't presented with the same implied confidence as
    one built from all 3. A short-tenure or currently-serving president
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
# docstring); v5 = the z-score population statistics (approval, trend,
# election margin, C-SPAN) and the electoral->popular margin rescale are
# measured each run instead of hand-typed (compute_president_reference,
# presidential_elections._fit_scales); v6 = the approval trend is judged
# against what presidents starting at the same level went on to do
# (fit_trend_on_start), and the sitting president's approval against
# predecessors over the same elapsed time (window_reference); v7 = Agency
# Alignment removed (administrations since 1994 finalize 59.6-61.8% of their
# rulemakings, too little difference to score), Historical Legacy weighted
# 50%, and a presidency cut short compared over its own number of days like
# the sitting one (FULL_TERM_DAYS); v8 = a postwar term's GDP growth
# scored relative to 13 peer economies over the same years (peer_relative).
PRESIDENT_ALGORITHM_VERSION = "v8"


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
# good." Same "~1.5x stdev" shape as score_calculator.py's LES
# saturation (_LES_SATURATION_STDEVS), now smoothed rather than hard-clamped.
_ZSCORE_SATURATION_STDEV = 1.5


def _moved(points: float) -> str:
    """"rose 4.8 points" / "fell 12.0 points", for the detail sentences."""
    return f"{'rose' if points >= 0 else 'fell'} {abs(points):.1f} points"


def _ahead(points: float) -> str:
    """"1.9 points a year ahead" / "0.4 points a year behind" of the peers."""
    return f"{abs(points):.1f} points a year {'ahead' if points >= 0 else 'behind'}"


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


# GDP growth is compared within its data regime. Pre-1947 real-GDP
# estimates overstate business-cycle volatility by construction (Romer 1989,
# "The Prewar Business Cycle Reconsidered", JPE 97(1)), and measured term
# growth bears that out: presidencies before 1947 vary 2.5x as much as those
# after (SD 2.87 vs 1.13 points, Maddison Project data — docs/research/
# president-scores.md). One scale for both let the prewar spread pin 12% of
# those presidents at 0 or 100 while no postwar president reached either.
_GDP_REGIME_SPLIT_YEAR = 1947


def _gdp_reference_key(term_start_year: int | None) -> str:
    if term_start_year is not None and term_start_year < _GDP_REGIME_SPLIT_YEAR:
        return "gdp_growth_prewar"
    return "gdp_growth_postwar"


def jobs_per_attributed_year(jobs_created_millions: float, term_years: float) -> float:
    """Jobs per year over the window the jobs figure covers: from January of
    the term's second year (the same year-1 exclusion as GDP — see
    economic_data.calculate_jobs_created), so the divisor is the term minus
    one year, floored so a young in-progress term doesn't divide by ~zero.
    One definition for the score and the population it is compared with."""
    return jobs_created_millions / max(term_years - 1.0, 0.5)


def calc_effectiveness(
    jobs_created_millions: float | None,
    gdp_growth_avg: float | None,
    term_years: float,
    term_start_year: int | None = None,
    reference: dict | None = None,
    gdp_per_person: float | None = None,
    gdp_peer_median: float | None = None,
    gdp_relative: float | None = None,
) -> int | None:
    """Calculate effectiveness score from economic data only.

    See _effectiveness_core for the full component breakdown — this is a
    thin wrapper kept for existing callers/tests that expect a bare int.
    """
    return _effectiveness_core(
        jobs_created_millions, gdp_growth_avg, term_years, term_start_year, reference,
        gdp_per_person, gdp_peer_median, gdp_relative,
    )["score"]


def peer_relative(term_start_year: int | None, gdp_relative: float | None) -> float | None:
    """A postwar term's growth relative to the peer economies (catch-up set
    aside, fetch/peer_gdp.py); None before 1947 or when not measured."""
    return gdp_relative if peer_comparable(term_start_year) else None


def peer_comparable(term_start_year: int | None) -> bool:
    """Whether a term's growth is scored against peer economies: postwar
    terms, the ones the peer series covers (fetch/peer_gdp.py)."""
    return _gdp_reference_key(term_start_year) == "gdp_growth_postwar"


def _effectiveness_core(
    jobs_created_millions: float | None,
    gdp_growth_avg: float | None,
    term_years: float,
    term_start_year: int | None = None,
    reference: dict | None = None,
    gdp_per_person: float | None = None,
    gdp_peer_median: float | None = None,
    gdp_relative: float | None = None,
) -> dict:
    """Same math as calc_effectiveness, returning every intermediate value
    alongside the final score.

    Components, each scored against the presidential population measured
    every run (compute_president_reference), like Public Mandate:
      - GDP growth (60%). Since 1947: average annual real growth per
        person over the years after the term's first (an annual stand-in
        for Blinder & Watson 2016's attribution lag), minus the median of
        13 peer economies over the same years, with the part of that gap
        the peers' catch-up growth predicts set aside (fetch/peer_gdp.py),
        compared with other postwar presidencies' figure. Most of a term's
        growth is shared with those economies (68% of the variance since
        1961, docs/research/president-scores.md), so raw growth measured
        the world economy a president presided over; the difference is
        what was particular to the US (president v8). Before 1947 no peer
        series covers the terms, so it is average annual real growth,
        first year excluded and peak-relative after a contraction
        (historical_gdp.compute_term_gdp_growth), compared with other
        prewar presidencies (see _GDP_REGIME_SPLIT_YEAR). A postwar term
        whose peer figures haven't been fetched yet is scored the prewar
        way against postwar presidencies until they are.
      - Jobs created (40%): payroll jobs per attributed year, BLS 1939
        onward only. Absolute jobs rather than percent growth: across
        presidencies since 1945 the absolute rate shows no trend with era
        (Spearman 0.01) while the percent rate falls with the labor force's
        own slowing growth (-0.52), so percent would penalize recent
        presidents for demographics.

    Replaced in president v5: hand-set curves (GDP 25 + g/5 x 55 around a
    "post-WWII 3.2%"; jobs 30 + rate/3M x 50) — AGENTS.md §3a.

    What growth relative to peers still includes: anything that moved the
    US alone without a president causing it (a domestic financial crisis
    building before the term, a population shift). It removes the shocks
    the peers shared, not every shock.
    """
    components: list[dict] = []
    # The scorecard's sentence and scales, as numbers (None where not
    # measured): the president's values and the averages they're scored
    # against.
    facts: dict = {"jobsMillions": jobs_created_millions, "jobsPerYear": None, "jobsMean": None,
                   "gdpGrowth": gdp_growth_avg, "gdpMean": None, "gdpSince": None,
                   "gdpPerPerson": None, "gdpPeers": None, "gdpCatchUp": None,
                   "gdpRelative": None, "gdpRelativeMean": None}

    gdp_key = _gdp_reference_key(term_start_year)
    gdp_stat = _president_stat(reference, gdp_key)
    relative = peer_relative(term_start_year, gdp_relative)
    relative_stat = _president_stat(reference, "gdp_growth_relative")
    if relative is not None and relative_stat and gdp_per_person is not None and gdp_peer_median is not None:
        # The points of US-minus-peers growth the peers' catch-up accounted
        # for, set aside in `relative`.
        catch_up = round(gdp_per_person - gdp_peer_median - relative, 4)
        facts.update(gdpPerPerson=gdp_per_person, gdpPeers=gdp_peer_median, gdpCatchUp=catch_up,
                     gdpRelative=relative, gdpRelativeMean=relative_stat[0], gdpSince=True)
        components.append(_population_zscore_component(
            "GDP growth vs. peer economies", 0.60, relative, relative_stat[0], relative_stat[1],
            f"Grew {gdp_per_person:.1f}% a year per person leaving out the first year, against "
            f"{gdp_peer_median:.1f}% in 13 other wealthy countries. Allowing for their catching up "
            f"with US incomes: {_ahead(relative)}, against {_ahead(relative_stat[0])} for the typical president "
            f"since {_GDP_REGIME_SPLIT_YEAR}",
        ))
    elif gdp_growth_avg is not None and gdp_stat:
        era = "before" if gdp_key == "gdp_growth_prewar" else "since"
        facts.update(gdpMean=gdp_stat[0], gdpSince=gdp_key == "gdp_growth_postwar")
        components.append(_population_zscore_component(
            "GDP growth", 0.60, gdp_growth_avg, gdp_stat[0], gdp_stat[1],
            f"The economy grew {gdp_growth_avg:.1f}% a year leaving out the first year, against "
            f"{gdp_stat[0]:.1f}% for presidents {era} {_GDP_REGIME_SPLIT_YEAR}",
        ))

    jobs_stat = _president_stat(reference, "jobs_per_year")
    if jobs_created_millions is not None and term_years > 0 and jobs_stat:
        rate = jobs_per_attributed_year(jobs_created_millions, term_years)
        facts.update(jobsPerYear=round(rate, 3), jobsMean=jobs_stat[0])
        components.append(_population_zscore_component(
            "Jobs created", 0.40, rate, jobs_stat[0], jobs_stat[1],
            f"{jobs_created_millions:.1f} million jobs, {rate:.2f} million a year leaving out the "
            f"first year, against {jobs_stat[0]:.2f} million for presidents since 1939",
        ))

    return {**_blend_live_components(components), "facts": facts}


# Population statistics for the term-average-approval, approval-trend, and
# election-margin components below, and for Historical Legacy.
#
# MEASURED EACH PIPELINE RUN from the stored presidential population
# (compute_president_reference, president_pipeline.py) and persisted to
# /data/president_reference.json for the API's breakdowns, with the bundled
# app/data/president_reference.json as the pre-first-run fallback. They used
# to be hand-typed literals (approval 50.93/9.06, trend -13.72/14.65,
# election margin 8.39/7.51, C-SPAN 549.14/157.61) — AGENTS.md §3a.
#
# Why trend is scored against the population average rather than zero: the
# typical president's approval drops over a term (the "honeymoon fades"
# pattern in the approval literature), so comparing to zero would count
# normal, universal decline as a failure for nearly every president.
#
# Election margin is the pre-polling-era proxy: the average margin of victory
# across a president's own election win(s), with electoral margins and
# pre-1824 electoral shares rescaled onto the popular-margin scale (see
# presidential_elections.py). Presidents who never won a presidential
# election in their own right have neither approval nor margin data; Public
# Mandate is excluded for them (see compute_president_overall_score).

# Fewest presidents with a value before its population mean/stdev is
# trusted; below it the last persisted value is kept for that stat.
_MIN_PRESIDENT_REFERENCE_N = 10


def _mean_stdev(values: list[float], min_n: int = _MIN_PRESIDENT_REFERENCE_N) -> dict | None:
    if len(values) < min_n:
        return None
    return {
        "mean": round(statistics.mean(values), 4),
        "stdev": round(statistics.stdev(values), 4),
        "n": len(values),
    }


def compute_president_reference(presidents: list[dict]) -> dict:
    """Population mean/stdev for each z-scored presidential input, from
    stored per-president values: dicts with id, name, avg_approval,
    approval_trend, election_margin, historical_legacy_score, and (for
    Effectiveness) gdp_growth_avg, gdp_growth_relative, term_start_year,
    jobs_created_millions,
    term_years.

    Approval, trend and margin are counted per presidency (split terms have
    their own polling and elections). The C-SPAN score is counted once per
    PERSON — the survey rates Grover Cleveland once, and the cleveland-22 /
    cleveland-24 split would otherwise count his score twice. Stats with too
    few values are omitted; the caller keeps the last persisted ones."""
    legacy_by_person: dict[str, float] = {}
    for p in presidents:
        if p.get("historical_legacy_score") is not None:
            legacy_by_person[p.get("name") or p["id"]] = float(p["historical_legacy_score"])

    def values(field: str) -> list[float]:
        return [float(p[field]) for p in presidents if p.get(field) is not None]

    gdp = {"gdp_growth_prewar": [], "gdp_growth_postwar": [], "gdp_growth_relative": []}
    jobs: list[float] = []
    for p in presidents:
        relative = peer_relative(p.get("term_start_year"), p.get("gdp_growth_relative"))
        if relative is not None:
            gdp["gdp_growth_relative"].append(relative)
        if p.get("gdp_growth_avg") is not None:
            gdp[_gdp_reference_key(p.get("term_start_year"))].append(float(p["gdp_growth_avg"]))
        if p.get("jobs_created_millions") is not None and (p.get("term_years") or 0) > 0:
            jobs.append(jobs_per_attributed_year(float(p["jobs_created_millions"]), float(p["term_years"])))

    # The whole-term population is completed presidencies of at least a full
    # term. A sitting president's approval covers part of a term, and a
    # presidency cut short (Kennedy's 1,036 days, Ford's 895) had no time for
    # the decline later in a term; each is compared with predecessors over
    # its own number of days instead (window_reference).
    completed = [
        p for p in presidents
        if not p.get("is_current") and (p.get("term_days") or FULL_TERM_DAYS) >= FULL_TERM_DAYS
    ]

    def completed_values(field: str) -> list[float]:
        return [float(p[field]) for p in completed if p.get(field) is not None]

    stats = {
        "avg_approval": _mean_stdev(completed_values("avg_approval")),
        "approval_trend": _mean_stdev(completed_values("approval_trend")),
        "approval_trend_fit": fit_trend_on_start([
            (float(p["approval_start"]), float(p["approval_trend"]))
            for p in completed
            if p.get("approval_start") is not None and p.get("approval_trend") is not None
        ]),
        "election_margin": _mean_stdev(values("election_margin")),
        "historical_legacy": _mean_stdev(list(legacy_by_person.values())),
        **{key: _mean_stdev(vals) for key, vals in gdp.items()},
        "jobs_per_year": _mean_stdev(jobs),
    }
    return {k: v for k, v in stats.items() if v is not None}


# A full presidential term (U.S. Const. art. II, sec. 1): four years from
# noon on January 20, 1,461 days. A presidency shorter than one (a death or
# resignation, or a successor's remainder) is compared with predecessors over
# its own number of days, as the sitting president is, rather than with whole
# terms, and is left out of the whole-term population.
FULL_TERM_DAYS = 1461


def term_days(term_start: str | None, term_end: str | None) -> int | None:
    """Days from the term's first day to the day after its last (the next
    term's first), or None for a term that hasn't ended."""
    if not term_start or not term_end:
        return None
    return (date.fromisoformat(term_end) - date.fromisoformat(term_start)).days


def approval_window(series: list[tuple[date, float]], days: int | None = None) -> dict | None:
    """{"avg", "start", "trend"} over the polls in a term's first `days`
    days (every poll when None), from (poll date, approve %) pairs in date
    order: the average, the first quartile's average (where the term
    started) and the last quartile's average minus the first's. The same
    quartile trend the pipeline stores for a whole term."""
    if not series:
        return None
    first = series[0][0]
    values = [approve for day, approve in series if days is None or (day - first).days <= days]
    q = max(1, len(values) // 4)
    start = sum(values[:q]) / q
    return {
        "avg": statistics.mean(values),
        "start": start,
        "trend": sum(values[-q:]) / q - start,
    }


def fit_trend_on_start(points: list[tuple[float, float]]) -> dict | None:
    """Least-squares fit of approval trend on starting approval over
    (start, trend) pairs, with the residual standard deviation the trend
    is z-scored by. None below _MIN_PRESIDENT_REFERENCE_N presidencies.

    Approval is bounded, and a president who starts high has far further
    to fall: across the 14 completed polling-era presidencies, the starting
    level explains 45% of the trend's variance (r = -0.67, 2026-10-01,
    UCSB American Presidency Project polls). Scored against the population
    average instead, a president who began near the floor looked steady
    for having nowhere to go. Comparing each president with what the
    record predicts from their starting point is regression adjustment
    for that baseline, the standard treatment of a change score whose
    room to move depends on where it starts."""
    if len(points) < _MIN_PRESIDENT_REFERENCE_N:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in points) / sxx
    intercept = my - slope * mx
    residuals = [y - (intercept + slope * x) for x, y in points]
    resid_sd = math.sqrt(sum(r * r for r in residuals) / (len(points) - 2))
    return {
        "intercept": round(intercept, 4),
        "slope": round(slope, 4),
        "resid_sd": round(resid_sd, 4),
        "n": len(points),
    }


def window_reference(
    current: list[tuple[date, float]], completed: list[list[tuple[date, float]]],
) -> dict | None:
    """The comparison population for a presidency shorter than a full term
    (the sitting president, or one cut short by death or resignation):
    every other completed presidency over the same number of days from its
    first poll. A term's approval falls as it goes on (the completed
    presidencies averaged 57.5% over their first 598 days and 51.9% over
    their full terms), so comparing part of a term with whole terms
    flatters whoever served the shorter time. A presidency shorter than the
    window is left out rather than compared over less time."""
    if len(current) < 2:
        return None
    days = (current[-1][0] - current[0][0]).days
    windows = [
        approval_window(series, days)
        for series in completed
        if series and (series[-1][0] - series[0][0]).days >= days
    ]
    windows = [w for w in windows if w]
    block = {
        "days": days,
        "avg_approval": _mean_stdev([w["avg"] for w in windows]),
        "approval_trend_fit": fit_trend_on_start([(w["start"], w["trend"]) for w in windows]),
    }
    return {k: v for k, v in block.items() if v is not None} if len(block) > 1 else None


def _president_stat(reference: dict | None, key: str) -> tuple[float, float] | None:
    """(mean, stdev) for one input: this run's reference first, then the
    persisted/bundled one (population_reference.PRESIDENT_REFERENCE)."""
    stat = (reference or {}).get(key) or (PRESIDENT_REFERENCE.load().get("presidents") or {}).get(key)
    return (stat["mean"], stat["stdev"]) if stat else None


def calc_public_mandate(
    avg_approval: float | None,
    approval_trend: float | None,
    election_margin: float | None,
    reference: dict | None = None,
    approval_start: float | None = None,
    is_current: bool = False,
    president_id: str | None = None,
) -> int | None:
    """Calculate Public Mandate score from real data only — approval
    polling where it exists, election margin as the pre-polling-era
    historical proxy otherwise. None if neither exists for this
    president (see _public_mandate_core).

    See _public_mandate_core for the full component breakdown — this is a
    thin wrapper kept for the same reuse contract as calc_effectiveness.
    """
    return _public_mandate_core(
        avg_approval, approval_trend, election_margin, reference, approval_start, is_current, president_id,
    )["score"]


def _approval_reference(reference: dict | None, is_current: bool, president_id: str | None = None) -> dict:
    """The approval population a president is compared with: for a
    presidency shorter than a full term (the sitting one, or one cut short),
    predecessors over the same elapsed time (window_reference, measured per
    presidency into "term_windows"); otherwise completed full-term
    presidencies. Each key falls back to the persisted/bundled reference
    ("sitting_window" is the pre-v7 sitting president's block)."""
    persisted = PRESIDENT_REFERENCE.load().get("presidents") or {}
    windows = (reference or {}).get("term_windows") or persisted.get("term_windows") or {}
    if president_id and windows.get(president_id):
        return windows[president_id]
    if is_current:
        window = (reference or {}).get("sitting_window") or persisted.get("sitting_window")
        if window:
            return window
    return {
        key: (reference or {}).get(key) or persisted.get(key)
        for key in ("avg_approval", "approval_trend", "approval_trend_fit")
    }


def _public_mandate_core(
    avg_approval: float | None,
    approval_trend: float | None,
    election_margin: float | None,
    reference: dict | None = None,
    approval_start: float | None = None,
    is_current: bool = False,
    president_id: str | None = None,
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
    population = _approval_reference(reference, is_current, president_id)
    approval_stat = population.get("avg_approval")
    approval = (approval_stat["mean"], approval_stat["stdev"]) if approval_stat else None
    trend_stat = population.get("approval_trend")
    trend = (trend_stat["mean"], trend_stat["stdev"]) if trend_stat else None
    fit = population.get("approval_trend_fit")
    window_days = population.get("days")
    margin = _president_stat(reference, "election_margin")
    facts = {
        "approval": avg_approval, "approvalMean": approval[0] if approval else None,
        "approvalTrend": approval_trend, "trendMean": trend[0] if trend else None,
        "approvalStart": approval_start, "comparedOverDays": window_days,
        "electionMargin": election_margin, "marginMean": margin[0] if margin else None,
    }

    if avg_approval is not None and approval:
        over = (
            f"past presidents over their first {window_days} days" if window_days is not None
            else "past presidents over their full terms"
        )
        components.append(_population_zscore_component(
            "Average approval", 0.70, avg_approval, approval[0], approval[1],
            f"Averaged {avg_approval:.1f}% approval, against {approval[0]:.1f}% for {over}",
        ))
        if approval_trend is not None and fit and approval_start is not None:
            expected = fit["intercept"] + fit["slope"] * approval_start
            facts["trendExpected"] = round(expected, 1)
            components.append(_population_zscore_component(
                "Approval trend", 0.30, approval_trend, expected, fit["resid_sd"],
                f"Approval {_moved(approval_trend)} from {approval_start:.0f}% at the start; "
                f"presidents who started there typically {_moved(expected)}",
            ))
        elif approval_trend is not None and trend and window_days is None:
            # No fit yet (the persisted reference predates it): against the
            # population average, as before v6.
            components.append(_population_zscore_component(
                "Approval trend", 0.30, approval_trend, trend[0], trend[1],
                f"Approval {_moved(approval_trend)} over the term; presidents typically "
                f"{_moved(trend[0])}",
            ))
    elif avg_approval is None and election_margin is not None and margin:
        components.append(_population_zscore_component(
            "Election margin (pre-polling-era proxy)", 1.0, election_margin, margin[0], margin[1],
            f"Won by an average of {election_margin:.1f} points, against {margin[0]:.1f} for "
            "presidents generally (election results stand in for approval polls, which "
            "began with Truman)",
        ))

    return {**_blend_live_components(components), "facts": facts}


# Historical Legacy's population mean/stdev over C-SPAN point totals comes
# from the same measured reference (compute_president_reference, counted
# once per person) as Public Mandate's.


def calc_historical_legacy(
    historical_legacy_score: int | None, reference: dict | None = None,
) -> int | None:
    """Calculate Historical Legacy score from C-SPAN's Presidential
    Historians Survey only.

    See _historical_legacy_core for the full component breakdown — this
    is a thin wrapper kept for the same reuse contract as
    calc_effectiveness/calc_public_mandate.
    """
    return _historical_legacy_core(historical_legacy_score, reference)["score"]


def _historical_legacy_core(
    historical_legacy_score: int | None, reference: dict | None = None,
) -> dict:
    """Same math as calc_historical_legacy, returning every intermediate
    value alongside the final score.

    Covers what neither of this platform's other two president dimensions
    can: crisis leadership, moral authority, vision, and similar
    historical-consequence judgments that don't reduce to GDP growth or
    approval polling (added 2026-07 after review
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
    legacy = _president_stat(reference, "historical_legacy")
    facts = {"points": historical_legacy_score, "pointsMean": legacy[0] if legacy else None}
    if historical_legacy_score is not None and legacy:
        components.append(_population_zscore_component(
            "Historians' assessment", 1.0, historical_legacy_score, legacy[0], legacy[1],
            f"{historical_legacy_score} points in C-SPAN's 2021 survey of historians, against "
            f"{legacy[0]:.0f} for the average president",
        ))
    return {**_blend_live_components(components), "facts": facts}


def recalculate_president_scores(
    president_id: str, live_data: dict, term_years: float, reference: dict | None = None,
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
            gdp_growth_per_person, gdp_growth_peer_median, gdp_growth_relative,
            term_start_year,
            avg_approval, approval_trend, election_margin,
            historical_legacy_score — any subset may be present; each
            calc_* function handles its own missing inputs.

    Returns:
        Dict with keys score_public_mandate, score_effectiveness,
        score_historical_legacy — any value may
        be None (that dimension doesn't apply to this president), never
        a hand-set fallback.
    """
    return {
        "score_public_mandate": calc_public_mandate(
            avg_approval=live_data.get("avg_approval"),
            approval_trend=live_data.get("approval_trend"),
            election_margin=live_data.get("election_margin"),
            reference=reference,
            approval_start=live_data.get("approval_start"),
            is_current=bool(live_data.get("is_current")),
            president_id=president_id,
        ),
        "score_effectiveness": calc_effectiveness(
            jobs_created_millions=live_data.get("jobs_created_millions"),
            gdp_growth_avg=live_data.get("gdp_growth_avg"),
            term_years=term_years,
            term_start_year=live_data.get("term_start_year"),
            reference=reference,
            gdp_per_person=live_data.get("gdp_growth_per_person"),
            gdp_peer_median=live_data.get("gdp_growth_peer_median"),
            gdp_relative=live_data.get("gdp_growth_relative"),
        ),
        "score_historical_legacy": calc_historical_legacy(
            historical_legacy_score=live_data.get("historical_legacy_score"),
            reference=reference,
        ),
    }
