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
    "constituentAlignment": 0.33,
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

# Supreme Court score weights: the single source shared by the scorer
# (services/justice_service.py), the directory's overall score
# (api/politicians.py) and the public /justices/weights endpoint.
#
# Justice v2: loyalty to the appointing president
# (pipeline/analyze/justice_loyalty.py), the one measure. Consistency and
# Independence, the two bloc-agreement measures before it, ranked justices
# by their distance from the Court's median (docs/research/justice-scores.md).
JUSTICE_SCORE_WEIGHTS: dict[str, float] = {
    "loyalty": 1.0,
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

# Asset-holding categories for the scorecard's holdings breakdown (annual
# financial disclosures). Each chamber's own asset-type vocabulary is mapped
# onto these in pipeline/fetch/fd_common.py. The frontend never hardcodes
# them: every holdings response carries each category's label and color.
# The breakdown lists categories largest first; this order is the tie-break
# and the order the colors were validated in. Display-only — exempt from the
# analysis-code fingerprint (senate_pipeline._DISPLAY_ONLY_NAMES). Colors are a fixed categorical order validated for
# colorblind separation and >= 3:1 contrast against the site's dark surface;
# OTHER is deliberately a neutral gray, not a ninth hue. Color follows the
# category, so a member without some category never repaints the rest.
HOLDING_CATEGORIES: dict[str, dict[str, str]] = {
    "STOCKS":      {"label": "Stocks",                "color": "#3987e5"},
    "FUNDS":       {"label": "Mutual funds & ETFs",   "color": "#d95926"},
    "BONDS":       {"label": "Bonds & Treasuries",    "color": "#199e70"},
    "CASH":        {"label": "Bank & cash",           "color": "#c98500"},
    "RETIREMENT":  {"label": "Retirement & pensions", "color": "#d55181"},
    "REAL_ESTATE": {"label": "Real estate",           "color": "#008300"},
    "BUSINESS":    {"label": "Business interests",    "color": "#9085e9"},
    "CRYPTO":      {"label": "Crypto",                "color": "#e66767"},
    "OTHER":       {"label": "Other",                 "color": "#8a857d"},
}

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
    # 2026-09: a bill reported out of committee and under floor debate
    # (motion to proceed, cloture, "considered by Senate") had no stage of
    # its own, so it read "In Committee" through a week of floor votes
    # (S. 4668, 119th Congress). Volden & Wiseman call this step "action
    # beyond committee".
    # v6.17: reported out of committee (or discharged, or placed on a
    # calendar) is V&W's "action beyond committee", apart from the hearings
    # and markups IN_COMMITTEE now means ("action in committee").
    "REPORTED":         {"name": "Reported by Committee", "color": "#e5e7eb", "order": 4},
    "ON_FLOOR":         {"name": "On the Floor",       "color": "#ff8a3d", "order": 5},
    "PASSED_CHAMBER":   {"name": "Passed Chamber",     "color": "#8b5cf6", "order": 6},
    "IN_OTHER_CHAMBER": {"name": "In Other Chamber",   "color": "#f59e0b", "order": 7},
    "TO_PRESIDENT":     {"name": "To President",       "color": "#ec4899", "order": 8},
    "ENACTED":          {"name": "Enacted",            "color": "#00ff41", "order": 9},
    "VETOED":           {"name": "Vetoed",             "color": "#ef4444", "order": 10},
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


# ── Constituent Alignment reference definition ──────────────────────────────
# Kept here, not in score_calculator, because population_reference needs them
# to stamp CONSTITUENT_REFERENCE at construction (it can't import the scorer).

# Fewest party-labeled votes Constituent Alignment reads a break rate from
# (party_break_rate); below it the vote part is the party's measured typical
# score (a neutral 50 without one).
CONSTITUENT_MIN_VOTES = 3

# Party-labeled votes at which Constituent Alignment's data sufficiency is
# graded "high" (calculate_confidence) and its vote score stops shrinking
# toward the party's typical score (50 without one). A volume count, the
# same for every member.
CONSTITUENT_FULL_CONFIDENCE_VOTES = 20

# The quantile of |residual| (standard deviations per vote from the seat's
# expected break rate) that sets Constituent Alignment's vote-score scale,
# per party: the most out-of-pattern tenth of a party's members in a chamber,
# measured on both sides of the expectation, sits at or past it.
SATURATION_QUANTILE = 0.9

# What a Constituent Alignment reference is measured on: the unweighted
# break rate (party_break_rate; since v6.20 over the whole Congress, each
# measure once, breaks toward the other party only) over records with at least
# CONSTITUENT_FULL_CONFIDENCE_VOTES party-labeled votes, its expectation a
# fractional-logit fit, its scale per party the SATURATION_QUANTILE of the
# Pearson residual per vote (written exactly, so any change to either constant
# changes the stamp). CONSTITUENT_REFERENCE accepts only stored entries
# carrying it, so one measured under another rule — v6.15's least-squares
# expectation and percentage-point scale, v6.13's content-weighted rate,
# another threshold or quantile — is not scored against.
CONSTITUENT_REFERENCE_STATISTIC = (
    f"congress-centerward-per-measure-break-rate/logit/party-residual/n>={CONSTITUENT_FULL_CONFIDENCE_VOTES}/q={SATURATION_QUANTILE!r}"
)


# ── The homepage's recent-issues feed ─────────────────────────────
# Its largest page, and how many rows per slot it reads before duplicates
# are set aside (a near-identical cluster can run 3-4 rows deep: the
# beef-tariff incident). The hourly refresh marks duplicates over exactly
# this pool (action_center.mark_recent_duplicates) and the API reads it
# (app/api/action.py), so the two share these numbers.
RECENT_FEED_MAX_LIMIT = 30
RECENT_FEED_POOL_MULTIPLIER = 3

