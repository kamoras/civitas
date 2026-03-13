"""
Embedding-based political data alignment engine.

Replaces hardcoded _INDUSTRY_POLICY_MAP and LLM-derived promise evaluation
with deterministic cosine similarity computation. All classification decisions
are made by comparing embeddings — the LLM is only used downstream for
human-readable narrative summaries of these pre-computed alignments.

Key functions:
  - industry_policy_similarity: replaces _INDUSTRY_POLICY_MAP
  - promise_vote_alignment: replaces LLM promise kept/broken classification
  - donor_vote_connection: replaces LLM lobbying match detection

Design principles:
  - Deterministic: same input always produces same output
  - Non-biased: no political opinion encoded, only semantic similarity
  - Auditable: similarity scores are transparent numbers, not black-box labels
"""

import logging
from functools import lru_cache

import numpy as np

logger = logging.getLogger(__name__)

_embedding_cache: dict[str, np.ndarray] = {}


def _embed(text: str) -> np.ndarray:
    """Embed text with caching to avoid redundant model calls."""
    if text in _embedding_cache:
        return _embedding_cache[text]
    from app.pipeline.vector_store import get_embedding_model
    emb = get_embedding_model().encode([text], show_progress_bar=False)[0]
    emb = emb / np.linalg.norm(emb)
    _embedding_cache[text] = emb
    return emb


def _embed_batch(texts: list[str]) -> np.ndarray:
    """Embed multiple texts efficiently."""
    if not texts:
        return np.array([])
    from app.pipeline.vector_store import get_embedding_model
    embs = get_embedding_model().encode(texts, show_progress_bar=False, batch_size=min(64, len(texts)))
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embs / norms


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


# ── Promise ↔ Vote alignment ─────────────────────────────────────


def compute_promise_vote_alignment(
    promise_text: str,
    votes: list[dict],
    sponsored_bills: list[dict] | None = None,
    max_related: int = 3,
    relevance_threshold: float = 0.28,
    bill_relevance_threshold: float = 0.40,
) -> dict:
    """Determine if a senator's actions align with a campaign promise.

    Evidence sources (all embedding-based, deterministic):
      1. **Votes** (primary): Yea/Nay on semantically related bills,
         weighted by stance direction. This is the strongest signal
         because it's a public, recorded commitment.
      2. **Sponsored bills** (supplementary): legislation the senator
         introduced that advances the promise. Sponsorship is weaker
         evidence than a vote (bills may stall, be symbolic, etc.)
         so it's weighted at 0.5× vote evidence. It can only produce
         "kept" signals — not sponsoring anything isn't evidence of
         breaking a promise.

    Returns:
        {
            "alignment": "kept" | "broken" | "partial" | "unclear",
            "relatedVotes": [bill_ids],
            "relatedBills": [bill_ids],
            "confidence": 0.0-1.0,
            "reasoning": "factual description of evidence"
        }
    """
    empty = {
        "alignment": "unclear",
        "relatedVotes": [],
        "relatedBills": [],
        "confidence": 0.0,
        "reasoning": "",
    }
    if not promise_text:
        return empty
    if not votes and not sponsored_bills:
        return empty

    promise_emb = _embed(promise_text[:500])

    # ── Vote evidence ──
    substantive = [
        v for v in votes
        if v.get("vote") in ("Yea", "Nay")
        and v.get("policyArea", "PROCEDURAL") != "PROCEDURAL"
    ] if votes else []

    related_votes: list[str] = []
    kept_signals = 0.0
    broken_signals = 0.0
    reasons: list[str] = []

    if substantive:
        vote_texts = [
            f"{v.get('billName', '')} {v.get('description', '')} {v.get('policyArea', '')}"
            for v in substantive
        ]
        vote_embs = _embed_batch(vote_texts)

        if vote_embs.size > 0:
            similarities = vote_embs @ promise_emb
            top_indices = np.argsort(similarities)[::-1][:max_related]

            for idx in top_indices:
                sim = float(similarities[idx])
                if sim < relevance_threshold:
                    continue

                vote = substantive[idx]
                vote_cast = vote["vote"]
                stance = vote.get("stance", "neutral")
                related_votes.append(vote["billId"])

                if stance == "pro":
                    if vote_cast == "Yea":
                        kept_signals += sim
                    else:
                        broken_signals += sim
                elif stance == "anti":
                    if vote_cast == "Nay":
                        kept_signals += sim
                    else:
                        broken_signals += sim
                else:
                    half_sim = sim * 0.5
                    if vote_cast == "Yea":
                        kept_signals += half_sim
                    else:
                        broken_signals += half_sim

            for idx in top_indices[:2]:
                if float(similarities[idx]) < relevance_threshold:
                    continue
                v = substantive[idx]
                reasons.append(
                    f"Voted {v['vote']} on {v.get('billName', v['billId'])} "
                    f"({v.get('policyArea', 'N/A')})"
                )

    # ── Sponsored bill evidence ──
    BILL_WEIGHT = 0.5
    related_bills: list[str] = []

    if sponsored_bills:
        bill_texts = [
            (b.get("officialTitle") or b.get("title", ""))[:300]
            for b in sponsored_bills
            if b.get("title")
        ]
        if bill_texts:
            bill_embs = _embed_batch(bill_texts)
            if bill_embs.size > 0:
                bill_sims = bill_embs @ promise_emb
                top_bill_idx = np.argsort(bill_sims)[::-1][:3]

                for bidx in top_bill_idx:
                    bsim = float(bill_sims[bidx])
                    if bsim < bill_relevance_threshold:
                        continue
                    bill = sponsored_bills[bidx]
                    related_bills.append(bill.get("billId", ""))
                    kept_signals += bsim * BILL_WEIGHT
                    reasons.append(
                        f"Sponsored {bill.get('title', bill.get('billId', ''))[:80]}"
                    )

    if not related_votes and not related_bills:
        return {
            "alignment": "unclear",
            "relatedVotes": [],
            "relatedBills": [],
            "confidence": 0.0,
            "reasoning": "No votes or legislation found with sufficient relevance to this promise.",
        }

    total_signal = kept_signals + broken_signals
    if total_signal == 0:
        alignment = "unclear"
        confidence = 0.3
    elif kept_signals > broken_signals * 1.3:
        alignment = "kept"
        confidence = min(kept_signals / total_signal, 1.0)
    elif broken_signals > kept_signals * 1.3:
        alignment = "broken"
        confidence = min(broken_signals / total_signal, 1.0)
    elif total_signal > 0:
        alignment = "partial"
        confidence = 0.5
    else:
        alignment = "unclear"
        confidence = 0.0

    return {
        "alignment": alignment,
        "relatedVotes": related_votes,
        "relatedBills": related_bills,
        "confidence": round(confidence, 2),
        "reasoning": ". ".join(reasons) + "." if reasons else "",
    }


# ── Donor ↔ Vote connection detection ────────────────────────────


def detect_donor_vote_connections(
    donors: list[dict],
    votes: list[dict],
    similarity_threshold: float = 0.35,
    max_matches: int = 8,
) -> list[dict]:
    """Detect connections between donors and votes using embedding similarity.

    Replaces the hardcoded _INDUSTRY_POLICY_MAP approach in cross_reference.py.
    Instead of mapping FINANCE→{FINANCIAL,TAXES}, we compute the actual
    semantic similarity between each donor's industry description and each
    vote's content.
    """
    external = [
        d for d in donors
        if d.get("type") not in ("CandidateAffiliated", "SKIP")
        and d.get("industry") not in ("OTHER", "POLITICAL", "SMALL_DONORS", "LARGE_INDIVIDUAL")
    ]
    if not external:
        return []

    substantive = [
        v for v in votes
        if v.get("vote") in ("Yea", "Nay")
        and v.get("policyArea", "PROCEDURAL") != "PROCEDURAL"
    ]
    if not substantive:
        return []

    # Embed donor industry descriptions
    donor_texts = [
        INDUSTRY_ANCHORS.get(d.get("industry", ""), d.get("industry", ""))
        for d in external[:8]
    ]
    donor_embs = _embed_batch(donor_texts)
    if donor_embs.size == 0:
        return []

    # Embed vote descriptions
    vote_texts = [
        f"{v.get('billName', '')} {v.get('description', '')} {v.get('policyArea', '')}"
        for v in substantive
    ]
    vote_embs = _embed_batch(vote_texts)
    if vote_embs.size == 0:
        return []

    # Compute similarity matrix: donors x votes
    sim_matrix = donor_embs @ vote_embs.T

    # Consolidate per donor: one match entry per unique donor org,
    # with all related bills aggregated into billsInfluenced.
    donor_matches: dict[str, dict] = {}

    for i, donor in enumerate(external[:8]):
        donor_name = donor.get("name", "")
        donor_key = donor_name.upper().strip()

        best_vote_indices = np.argsort(sim_matrix[i])[::-1]
        best_sim = 0.0
        matched_bills: list[str] = []
        desc_parts: list[str] = []

        for j in best_vote_indices[:3]:
            sim = float(sim_matrix[i, j])
            if sim < similarity_threshold:
                break

            vote = substantive[j]
            bid = vote["billId"]
            if bid in matched_bills:
                continue
            matched_bills.append(bid)
            best_sim = max(best_sim, sim)

            vote_cast = vote["vote"]
            bill_name = vote.get("billName", bid)[:80]
            is_amendment = "amdt" in bid.lower() or "amendment" in bill_name.lower()
            desc_parts.append(
                f"Voted {vote_cast} on {'amendment ' if is_amendment else ''}"
                f"{bill_name} ({vote.get('policyArea', '')})"
            )

        if not matched_bills:
            continue

        if donor_key in donor_matches:
            existing = donor_matches[donor_key]
            for b in matched_bills:
                if b not in existing["billsInfluenced"]:
                    existing["billsInfluenced"].append(b)
            existing["similarity"] = max(existing["similarity"], best_sim)
        else:
            donor_matches[donor_key] = {
                "lobbyistOrg": donor_name,
                "industry": donor.get("industry", "OTHER"),
                "lobbyingSpend": 0,
                "donationToSenator": round(donor.get("total", 0)),
                "billsInfluenced": matched_bills,
                "senatorVoteAligned": None,
                "similarity": round(best_sim, 3),
                "description": (
                    f"{donor_name} ({donor.get('industry', '?')}) donated "
                    f"${donor.get('total', 0):,.0f}. "
                    + ". ".join(desc_parts) + "."
                ),
            }

    matches = sorted(
        donor_matches.values(),
        key=lambda m: m["donationToSenator"],
        reverse=True,
    )
    return matches[:max_matches]
