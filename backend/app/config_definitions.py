"""Single source of truth for all dynamic enums, categories, and weights.

The frontend fetches these from GET /api/config so it never needs to
hardcode industry codes, category labels, score weights, or policy areas.
Backend modules import from here instead of defining their own copies.
"""

from enum import StrEnum


# Three weighted dimensions, measured distinct from one another (pairwise
# |r| < 0.31, 2026-07 audit). Promise Persistence (v6.0) and Funding
# Diversity (v6.5) still compute, store and display, but carry no weight;
# Funding Diversity's industry concentration is a component of
# fundingIndependence. Why each weight is what it is, with the measurements:
# docs/methodology/weights.md.
SCORE_WEIGHTS: dict[str, float] = {
    "fundingIndependence": 0.33,
    "independentVoting": 0.33,
    "legislativeEffectiveness": 0.34,
}

# Historical Legacy (C-SPAN Presidential Historians Survey) at 35%; the three
# mechanical dimensions split the rest. compute_president_overall_score holds
# Historical Legacy at exactly 35% whenever two or more mechanical dimensions
# are present. Independence, Follow-Through and Competence were removed for
# having no defensible live signal. The measurements behind 35% and each
# removal: docs/methodology/weights.md.
PRESIDENT_SCORE_WEIGHTS: dict[str, float] = {
    # The three mechanical dimensions split 0.65 three ways (0.65/3 =
    # 0.21666...) — 0.2167 is that value rounded to 4 places, so the
    # total is 1.0001, not exactly 1.0. Harmless: every consumer
    # (compute_president_overall_score, _blend_live_components)
    # renormalizes over whatever's actually present rather than assuming
    # the nominal weights already sum to 1.
    "publicMandate": 0.2167,
    "effectiveness": 0.2167,
    "agencyAlignment": 0.2167,
    "historicalLegacy": 0.35,
}

# Supreme Court impartiality-score weights. Single source of truth shared by
# the scorer (services/justice_service.py), the directory's overall-score calc
# (api/politicians.py), and the public /justices/weights endpoint — previously
# these were three independent copies that could silently drift.
#
# v6.13: Judicial Restraint (0.20) and Bipartisan Agreement (0.15) removed —
# see justice_analyzer's module docstring. Bipartisan Agreement measured
# Independence's construct (Spearman 0.86), so its weight joins
# Independence's (0.30 + 0.15); the two remaining weights are then
# renormalized over 0.80 — no new weighting judgment.
JUSTICE_SCORE_WEIGHTS: dict[str, float] = {
    "consistency": 0.35 / 0.80,
    "independence": 0.45 / 0.80,
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


# ── Explore search ranking ───────────────────────────────────────
#
# The explore index is queried by two retrieval channels whose scores are
# not comparable to each other — cosine distance from a sentence encoder
# and Okapi BM25 from an inverted index live on unrelated scales, and the
# usual fix (min-max normalise each, then add) makes the blend depend on
# whatever the best and worst scores happened to be for that one query.
# Reciprocal rank fusion (Cormack, Clarke & Büttcher, SIGIR 2009) instead
# throws the scores away and fuses the *rankings*:
#
#     score(d) = Σ_r  weight_r / (K + rank_r(d))
#
# A ranker that didn't return a document contributes nothing for it. That
# same property is what lets the two query-independent priors sit in the
# sum as additional voters: freshness ranks every candidate, while
# authority ranks only documents the corpus actually cites, so a document
# with no way to earn a citation is passed over rather than penalised
# (see pipeline/analyze/document_authority.py).
#
# TWO published constants live here, with their citations. Everything else
# about this ranking is generated data, loaded from
# app/data/explore_ranking.json and produced by
# scripts/calibrate_explore_ranking.py against the live corpus — the same
# pattern as district_pvi.json and state_population.json (principle 3a).
# Do not hand-edit the JSON; re-run the script.

# K = 60 is the constant Cormack et al. published and the de-facto default.
# It flattens the contribution curve so no single ranker's top hit can run
# away with the result. A property of the algorithm, not of this corpus.
EXPLORE_RRF_K: int = 60

# The two retrieval channels carry equal weight, which is unweighted RRF
# exactly as published — there is no prior reason to trust the encoder over
# the inverted index or the reverse, and the calibration measures their
# disagreement rather than presuming one is better.
EXPLORE_RETRIEVAL_WEIGHT: float = 1.0

# Everything else about this ranking — BM25F field weights, the two prior
# weights, candidate pool depth, the diversity cap, fingerprint lengths,
# snippet width — is generated data, not a constant. It is measured
# against the live corpus by pipeline/calibrate_ranking.py on every
# explore pipeline run and read through pipeline/explore_ranking.py.
# There is deliberately nothing to hand-edit here.
