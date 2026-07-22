"""Single source of truth for all dynamic enums, categories, and weights.

The frontend fetches these from GET /api/config so it never needs to
hardcode industry codes, category labels, score weights, or policy areas.
Backend modules import from here instead of defining their own copies.
"""

from enum import StrEnum


# Weight rationale (2026-07 composite-validity audit): fundingIndependence
# and fundingDiversity correlate at r=0.72 across the live Senate population
# — both driven by the same underlying funding-profile signal (grassroots
# small-dollar money scores well on both; PAC/large-donor-heavy fundraising
# scores poorly on both), not the "distinct dimension" each is meant to
# measure independently. A prior fix (2026-07, this dict's v6.0-era history)
# rebalanced the two to a combined 25% rather than let the redundant pair
# dominate (the audit's reference case: the sitting Senate Majority Leader
# ranked 2nd from last Senate-wide, driven almost entirely by this pair,
# despite above-median scores on the other three dimensions) — but a
# rebalance still measures the same signal twice under two labels. v6.5
# (2026-07) folds fundingDiversity into fundingIndependence outright: one
# dimension, weight = sum of the two prior weights (0.20 + 0.13 = 0.33).
# fundingDiversity's two signals (source breadth, industry concentration)
# are now components inside fundingIndependence's own score, at internal
# weights equal to each component's PRIOR contribution to the overall
# score divided by the merged weight — a linear renormalization, not a new
# judgment call — the continuous math is provably identical, so the only
# effect is where rounding happens (clamp() rounds each dimension to an
# int; previously FI and FD rounded independently before being weighted,
# now the merged dimension rounds once), bounded at roughly half a point
# either way (see score_calculator.py's v6.4->v6.5 changelog note for the
# math). score_funding_diversity keeps being computed and stored, same
# "kept independently visible, excluded from the weighted sum" pattern as
# promisePersistence below — it just no longer has its own SCORE_WEIGHTS
# entry or top-level scorecard panel.
#
# promisePersistence removed entirely (2026-07, ALGORITHM_VERSION v6.0):
# a live measurement across all 100 senators found 0 of 100 reached even
# "medium" confidence per calculate_confidence()'s own thresholds (mean
# 0.3 evaluable promises, 76% with zero) — real campaign promises are
# generic platform language that embedding-based matching against specific
# vote/bill text structurally can't bridge (see
# policy_alignment.compute_promise_vote_alignment's docstring, which
# documents three prior fix attempts that didn't resolve it, and
# ground_truth.py's MIN_STDEV comment documenting the same collapse). The
# underlying promise extraction/alignment pipeline and its "kept/broken/
# partial" display keep running unchanged — only the scoring weight is
# gone. Its 25% redistributed proportionally (each remaining weight ×4/3)
# across the three dimensions confirmed empirically distinct in the audit
# above (pairwise |r| < 0.31): independentVoting and legislativeEffectiveness
# absorb the largest shares, fundingIndependence a smaller share consistent
# with its correlated-pair status.
SCORE_WEIGHTS: dict[str, float] = {
    "fundingIndependence": 0.33,
    "independentVoting": 0.33,
    "legislativeEffectiveness": 0.34,
}

# Independence (15%) and Follow-Through (20%) removed entirely (2026-07):
# both were always 100% hand-set editorial values with no live formula and
# no realistic path to one — Independence's obvious source (OpenSecrets'
# revolving-door API) was discontinued in 2025, and Follow-Through would
# need the same platform-text-vs-action embedding match already tried and
# abandoned 4x for senators' Promise Persistence (config_definitions.py's
# v6.0 note, above). Same precedent as that removal: rather than keep
# presenting a hand-set number as a computed score, drop it. Their
# combined weight first redistributed proportionally across the
# remaining four (publicMandate 15->23%, effectiveness 20->31%,
# competence 15->23%, agencyAlignment 15->23%), then a fifth dimension —
# historicalLegacy — was added (2026-07) to cover what none of the other
# four can: crisis leadership, moral authority, and similar historical-
# consequence judgments that don't reduce to GDP growth, approval
# polling, EO rate, or rulemaking volume (see president_scorer.
# calc_historical_legacy's docstring — sourced from C-SPAN's Presidential
# Historians Survey, a real external expert-consensus survey, not a
# hand-set number).
#
# historicalLegacy's weight went through two revisions after the initial
# equal-fifths 20% (both 2026-07, both verified against the real
# 47-president dataset, not picked by eye):
#
# 1. Raised to 50%: the other four dimensions were never going to
#    reconstruct "historical greatness" on their own (a booming economy
#    or high EO-activity rate doesn't reliably track what historians
#    actually weigh), so at 20% four dimensions that don't individually
#    track greatness could outvote the one that does — Coolidge,
#    McKinley, and Harding all landed in the top 10 while Lincoln and
#    Eisenhower fell out of it.
# 2. Brought back down to 35%: at 50%, the Spearman rank correlation
#    between this platform's overall ranking and a pure 100%-
#    historicalLegacy ranking (i.e. just C-SPAN's own answer) measured
#    0.958 — the other four dimensions were contributing almost nothing
#    of their own. The four mechanical dimensions ALONE (0% weight)
#    correlate only 0.172 with C-SPAN — near-zero, meaning they measure
#    something genuinely different from historical-greatness judgment,
#    not a noisy/broken attempt at the same thing, so drowning them out
#    entirely wasn't defensible either. 35% is the point where the top
#    of the ranking is already recognizable (FDR, Washington, Lincoln,
#    Theodore Roosevelt, JFK, Eisenhower) while the four mechanical
#    dimensions still meaningfully move the rest of the ranking
#    (correlation to pure C-SPAN only 0.886, not 0.96) — a real,
#    disclosed compromise between two only loosely correlated kinds of
#    judgment, not a weight tuned until the result looked acceptable.
#    Coolidge and McKinley still edge into the bottom of the top 10 at
#    this weight; that's an honest, arguable disagreement with C-SPAN's
#    own ranking, not something further weight-tuning should paper over.
#
# This has no effect on how the currently-serving president is scored:
# historicalLegacy is null for anyone without a completed, C-SPAN-rated
# term, so compute_president_overall_score's renormalization already
# falls back to the other three dimensions entirely in that case.
#
# Competence (EO-activity-rate) removed entirely (2026-07), same
# "no defensible live signal" standard as Independence/Follow-Through
# above. A Coolidge-ranking review found EO-activity-rate — Competence's
# only ever-populated component (court-success-rate and cabinet-turnover
# have no fetch source, see president_scorer._competence_core's removed
# docstring) — has essentially zero relationship with real administrative
# competence: Spearman correlation of 0.097 (p=0.53, statistically no
# different from noise) against C-SPAN's own "Administrative Skill"
# category score across the same 44 historians-rated presidents. Coolidge
# and Harding make the gap concrete — nearly identical EO-rates (~216/yr
# each) but historians' actual administrative-skill judgment rates them
# 596 vs. 334 (of 1000), almost as far apart as two presidents get.
# Swapping in C-SPAN's Administrative Skill score directly (rather than
# just disclosing EO-rate's weakness) was considered and rejected: it
# isn't an independent data source, it's literally one of the ten
# categories C-SPAN itself sums into the same Final Score already driving
# historicalLegacy at 35% — folding it into a second, separate dimension
# would push this platform's true historian-derived weight toward ~51%
# (35% + Competence's share), undoing the exact over-reliance-on-C-SPAN
# problem the 50%->35% revision above was calibrated to avoid. Competence's
# 16.25% is redistributed evenly across the three remaining mechanical
# dimensions (21.67% each) rather than reopened as a fresh full weight
# search — verified this still hits the same qualitative target that
# justified 35% (Lincoln and Eisenhower both stay in the top 10; Coolidge
# drops from top-10 to #12, Harding to #26, McKinley to #17).
PRESIDENT_SCORE_WEIGHTS: dict[str, float] = {
    "publicMandate": 0.2167,
    "effectiveness": 0.2167,
    "agencyAlignment": 0.2167,
    "historicalLegacy": 0.35,
}

# Supreme Court impartiality-score weights. Single source of truth shared by
# the scorer (services/justice_service.py), the directory's overall-score calc
# (api/politicians.py), and the public /justices/weights endpoint — previously
# these were three independent copies that could silently drift.
JUSTICE_SCORE_WEIGHTS: dict[str, float] = {
    "consistency": 0.35,
    "independence": 0.30,
    "bipartisan_agreement": 0.15,
    "judicial_restraint": 0.20,
}

INDUSTRIES: dict[str, dict] = {
    "PHARMA":          {"name": "Pharmaceuticals",          "color": "#ff4444"},
    "INSURANCE":       {"name": "Insurance",                "color": "#ff6600"},
    "OIL_GAS":         {"name": "Oil & Gas",                "color": "#8b4513"},
    "DEFENSE":         {"name": "Defense",                  "color": "#556b2f"},
    "FINANCE":         {"name": "Finance / Wall St.",       "color": "#ffd700"},
    "REAL_ESTATE":     {"name": "Real Estate",              "color": "#daa520"},
    "TECH":            {"name": "Technology",               "color": "#00bfff"},
    "TELECOM":         {"name": "Telecom",                  "color": "#1e90ff"},
    "AGRIBUSINESS":    {"name": "Agribusiness",             "color": "#adff2f"},
    "ENERGY":          {"name": "Energy",                   "color": "#ff8c00"},
    "CONSTRUCTION":    {"name": "Construction",             "color": "#cd853f"},
    "TRANSPORT":       {"name": "Transportation",           "color": "#708090"},
    "LAWYERS":         {"name": "Lawyers",                  "color": "#9370db"},
    "LOBBYISTS":       {"name": "Lobbyists",                "color": "#dc143c"},
    "GAMBLING":        {"name": "Gambling",                 "color": "#ff1493"},
    "GUNS":            {"name": "Firearms",                 "color": "#b22222"},
    "TOBACCO":         {"name": "Tobacco",                  "color": "#a0522d"},
    "CRYPTO":          {"name": "Crypto",                   "color": "#f7931a"},
    "PRIVATE_PRISON":  {"name": "Private Prisons",          "color": "#696969"},
    "POLITICAL":       {"name": "Party / Political PACs",   "color": "#cc44ff"},
    "LABOR_UNIONS":    {"name": "Labor Unions",             "color": "#e74c3c"},
    "EDUCATION":       {"name": "Education",                "color": "#3498db"},
    "MEDIA":           {"name": "Media / Entertainment",    "color": "#e67e22"},
    "RETAIL":          {"name": "Retail / Consumer Goods",  "color": "#2ecc71"},
    "MANUFACTURING":   {"name": "Manufacturing",            "color": "#95a5a6"},
    "HEALTHCARE":      {"name": "Healthcare / Hospitals",   "color": "#ff6b81"},
    "OTHER":           {"name": "Other (Unclassified)",     "color": "#444444"},
    "SMALL_DONORS":    {"name": "Small Donors (<$200)",     "color": "#00ff41"},
    "LARGE_INDIVIDUAL":{"name": "Large Individual Donors",  "color": "#39ff14"},
    "UNCLASSIFIED":    {"name": "Other Sources",            "color": "#666666"},
}

PLATFORM_CATEGORIES: dict[str, str] = {
    "healthcare":      "HEALTHCARE",
    "economy":         "ECONOMY",
    "defense":         "DEFENSE",
    "environment":     "ENVIRONMENT",
    "immigration":     "IMMIGRATION",
    "education":       "EDUCATION",
    "labor":           "LABOR",
    "justice":         "JUSTICE",
    "guns":            "GUNS",
    "tech":            "TECH",
    "finance":         "FINANCE",
    "energy":          "ENERGY",
    "trade":           "TRADE",
    "welfare":         "WELFARE",
    "infrastructure":  "INFRASTRUCTURE",
    "civil_rights":    "CIVIL RIGHTS",
    "foreign_policy":  "FOREIGN POLICY",
    "other":           "OTHER",
}

POLICY_AREAS: list[str] = [
    "LABOR", "DEFENSE", "FOREIGN_POLICY", "GUNS", "HEALTHCARE", "ENVIRONMENT", "TAXES",
    "IMMIGRATION", "EDUCATION", "FINANCIAL", "ENERGY", "TECH", "JUSTICE",
    "TRADE", "WELFARE", "ABORTION", "PROCEDURAL",
]

VALID_INDUSTRIES = set(INDUSTRIES.keys())

# Legislative pipeline stages for the "bills currently moving through
# Congress" view. `order` drives the left-to-right position in the
# process-flow visualization. Codes are produced by
# app.pipeline.analyze.bill_stage.classify_bill_stage_from_actions.
BILL_STAGES: dict[str, dict] = {
    "INTRODUCED":       {"name": "Introduced",        "color": "#6b7280", "order": 1},
    # 2026-07 fix: split out from IN_COMMITTEE. Automatic referral is the
    # default first step for virtually every bill (see bill_stage.py's
    # module docstring) — it isn't evidence anyone did anything with it,
    # and collapsing it into the same bucket as a genuine hearing/markup
    # made "in committee" (and, downstream, Legislative Effectiveness's
    # stage-2 credit) mean almost nothing: live audit found one senator's
    # sponsored-bills summary reading "135 bills, 123 advancing" purely
    # because nearly all of them simply hadn't died yet.
    "REFERRED":         {"name": "Referred to Committee", "color": "#60a5fa", "order": 2},
    "IN_COMMITTEE":     {"name": "In Committee",       "color": "#3b82f6", "order": 3},
    "PASSED_CHAMBER":   {"name": "Passed Chamber",     "color": "#8b5cf6", "order": 4},
    "IN_OTHER_CHAMBER": {"name": "In Other Chamber",   "color": "#f59e0b", "order": 5},
    "TO_PRESIDENT":     {"name": "To President",       "color": "#ec4899", "order": 6},
    "ENACTED":          {"name": "Enacted",            "color": "#00ff41", "order": 7},
    "VETOED":           {"name": "Vetoed",             "color": "#ef4444", "order": 8},
}

# Derived from BILL_STAGES's keys rather than listed separately, so a code
# (bill_stage.py, bill_service.py) can compare/assign `BillStage.ENACTED`
# instead of a bare string, with zero risk of the two lists drifting apart.
BillStage = StrEnum("BillStage", {stage: stage for stage in BILL_STAGES})
