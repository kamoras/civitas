"""
Embedding-based political data alignment engine.

Replaces hardcoded _INDUSTRY_POLICY_MAP with deterministic cosine similarity
computation. All classification decisions are made by comparing embeddings —
the LLM is only used downstream for human-readable narrative summaries of
these pre-computed alignments.

Key functions:
  - industry_policy_similarity: replaces _INDUSTRY_POLICY_MAP
  - donor_vote_connection: replaces LLM lobbying match detection

Design principles:
  - Deterministic: same input always produces same output
  - Non-biased: no political opinion encoded, only semantic similarity
  - Auditable: similarity scores are transparent numbers, not black-box labels

Campaign-promise tracking (promise extraction + promise<->vote alignment)
was removed entirely (2026-07): a live measurement found the underlying
matching — regardless of whether promises came from LLM extraction or
deterministic sponsored-bill derivation — routinely produced wrong or
nonsensical verdicts (e.g. a senator's signature policy position marked
"broken" against an unrelated procedural vote; scraped website navigation
text mistaken for a stated promise). This was the fourth and final attempt
after v5, v5.1/v5.3, v5.4, and v5.10 — see config_definitions.SCORE_WEIGHTS'
docstring for the scoring-side history of the same conclusion.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

_embedding_cache: dict[str, np.ndarray] = {}


def _embed_batch(texts: list[str]) -> np.ndarray:
    """Embed multiple texts efficiently, reusing cached embeddings.

    The same texts recur heavily within a pipeline run — every promise
    of every member is scored against the same floor votes — so only
    cache misses are encoded (in one batch), then stored in the shared
    cache. On the Pi this collapses hundreds of thousands of encode
    calls per House run into one pass over the unique texts.
    """
    if not texts:
        return np.array([])
    misses = [t for t in dict.fromkeys(texts) if t not in _embedding_cache]
    if misses:
        from app.pipeline.vector_store import get_embedding_model
        embs = get_embedding_model().encode(
            misses, show_progress_bar=False, batch_size=min(64, len(misses)),
        )
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embs = embs / norms
        for t, e in zip(misses, embs):
            _embedding_cache[t] = e
    return np.array([_embedding_cache[t] for t in texts])


def clear_alignment_cache() -> None:
    """Clear cached embeddings (call between pipeline runs)."""
    global _industry_policy_scores
    _embedding_cache.clear()
    _industry_policy_scores = None


# ── Industry ↔ Policy alignment ──────────────────────────────────

# These descriptions are used as embedding anchors. They are NOT classification
# labels — they describe the semantic space of each domain so that cosine
# similarity can determine relatedness between any donor industry and any bill
# policy area, including combinations that a static mapping would miss.

INDUSTRY_ANCHORS: dict[str, str] = {
    "FINANCE": "banking financial services investment securities lending credit Wall Street",
    "PHARMA": "pharmaceutical drugs biotech medicine vaccines prescription",
    "INSURANCE": "insurance coverage premiums health insurance property casualty underwriting",
    "HEALTHCARE": "hospital medical health system clinic physician nursing patient care",
    "OIL_GAS": "oil gas petroleum drilling fracking pipeline fossil fuel refinery",
    "ENERGY": "energy utility electric power renewable solar wind nuclear generation",
    "DEFENSE": "defense military weapons aerospace contractors armed forces Pentagon",
    "TECH": "technology software internet artificial intelligence data privacy cybersecurity",
    "TELECOM": "telecommunications wireless broadband cable cellular network",
    "REAL_ESTATE": "real estate property housing mortgage realty homebuilder",
    "AGRIBUSINESS": "agriculture farming crop livestock dairy grain ranching fertilizer",
    "TRANSPORT": "transportation airline aviation railroad shipping trucking logistics",
    "CONSTRUCTION": "construction building contractor engineering infrastructure",
    "LAWYERS": "law firm attorney legal litigation counsel",
    "GUNS": "firearm gun rifle ammunition weapons second amendment NRA",
    "TOBACCO": "tobacco cigarette vaping nicotine smoking",
    "CRYPTO": "cryptocurrency bitcoin blockchain digital currency",
    "PRIVATE_PRISON": "prison corrections incarceration detention correctional",
    "LABOR_UNIONS": "union labor workers collective bargaining organized labor",
    "GAMBLING": "casino gambling gaming sports betting lottery",
    "LOBBYISTS": "lobbying government relations public affairs advocacy",
    "MEDIA": "media broadcast television news publishing journalism",
    "RETAIL": "retail store consumer goods shopping wholesale",
    "MANUFACTURING": "manufacturing factory production industrial assembly",
    "EDUCATION": "university college school education academic research",
}

POLICY_ANCHORS: dict[str, str] = {
    "LABOR": "labor unions workers employment wages collective bargaining workforce rights",
    "DEFENSE": "military defense national security armed forces veterans weapons Pentagon",
    "GUNS": "firearms gun control second amendment weapons background checks",
    "HEALTHCARE": "healthcare medical insurance hospitals Medicare Medicaid prescription drugs",
    "ENVIRONMENT": "environment climate change pollution EPA emissions conservation clean energy",
    "TAXES": "taxes federal budget government spending appropriations tax reform fiscal policy",
    "IMMIGRATION": "immigration border security asylum refugees visa citizenship",
    "EDUCATION": "education schools universities student loans teachers curriculum",
    "FINANCIAL": "financial regulation banking oversight consumer protection Wall Street",
    "ENERGY": "energy renewable solar wind nuclear fossil fuel pipeline electricity grid",
    "TECH": "technology internet data privacy cybersecurity artificial intelligence antitrust",
    "JUSTICE": "criminal justice law enforcement courts sentencing civil rights executive power",
    "TRADE": "international trade tariffs sanctions imports exports commerce agreements",
    "WELFARE": "social programs safety net food assistance housing Social Security disaster relief",
    "PROCEDURAL": "procedural motion cloture table adjourn quorum nomination confirmation",
}



_industry_policy_scores: dict[tuple[str, str], float] | None = None


def _compute_industry_policy_matrix() -> dict[tuple[str, str], float]:
    """Pre-compute cosine similarity between every industry and policy anchor."""
    global _industry_policy_scores
    if _industry_policy_scores is not None:
        return _industry_policy_scores

    ind_keys = list(INDUSTRY_ANCHORS.keys())
    pol_keys = list(POLICY_ANCHORS.keys())

    ind_embs = _embed_batch(list(INDUSTRY_ANCHORS.values()))
    pol_embs = _embed_batch(list(POLICY_ANCHORS.values()))

    scores = ind_embs @ pol_embs.T

    _industry_policy_scores = {}
    for i, ind in enumerate(ind_keys):
        for j, pol in enumerate(pol_keys):
            _industry_policy_scores[(ind, pol)] = float(scores[i, j])

    logger.info(
        "Computed %d industry↔policy similarity scores",
        len(_industry_policy_scores),
    )
    return _industry_policy_scores


def industry_policy_similarity(industry: str, policy_area: str) -> float:
    """Get the semantic similarity between a donor industry and a bill policy area.

    Returns a float in [-1, 1] where higher = more related.
    Replaces the hardcoded _INDUSTRY_POLICY_MAP.
    """
    scores = _compute_industry_policy_matrix()
    return scores.get((industry, policy_area), 0.0)


def get_related_policies(
    industry: str,
    threshold: float = 0.35,
) -> set[str]:
    """Get policy areas semantically related to a donor industry.

    Drop-in replacement for _INDUSTRY_POLICY_MAP[industry].
    """
    scores = _compute_industry_policy_matrix()
    return {
        pol for (ind, pol), score in scores.items()
        if ind == industry and score >= threshold and pol != "PROCEDURAL"
    }




# ── Donor ↔ Vote connection detection ────────────────────────────


def detect_donor_vote_connections(
    donors: list[dict],
    votes: list[dict],
    industry_breakdown: list[dict] | None = None,
    min_industry_share: float = 25.0,
    policy_similarity_threshold: float = 0.75,
    max_matches: int = 8,
) -> list[dict]:
    """Detect connections between a senator's donor base and their votes.

    Two-stage gate, both required, addressing a 2026-07 audit finding that
    the previous per-donor/raw-text-similarity design flagged essentially
    every vote near any donor regardless of size or topical relevance
    ("voted yea on the annual budget, a small donor benefits" is not a
    finding — this looks for cases like a real regulatory-capture story:
    a substantial share of a member's classifiable industry funding comes
    from one industry, AND they voted on legislation squarely in that
    industry's domain):

    1. **Substantial funding, not any funding.** Industry share is
       computed against the CLASSIFIED-industry-only total (excluding
       SMALL_DONORS/LARGE_INDIVIDUAL/UNCLASSIFIED/OTHER/POLITICAL — these
       are structurally not "an industry": unitemized small-dollar
       donors, individuals with no useful employer data, and non-
       contribution receipts like loans/transfers/interest; see
       normalize_finance.py's _build_industry_breakdown). Measured
       2026-07: against total_raised (including those buckets) even a
       senator's single largest industry rarely clears 5% — that
       denominator makes "substantial" mean nothing. Against classified-
       industry-only total, the same population's top industry share
       has a median of 32%, a usable, discriminating signal. Default
       threshold (25%) sits just below that median.

    2. **Policy-area-anchored matching, not raw free-text similarity.**
       The old approach embedded a donor's industry description against
       each vote's full free-text description — noisy, uncalibrated
       (any two pieces of formal legislative-register text share enough
       vocabulary to score misleadingly high). Votes already carry a
       classified policyArea; industry_policy_similarity() gives a
       stable, pre-computed similarity between an industry anchor and a
       policy-area anchor — both small, fixed taxonomies, not raw text.
       Measured 2026-07 (375 industry x policy-area pairs): genuine
       matches (TECH vs TECH, ENERGY vs ENERGY, DEFENSE vs DEFENSE)
       score 0.87-0.93; the cross-category noise floor sits at
       mean 0.66 / p90 0.71 — a clean gap. Default threshold (0.75)
       sits in that gap.
    """
    if not industry_breakdown:
        return []

    from app.pipeline.analyze.score_calculator import NON_INDUSTRY_CODES

    # LOBBYISTS is a service profession, not a policy domain — a lobbying
    # firm's PAC represents whichever clients pay it, across every policy
    # area, so its industry label doesn't reveal a specific interest the
    # way TECH/ENERGY/DEFENSE do (those directly name an economic sector).
    # Measured: LOBBYISTS is the only industry anchor that crosses the
    # 0.75 policy-similarity gate at all (0.751 vs TAXES, right at the
    # edge) — not because it's genuinely tax-focused, but because
    # "lobbying government relations public affairs advocacy" is broad
    # enough to drift near every policy area. Still counts as real
    # industry money for the funding-share denominator (it's not
    # SMALL_DONORS/UNCLASSIFIED-style non-money) — just not a valid
    # candidate for "this industry cares about this policy area."
    _NOT_POLICY_SPECIFIC = {"LOBBYISTS"}

    real_industries = [
        i for i in industry_breakdown
        if i.get("industry") not in NON_INDUSTRY_CODES and (i.get("total") or 0) > 0
    ]
    classified_total = sum(i["total"] for i in real_industries)
    if classified_total <= 0:
        return []

    substantial = [
        i for i in real_industries
        if i.get("industry") not in _NOT_POLICY_SPECIFIC
        and (i["total"] / classified_total * 100) >= min_industry_share
    ]
    if not substantial:
        return []

    substantive = [
        v for v in votes
        if v.get("vote") in ("Yea", "Nay")
        and v.get("policyArea", "PROCEDURAL") != "PROCEDURAL"
    ]
    if not substantive:
        return []

    # Largest individual donor within each qualifying industry, for a
    # concrete, human-readable headline name — the gating decision itself
    # is aggregate-level (above), this is display only.
    donors_by_industry: dict[str, list[dict]] = {}
    for d in donors:
        if d.get("type") in ("CandidateAffiliated", "Self-Funded", "SKIP"):
            continue
        donors_by_industry.setdefault(d.get("industry", ""), []).append(d)

    industry_matches: list[dict] = []

    for ind in substantial:
        industry = ind["industry"]
        share = ind["total"] / classified_total * 100

        matched_bills: list[str] = []
        desc_parts: list[str] = []
        best_sim = 0.0
        all_consensus = True

        for vote in substantive:
            sim = industry_policy_similarity(industry, vote.get("policyArea", ""))
            if sim < policy_similarity_threshold:
                continue

            bid = vote["billId"]
            if bid in matched_bills:
                continue
            matched_bills.append(bid)
            best_sim = max(best_sim, sim)

            vote_cast = vote["vote"]
            bill_name = vote.get("billName", bid)[:80]
            is_amendment = "amdt" in bid.lower() or "amendment" in bill_name.lower()

            yeas = vote.get("totalYeas") or vote.get("yeas", 0)
            nays = vote.get("totalNays") or vote.get("nays", 0)
            is_consensus = False
            if yeas + nays > 20:
                is_consensus = (max(yeas, nays) / (yeas + nays)) >= 0.85
            if not is_consensus:
                all_consensus = False

            desc_parts.append(
                f"Voted {vote_cast} on {'amendment ' if is_amendment else ''}"
                f"{bill_name} ({vote.get('policyArea', '')})"
                f"{' [Consensus]' if is_consensus else ''}"
            )

        if not matched_bills:
            continue

        top_donor_name = None
        if donors_by_industry.get(industry):
            top_donor_name = max(
                donors_by_industry[industry], key=lambda d: d.get("total", 0),
            ).get("name")

        industry_matches.append({
            "lobbyistOrg": top_donor_name or f"{industry.replace('_', ' ').title()} industry",
            "industry": industry,
            "lobbyingSpend": 0,
            "donationToSenator": round(ind["total"]),
            "billsInfluenced": matched_bills,
            # Always None: determining whether this vote aligned with
            # the donor's interest requires knowing which way the
            # donor's industry wanted the bill to go, and no ingested
            # source (LDA filings carry aggregate spend, not per-bill
            # positions) discloses that. Filling it in via a hand-
            # authored industry->stance mapping would be exactly the
            # kind of authored political conclusion this platform's
            # scores are designed never to contain (2026-07 audit;
            # see score_calculator.py's Independent Voting note).
            "senatorVoteAligned": None,
            "isConsensusVote": all_consensus,
            "similarity": round(best_sim, 3),
            "description": (
                f"{industry.replace('_', ' ').title()} accounts for "
                f"{share:.0f}% of this member's classifiable industry "
                f"funding (${ind['total']:,.0f}) "
                f"— a substantial concentration, not a typical spread "
                f"across many industries. Votes on related topics: "
                + ". ".join(desc_parts) + "."
            ),
        })

    industry_matches.sort(key=lambda m: m["donationToSenator"], reverse=True)
    return industry_matches[:max_matches]
