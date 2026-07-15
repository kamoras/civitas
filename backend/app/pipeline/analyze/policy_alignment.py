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
import re

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

# ── Promise category ↔ vote policy-area compatibility ────────────
#
# A raw cosine threshold alone can't cleanly separate true matches from
# noise: the two distributions overlap (measured — see
# compute_promise_vote_alignment docstring), so some cross-domain pairs
# score higher than some genuine same-domain matches (e.g. a promise
# about traffic-fatality resolutions scored 0.823 against a vote to
# terminate a tariff national emergency — above the calibrated 0.80
# threshold, but the two have nothing to do with each other). Gating on
# whether the promise's classified category is even plausibly the same
# domain as the vote's policyArea closes that gap for clear cross-domain
# cases without relying on the embedding score to do all the work.
# Deliberately permissive (categories map to several policy areas, not
# one) to avoid rejecting genuine multi-domain promises; "other" and
# categories with no clean POLICY_ANCHORS equivalent (infrastructure)
# are left ungated since we have no reliable mapping for them.
_CATEGORY_POLICY_COMPAT: dict[str, set[str]] = {
    "healthcare": {"HEALTHCARE"},
    "economy": {"TAXES", "FINANCIAL"},
    "defense": {"DEFENSE"},
    "environment": {"ENVIRONMENT", "ENERGY"},
    "immigration": {"IMMIGRATION"},
    "education": {"EDUCATION"},
    "labor": {"LABOR"},
    "justice": {"JUSTICE"},
    "guns": {"GUNS"},
    "tech": {"TECH"},
    "finance": {"FINANCIAL", "TAXES"},
    "energy": {"ENERGY", "ENVIRONMENT"},
    "trade": {"TRADE"},
    "welfare": {"WELFARE"},
    "civil_rights": {"JUSTICE", "IMMIGRATION"},
    "foreign_policy": {"TRADE", "DEFENSE"},
}
# A near-exact text match can still be a genuine cross-domain promise
# (our category taxonomy is a heuristic, not ground truth) — let very
# high similarity override the gate rather than hard-block it.
_CATEGORY_GATE_OVERRIDE = 0.95

# Sponsored bills mostly carry Congress.gov's raw CRS policy-area label
# (~30 values, e.g. "ARMED_FORCES_AND_NATIONAL_SECURITY") rather than
# this project's curated POLICY_ANCHORS taxonomy — senate_pipeline.py
# prefers the CRS label verbatim whenever Congress.gov supplies one, so
# over 98% of sponsored_bills rows are raw CRS, not curated (2026-07
# audit). Votes (key_votes) are unaffected and already use the curated
# taxonomy. Without this mapping the category gate would silently
# reject nearly all sponsored-bill evidence, since e.g. "HEALTH" would
# never match "HEALTHCARE" despite meaning the same thing.
_CRS_TO_POLICY_ANCHOR: dict[str, str] = {
    "ARMED_FORCES_AND_NATIONAL_SECURITY": "DEFENSE",
    "CIVIL_RIGHTS_AND_LIBERTIES,_MINORITY_ISSUES": "JUSTICE",
    "CONGRESS": "PROCEDURAL",
    "CRIME_AND_LAW_ENFORCEMENT": "JUSTICE",
    "ECONOMICS_AND_PUBLIC_FINANCE": "TAXES",
    "ENVIRONMENTAL_PROTECTION": "ENVIRONMENT",
    "FINANCE_AND_FINANCIAL_SECTOR": "FINANCIAL",
    "FOREIGN_TRADE_AND_INTERNATIONAL_FINANCE": "TRADE",
    "GOVERNMENT_OPERATIONS_AND_POLITICS": "PROCEDURAL",
    "HEALTH": "HEALTHCARE",
    "HOUSING_AND_COMMUNITY_DEVELOPMENT": "WELFARE",
    "INTERNATIONAL_AFFAIRS": "TRADE",
    "LABOR_AND_EMPLOYMENT": "LABOR",
    "LAW": "JUSTICE",
    "PUBLIC_LANDS_AND_NATURAL_RESOURCES": "ENVIRONMENT",
    "SCIENCE,_TECHNOLOGY,_COMMUNICATIONS": "TECH",
    "SOCIAL_WELFARE": "WELFARE",
    "TAXATION": "TAXES",
}


def _normalize_policy_area(raw: str) -> str:
    """Map a Congress.gov CRS policy-area label onto this project's curated taxonomy."""
    return _CRS_TO_POLICY_ANCHOR.get(raw, raw)


def _passes_category_gate(promise_category: str | None, policy_area: str, sim: float) -> bool:
    """Check whether a vote/bill's policy area is a plausible match for the promise's category."""
    if not promise_category:
        return True
    compat = _CATEGORY_POLICY_COMPAT.get(promise_category)
    if not compat:
        return True
    return _normalize_policy_area(policy_area) in compat or sim >= _CATEGORY_GATE_OVERRIDE


# Below the main relevance_threshold (0.80/0.82), a fixed cutoff can't tell
# "genuinely about this promise" from "written in the same bureaucratic
# register" — a 2026-07 audit found the best-matching vote for real,
# non-bill-quoting promises typically lands at 0.65-0.75 regardless of
# whether it's topically related (measured: same-category and
# different-category best-match distributions overlap almost entirely at
# the embedding level, medians 0.72 vs 0.76 on 56 live promises). Below
# GATE_LOW, drop silently — this is where cross-category noise ~p50 sits,
# so an LLM call would mostly be confirming noise. Between GATE_LOW and the
# main threshold, ask an LLM to read the actual text and judge genuine
# relatedness (see _should_count_as_evidence_llm) instead of trusting the
# embedding number alone. At/above the main threshold, keep auto-accepting
# with no LLM call, exactly as before this change.
VOTE_GATE_LOW = 0.62
BILL_GATE_LOW = 0.64


def _should_count_as_evidence_llm(
    promise_text: str, candidate_text: str, candidate_kind: str,
    default_on_failure: bool = False,
) -> bool:
    """LLM gate: is this genuine evidence for the promise?

    On a call/parse FAILURE (network error, unparseable response, or a
    response with no "relates" key — call_llm never raises, it returns
    None on any internal failure) this returns ``default_on_failure``
    rather than always False. A broken verification call must not
    silently override a signal the caller already trusted — see
    _passes_relevance for how the two callers set this differently:
    a borderline match (no prior confidence) still defaults to reject
    on failure, unchanged from before; a match that already cleared the
    high embedding threshold defaults to keep its accept, since the
    embedding score — not this call — was the actual evidence for it.
    Only an explicit ``{"relates": false}`` judgment fails closed.
    """
    from app.pipeline.analyze.ollama_client import call_llm
    from app.pipeline.analyze.prompts import promise_evidence_gate_prompt

    prompt = promise_evidence_gate_prompt(promise_text, candidate_text, candidate_kind)
    result = call_llm(
        prompt_version=prompt["promptVersion"],
        system_prompt=prompt["systemPrompt"],
        user_prompt=prompt["userPrompt"],
        cache_key={"type": "promise_evidence_gate", "promise": promise_text, "candidate": candidate_text},
        max_tokens=100,
    )
    if not isinstance(result, dict) or "relates" not in result:
        return default_on_failure
    return bool(result["relates"])


def _passes_relevance(
    sim: float, low: float, high: float, use_llm: bool,
    promise_text: str, candidate_text: str, candidate_kind: str,
) -> bool:
    """Relevance check. House (use_llm=False) keeps the original sharp
    threshold with no LLM call ever — 431 members, no budget for it.

    Senate (use_llm=True) LLM-verifies every candidate at/above ``low``,
    not just the old gray zone between ``low`` and ``high``: a same-
    category, high-embedding-similarity match can still be a poor fit
    for the SPECIFIC promise (e.g. a "expand Medicare" promise satisfied
    by a vote on HSA contributions — both HEALTHCARE-tagged and sharing
    enough legislative-register vocabulary to clear the embedding
    threshold, despite being different, even opposed, policy approaches
    — 2026-07 external audit finding). A confirmed LLM negative now
    rejects a match that used to auto-accept purely on embedding score.

    Below ``low``: still auto-reject with no LLM call — that's where
    cross-category noise sits at the embedding level, so a call would
    mostly be confirming noise, same reasoning as before this change.
    """
    if sim < low or not use_llm:
        return sim >= high
    return _should_count_as_evidence_llm(
        promise_text, candidate_text, candidate_kind,
        default_on_failure=(sim >= high),
    )


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

_OPPOSE_LEAD_RE = re.compile(
    r"^(?:oppose|repeal|block|stop|reject|overturn|against|"
    r"vote against|no on|end)\b",
    re.IGNORECASE,
)


def _promise_polarity(promise_text: str) -> str:
    """Infer whether a named-bill promise means to advance or block that bill.

    Defaults to "support": named-bill promises overwhelmingly come from
    a member's own sponsored legislation or platform plank ("Support the
    X Act", or just the bill's own title), not a bill they campaigned
    against.
    """
    return "oppose" if _OPPOSE_LEAD_RE.match(promise_text.strip()) else "support"


def _bill_name_tokens(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z']+", text) if len(w) > 3}


def _is_named_bill_match(promise_text: str, bill_name: str) -> bool:
    """Detect a promise that names/quotes a specific bill by title.

    When a promise IS a specific bill ("Support the Ending Importation
    of Russian Oil Act"), the bill's generic pro/anti policy-area
    direction (from ``derive_stance``) is the wrong signal for kept vs.
    broken. That direction describes whether the bill expands or
    restricts its own policy area, not whether this vote fulfills THIS
    promise — a bill titled "Ending X" is classified "anti" regardless
    of whether X is something the member wants ended. A Yea vote on it
    would then score as "broken" a promise to support ending X, which
    is backwards (2026-07 audit: Sen. Peters, "Support the Ending
    Importation of Russian Oil Act"). For a named-bill promise, whether
    it was kept is a direct question — did they vote the way they said
    they would — not a question of the bill's own directionality.
    """
    bill_tokens = _bill_name_tokens(bill_name)
    if len(bill_tokens) < 3:
        return False
    promise_tokens = _bill_name_tokens(promise_text)
    overlap = promise_tokens & bill_tokens
    # An absolute floor alongside the ratio: a short 3-token title only
    # needs 2 generic domain words in common to clear a 0.6 ratio (e.g.
    # "Prescription Drug Pricing Act" vs. a promise merely about drug
    # costs), which is topical relevance, not a title quote. Requiring
    # >=3 shared distinctive words keeps this to genuine name matches.
    return len(overlap) >= 3 and len(overlap) / len(bill_tokens) > 0.6


def compute_promise_vote_alignment(
    promise_text: str,
    votes: list[dict],
    sponsored_bills: list[dict] | None = None,
    max_related: int = 3,
    relevance_threshold: float = 0.80,
    bill_relevance_threshold: float = 0.82,
    use_llm: bool = True,
    promise_category: str | None = None,
) -> dict:
    """Determine if a member's actions align with a campaign promise.

    With ``use_llm=True`` (Senate pipeline), the local LLM (Qwen2.5 1.5B —
    see config.py's OLLAMA_MODEL) decomposes the promise into a richer
    semantic query before the embedding search.
    With ``use_llm=False`` (House pipeline — deterministic by design,
    and 431 members × several promises of decomposition calls would add
    hours), the raw promise text is embedded directly; the alignment
    rules downstream are identical.

    Thresholds are calibrated against this project's embedding model
    (Snowflake arctic-embed-xs), not a generic 0-1 cosine scale: any two
    pieces of formal legislative-register English score ~0.55-0.87 on
    this model from shared register alone (measured on 300 promise/vote
    and promise/bill pairs cross-category, i.e. genuinely unrelated —
    p90 0.73/0.79, p99 0.80/0.83), while true matches (promise text that
    names or quotes the actual bill) score ~0.77-1.0 (p10 0.84/0.81).
    The old defaults (0.28, 0.40) sat entirely inside the noise floor,
    so the "relevance" filter passed ~100% of unrelated votes/bills —
    the alignment engine was citing whatever ranked highest among noise
    as "evidence" for a promise. 0.80/0.82 cut cross-category false
    positives to ~1-2% while keeping ~88-97% of true bill-quoting
    matches, but the two distributions overlap in their tails: a
    tariff-emergency-termination vote scored 0.823 against a
    traffic-fatality-resolution promise (2026-07 audit), above the
    threshold despite zero topical connection. ``promise_category``
    (see ``_passes_category_gate``) closes that residual gap by also
    requiring the vote/bill's policy area to be a plausible match for
    the promise's domain, rather than relying on the embedding score
    alone to separate signal from noise.

    88-97% recall was measured on bill-quoting promises specifically.
    Most real campaign promises are generic platform language ("Expand
    Medicare coverage"), not bill quotes, and a 2026-07 audit found their
    best genuinely-related vote typically scores only 0.65-0.75 — below
    threshold, and empirically no higher than same-promise unrelated
    votes at the raw embedding level (same-category vs. different-category
    best-match distributions overlap almost entirely: medians 0.72 vs.
    0.76 on 56 live promises). This had been quietly collapsing Promise
    Persistence toward a flat neutral score for most senators (93% of
    promises landing "unclear"; population stdev 3.7 against a 8.0 floor)
    even after the v5.3 shrinkage-prior recalibration, because that fix
    assumed a typical evaluable-promise count the real evidence rate
    never reached. Below the main threshold, ``_passes_relevance`` now
    asks an LLM to read the actual promise and candidate text and judge
    genuine relatedness (Senate only, ``use_llm=True``) rather than
    dropping every sub-threshold candidate — see ``VOTE_GATE_LOW`` /
    ``BILL_GATE_LOW``.

    v2 (2026-07, external audit finding): at/above threshold now ALSO gets
    an LLM check (Senate only) rather than auto-accepting on embedding
    score alone — a same-category, high-similarity match can still be a
    poor fit for the specific promise (a "expand Medicare" promise
    satisfied by an HSA-contribution vote; both HEALTHCARE-tagged and
    close enough in legislative register to clear threshold, despite being
    different policy approaches). See ``_passes_relevance`` for the
    fail-open-on-call-failure / fail-closed-on-confirmed-negative split
    that keeps this from regressing high-confidence matches when the LLM
    call itself fails.
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

    search_text = promise_text
    if use_llm:
        # 1. Decompose promise via LLM for better search query
        from app.pipeline.analyze.prompts import promise_decomposition_prompt
        from app.pipeline.analyze.ollama_client import call_llm, extract_json

        decomp_prompt = promise_decomposition_prompt(promise_text)
        raw_decomp = call_llm(
            system_prompt=decomp_prompt["systemPrompt"],
            user_prompt=decomp_prompt["userPrompt"],
            prompt_version=decomp_prompt["promptVersion"],
            cache_key={"promise": promise_text},
            max_tokens=300
        )
        # call_llm returns already-parsed JSON; extract_json is only needed for raw strings
        decomp = raw_decomp if isinstance(raw_decomp, dict) else (extract_json(raw_decomp) if raw_decomp else None)

        # Use the LLM's optimized search query if available, otherwise fallback
        if decomp and decomp.get("searchQuery"):
            # Blend original + optimized for best coverage
            search_text = f"{promise_text} {decomp['searchQuery']} {' '.join(decomp.get('keywords', []))}"

    promise_emb = _embed(search_text[:1000])

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
                if not _passes_relevance(
                    sim, VOTE_GATE_LOW, relevance_threshold, use_llm,
                    promise_text, vote_texts[idx], "vote",
                ):
                    continue
                if not _passes_category_gate(
                    promise_category, substantive[idx].get("policyArea", ""), sim,
                ):
                    continue

                vote = substantive[idx]
                vote_cast = vote["vote"]
                related_votes.append(vote["billId"])

                if _is_named_bill_match(promise_text, vote.get("billName", "")):
                    # The promise names this exact bill — go straight to
                    # "did they vote the way they said they would" and
                    # skip the generic stance direction entirely.
                    wants_yea = _promise_polarity(promise_text) == "support"
                    if wants_yea == (vote_cast == "Yea"):
                        kept_signals += sim
                    else:
                        broken_signals += sim
                    continue

                stance = vote.get("stance", "neutral")
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
                # Neutral/unknown stance: the vote is related to the promise
                # topic but we cannot tell which direction honors it, so it
                # contributes no kept/broken signal. (Previously a Yea on any
                # topically-related bill counted as half-kept, which — since
                # most classified stances are neutral and most floor votes
                # pass — biased promise evaluation heavily toward "kept":
                # the 2026-06 audit measured 88% of promises as kept.)

            for idx in top_indices[:2]:
                sim = float(similarities[idx])
                if not _passes_relevance(
                    sim, VOTE_GATE_LOW, relevance_threshold, use_llm,
                    promise_text, vote_texts[idx], "vote",
                ):
                    continue
                if not _passes_category_gate(promise_category, substantive[idx].get("policyArea", ""), sim):
                    continue
                v = substantive[idx]
                reasons.append(
                    f"Voted {v['vote']} on {v.get('billName', v['billId'])} "
                    f"({v.get('policyArea', 'N/A')})"
                )

    # ── Sponsored bill evidence ──
    # Sponsoring a bill on the promised topic is effort, not fulfillment:
    # introducing a bill is free and senators sponsor bills on their own
    # platform topics almost by definition. Counting introduction as
    # "kept" (as prior versions did) made promise evaluation circular —
    # promises extracted from a senator's platform were marked kept
    # because the senator sponsored legislation about their platform.
    # Only bills that actually advanced (became law, passed a chamber,
    # or were ordered reported) count as kept evidence; introduction-only
    # bills accumulate an "effort" signal that can at most yield a
    # "partial" alignment.
    BILL_WEIGHT = 0.5
    related_bills: list[str] = []
    effort_signals = 0.0

    if sponsored_bills:
        candidates = [b for b in sponsored_bills if b.get("title")]
        bill_texts = [
            (b.get("officialTitle") or b.get("title", ""))[:300]
            for b in candidates
        ]
        if bill_texts:
            bill_embs = _embed_batch(bill_texts)
            if bill_embs.size > 0:
                bill_sims = bill_embs @ promise_emb
                top_bill_idx = np.argsort(bill_sims)[::-1][:3]

                for bidx in top_bill_idx:
                    bsim = float(bill_sims[bidx])
                    if not _passes_relevance(
                        bsim, BILL_GATE_LOW, bill_relevance_threshold, use_llm,
                        promise_text, bill_texts[bidx], "sponsored bill",
                    ):
                        continue
                    bill = candidates[bidx]
                    if not _passes_category_gate(
                        promise_category, bill.get("policyArea", ""), bsim,
                    ):
                        continue
                    related_bills.append(bill.get("billId", ""))

                    action = (bill.get("latestAction") or "").lower()
                    bill_advanced = bill.get("isLaw") or any(
                        kw in action for kw in [
                            "passed", "agreed to", "ordered to be reported",
                        ]
                    )
                    if bill_advanced:
                        kept_signals += bsim * BILL_WEIGHT
                        reasons.append(
                            f"Advanced {bill.get('title', bill.get('billId', ''))[:80]}"
                        )
                    else:
                        effort_signals += bsim * BILL_WEIGHT
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
        if effort_signals > 0:
            # Legislation introduced on the topic but no directional vote
            # evidence and nothing advanced: acted on the promise without
            # a measurable outcome.
            alignment = "partial"
            confidence = 0.4
        else:
            alignment = "unclear"
            confidence = 0.3
    elif kept_signals > broken_signals * 1.3:
        alignment = "kept"
        confidence = min(kept_signals / total_signal, 1.0)
    elif broken_signals > kept_signals * 1.3:
        alignment = "broken"
        confidence = min(broken_signals / total_signal, 1.0)
    else:
        alignment = "partial"
        confidence = 0.5

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
