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

PRESIDENT_SCORE_WEIGHTS: dict[str, float] = {
    "independence": 0.15,
    "followThrough": 0.20,
    "publicMandate": 0.15,
    "effectiveness": 0.20,
    "competence": 0.15,
    "agencyAlignment": 0.15,
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
    "TRADE", "WELFARE", "PROCEDURAL",
]

VALID_INDUSTRIES = set(INDUSTRIES.keys())

# Legislative pipeline stages for the "bills currently moving through
# Congress" view. `order` drives the left-to-right position in the
# process-flow visualization. Codes are produced by
# app.pipeline.analyze.bill_stage.classify_bill_stage_from_actions.
BILL_STAGES: dict[str, dict] = {
    "INTRODUCED":       {"name": "Introduced",        "color": "#6b7280", "order": 1},
    "IN_COMMITTEE":     {"name": "In Committee",       "color": "#3b82f6", "order": 2},
    "PASSED_CHAMBER":   {"name": "Passed Chamber",     "color": "#8b5cf6", "order": 3},
    "IN_OTHER_CHAMBER": {"name": "In Other Chamber",   "color": "#f59e0b", "order": 4},
    "TO_PRESIDENT":     {"name": "To President",       "color": "#ec4899", "order": 5},
    "ENACTED":          {"name": "Enacted",            "color": "#00ff41", "order": 6},
    "VETOED":           {"name": "Vetoed",             "color": "#ef4444", "order": 7},
}

# Derived from BILL_STAGES's keys rather than listed separately, so a code
# (bill_stage.py, bill_service.py) can compare/assign `BillStage.ENACTED`
# instead of a bare string, with zero risk of the two lists drifting apart.
BillStage = StrEnum("BillStage", {stage: stage for stage in BILL_STAGES})
