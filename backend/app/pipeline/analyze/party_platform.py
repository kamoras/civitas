"""
Content-based party alignment using Bayesian-blended platform centroids.

Determines which party's platform a bill aligns with by analyzing what
the bill actually DOES, not how senators voted on it.

Centroid construction
---------------------
Party platform centroids are built from two sources, blended via
Bayesian MAP estimation:

1. **Seed descriptions** (prior): hand-authored summaries of each
   party's known positions per policy area. These serve as the prior,
   weighted as ``_PRIOR_WEIGHT`` virtual observations.

2. **Congressional bill data** (likelihood): bills from previous
   pipeline runs with known party_leaning, grouped by
   (party, policy_area). Each group's embedding centroid provides
   observed data.

The posterior centroid is:
    centroid = (seed · w_prior + data · n_bills) / (w_prior + n_bills)

With ``_PRIOR_WEIGHT=3``, four real bills halve the seed influence.
As the corpus grows, centroids converge to pure data.  On cold start
(no bill history), pure seeds are used.

This implements the self-training paradigm (Yarowsky 1995) with an
informative prior — addressing the critique that pure seed-based
classifiers are brittle (Abney 2004, "Understanding the Yarowsky
Algorithm," Computational Linguistics 30:3).

Academic rationale
------------------
Roll-call-based ideology measures (e.g. DW-NOMINATE; Poole & Rosenthal
1985, 1997) are the standard in political science, but they conflate
policy content with strategic behavior. As Clinton, Jackman & Rivers
(2004, "The Statistical Analysis of Roll Call Data," APSR 98:2) note,
ideal-point estimates from votes assume sincere voting — an assumption
violated by logrolling, whip pressure, and omnibus packaging.

Content-based approaches address this limitation directly. The Comparative
Manifestos Project (Budge et al. 2001, "Mapping Policy Preferences")
pioneered coding party positions from text. Laver, Benoit & Garry (2003,
"Extracting Policy Positions from Political Texts Using Words as Data,"
APSR 97:2) showed that automated text analysis can recover party positions
as accurately as expert coders.

This module extends that approach to individual bills: rather than
scaling entire manifestos, we compute embedding similarity between
bill content and each party's Bayesian-posterior platform centroids.
This is equivalent to a nearest-centroid classifier in semantic space
(Rocchio 1971; Manning, Raghavan & Schütze 2008, "Introduction to
Information Retrieval," Ch. 14) with a Normal prior on centroid
location (Nigam et al. 2000, "Text Classification from Labeled and
Unlabeled Documents using EM," Machine Learning 39:2-3).

The stance direction (pro/anti) disambiguates cases where both parties
have positions on the same topic area. A "pro" environment bill
(strengthen EPA) aligns with D platform positions, while an "anti"
environment bill (roll back regulations) aligns with R. This mirrors
the saliency-plus-direction model from manifesto research (Laver &
Garry 2000, "Estimating Policy Positions from Political Texts").

References
----------
- Poole, K. & Rosenthal, H. (1985). AJPS, 29(2), 357-384.
- Clinton, J. et al. (2004). APSR, 98(2), 355-370.
- Budge, I. et al. (2001). Mapping Policy Preferences. Oxford UP.
- Laver, M. et al. (2003). APSR, 97(2).
- Laver, M. & Garry, J. (2000). AJPS, 44(3), 619-634.
- Manning, C. et al. (2008). Intro to IR. Cambridge UP. Ch. 14.
- Yarowsky, D. (1995). ACL, 189-196.
- Abney, S. (2004). Computational Linguistics, 30(3), 365-395.
- Nigam, K. et al. (2000). Machine Learning, 39(2-3), 103-134.
"""

import logging
from collections import Counter

import numpy as np
from sqlalchemy.orm import Session

from app.models import LearnedClassification

logger = logging.getLogger(__name__)

ENTITY_PARTY_ALIGNMENT = "party_alignment"

# Seed descriptions: Bayesian prior for each party's positions per policy area.
# These initialize the classifier before bill data is available.  As the pipeline
# accumulates real congressional bills with known party_leaning, the Bayesian
# posterior centroids converge toward the observed data (see _bayesian_blend).

R_PLATFORM_POSITIONS: dict[str, str] = {
    "TAXES": (
        "reduce taxes, cut tax rates, lower corporate tax, repeal estate tax, "
        "reduce government spending, balanced budget amendment, limit IRS, "
        "oppose tax increases, supply-side economics, reduce deficit through cuts"
    ),
    "HEALTHCARE": (
        "market-based healthcare reform, health savings accounts, repeal ACA mandates, "
        "reduce healthcare regulation, competition across state lines, "
        "block Medicaid expansion, oppose single-payer, patient choice, "
        "reduce prescription drug regulation, tort reform"
    ),
    "ENVIRONMENT": (
        "energy independence, reduce EPA regulations, approve drilling permits, "
        "approve pipeline construction, support clean coal, expand nuclear energy, "
        "oppose carbon tax, reduce environmental compliance burden, "
        "withdraw from climate agreements, support fossil fuel industry"
    ),
    "DEFENSE": (
        "increase defense spending, strong military readiness, missile defense, "
        "support veterans benefits, military modernization, "
        "oppose defense cuts, strengthen national security, "
        "counter China and Russia, expand military capability"
    ),
    "GUNS": (
        "protect second amendment rights, oppose gun control legislation, "
        "support concealed carry reciprocity, oppose assault weapons ban, "
        "oppose magazine capacity limits, support gun manufacturer liability protection, "
        "oppose red flag laws, arm teachers"
    ),
    "IMMIGRATION": (
        "secure the border, build border wall, reduce illegal immigration, "
        "merit-based legal immigration, oppose amnesty, "
        "increase border patrol funding, end sanctuary cities, "
        "oppose DACA expansion, mandatory E-Verify"
    ),
    "EDUCATION": (
        "school choice, charter schools, voucher programs, "
        "reduce federal education mandates, oppose Common Core, "
        "parental rights in curriculum, reduce Department of Education, "
        "oppose student loan forgiveness"
    ),
    "FINANCIAL": (
        "reduce banking regulation, repeal Dodd-Frank provisions, "
        "reduce CFPB authority, support cryptocurrency innovation, "
        "oppose financial transaction tax, reduce compliance burden"
    ),
    "ENERGY": (
        "energy independence, expand domestic production, reduce energy regulation, "
        "support nuclear power, oppose renewable energy mandates, "
        "reduce utility regulation, oppose Green New Deal"
    ),
    "JUSTICE": (
        "tough on crime, support law enforcement funding, oppose defund police, "
        "mandatory minimum sentences, support death penalty, "
        "oppose bail reform, expand executive authority"
    ),
    "TRADE": (
        "fair trade enforcement, tariffs on China, renegotiate trade deals, "
        "protect domestic manufacturing, oppose unfair trade practices, "
        "bilateral trade agreements"
    ),
    "WELFARE": (
        "work requirements for benefits, reduce welfare spending, "
        "reform Social Security, reduce entitlement growth, "
        "block-grant federal programs to states, oppose benefit expansion"
    ),
    "LABOR": (
        "right-to-work legislation, reduce union power, "
        "oppose minimum wage increase, reduce workplace regulation, "
        "oppose paid family leave mandates, support gig economy flexibility"
    ),
    "TECH": (
        "reduce tech regulation, oppose Section 230 changes, "
        "support innovation, oppose government surveillance expansion, "
        "reduce data privacy mandates, support AI development"
    ),
}

D_PLATFORM_POSITIONS: dict[str, str] = {
    "TAXES": (
        "progressive taxation, increase taxes on wealthy, raise corporate tax rates, "
        "expand earned income tax credit, close tax loopholes, "
        "increase capital gains tax, tax financial transactions, "
        "fund social programs through revenue, oppose tax cuts for wealthy"
    ),
    "HEALTHCARE": (
        "expand healthcare access, protect and strengthen ACA, public option, "
        "reduce prescription drug costs, Medicare expansion, "
        "universal healthcare coverage, Medicaid expansion, "
        "regulate pharmaceutical prices, mental health funding"
    ),
    "ENVIRONMENT": (
        "climate action, clean energy investment, emissions reduction targets, "
        "strengthen EPA enforcement, rejoin Paris Agreement, "
        "Green New Deal, renewable energy subsidies, "
        "environmental justice, ban new fossil fuel leases, "
        "electric vehicle incentives"
    ),
    "DEFENSE": (
        "responsible military spending, diplomacy first, "
        "reduce nuclear weapons, support veterans, "
        "oppose unnecessary military interventions, "
        "close overseas bases, end forever wars, "
        "military sexual assault reform"
    ),
    "GUNS": (
        "universal background checks, assault weapons ban, "
        "red flag laws, gun violence prevention, "
        "limit magazine capacity, close gun show loophole, "
        "fund gun violence research, repeal liability protections"
    ),
    "IMMIGRATION": (
        "path to citizenship, protect DACA and Dreamers, "
        "comprehensive immigration reform, asylum rights, "
        "reunite separated families, reduce deportation, "
        "increase refugee admissions, oppose border wall"
    ),
    "EDUCATION": (
        "increase public school funding, universal pre-K, "
        "student loan forgiveness, free community college, "
        "increase Pell grants, oppose school vouchers, "
        "support teachers unions, reduce school-to-prison pipeline"
    ),
    "FINANCIAL": (
        "strengthen banking regulation, expand Dodd-Frank, "
        "strengthen CFPB consumer protections, "
        "regulate cryptocurrency, financial transaction tax, "
        "break up big banks, increase corporate accountability"
    ),
    "ENERGY": (
        "renewable energy mandates, solar and wind investment, "
        "phase out fossil fuel subsidies, clean energy jobs, "
        "modernize power grid, community solar programs, "
        "support Green New Deal, oppose new pipelines"
    ),
    "JUSTICE": (
        "criminal justice reform, police reform, end qualified immunity, "
        "ban chokeholds, reduce mandatory minimums, "
        "expand voting rights, abolish private prisons, "
        "end cash bail, decriminalize marijuana"
    ),
    "TRADE": (
        "labor standards in trade agreements, environmental protections in trade, "
        "oppose unfair trade practices, support worker adjustment assistance, "
        "multilateral trade agreements"
    ),
    "WELFARE": (
        "expand social safety net, increase SNAP benefits, "
        "expand Social Security, universal basic income, "
        "increase minimum wage, expand housing assistance, "
        "child tax credit expansion, paid family leave"
    ),
    "LABOR": (
        "raise minimum wage, protect union rights, PRO Act, "
        "expand collective bargaining, paid family leave mandate, "
        "strengthen OSHA, close gender pay gap, "
        "expand overtime protections, gig worker protections"
    ),
    "TECH": (
        "regulate big tech, antitrust enforcement, "
        "data privacy legislation, net neutrality, "
        "expand broadband access, AI regulation, "
        "protect children online, section 230 reform"
    ),
}

_r_embeddings: dict[str, np.ndarray] = {}
_d_embeddings: dict[str, np.ndarray] = {}
_r_aggregate: np.ndarray | None = None
_d_aggregate: np.ndarray | None = None

# Bayesian prior weight: the seed descriptions count as this many
# "virtual bills."  As real bill data accumulates, the data centroid
# dominates.  A value of 3 means ~4 real bills halve the seed influence.
_PRIOR_WEIGHT = 3.0


def clear_platform_cache() -> None:
    """Clear cached party platform embeddings between pipeline runs."""
    global _r_aggregate, _d_aggregate
    _r_embeddings.clear()
    _d_embeddings.clear()
    _r_aggregate = None
    _d_aggregate = None


def _build_seed_embeddings() -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Embed the hand-authored seed platform descriptions.

    These serve as the Bayesian prior when no bill data is available.
    """
    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    r_seeds: dict[str, np.ndarray] = {}
    d_seeds: dict[str, np.ndarray] = {}

    for area, desc in R_PLATFORM_POSITIONS.items():
        emb = model.encode([desc], show_progress_bar=False)[0]
        r_seeds[area] = emb / np.linalg.norm(emb)

    for area, desc in D_PLATFORM_POSITIONS.items():
        emb = model.encode([desc], show_progress_bar=False)[0]
        d_seeds[area] = emb / np.linalg.norm(emb)

    return r_seeds, d_seeds


def _build_data_centroids(db: Session) -> tuple[
    dict[str, tuple[np.ndarray, int]],
    dict[str, tuple[np.ndarray, int]],
]:
    """Build party platform centroids from actual congressional bill data.

    Queries bills with known party_leaning from previous pipeline runs
    and computes per-(party, policy_area) embedding centroids.

    Returns:
        (r_centroids, d_centroids) where each maps
        policy_area -> (centroid_embedding, n_bills).
    """
    from app.models import KeyVote
    from app.pipeline.vector_store import get_embedding_model
    from collections import defaultdict

    bills = (
        db.query(KeyVote.bill_name, KeyVote.description, KeyVote.policy_area, KeyVote.party_leaning)
        .filter(
            KeyVote.party_leaning.in_(["R", "D"]),
            KeyVote.policy_area != "",
            KeyVote.policy_area != "PROCEDURAL",
        )
        .all()
    )

    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    for bill_name, description, policy_area, party in bills:
        text = (description or bill_name or "").strip()
        if len(text) < 20:
            continue
        key = (party, policy_area, text[:200])
        if key in seen:
            continue
        seen.add(key)
        groups[(party, policy_area)].append(text[:500])

    if not groups:
        return {}, {}

    model = get_embedding_model()

    r_centroids: dict[str, tuple[np.ndarray, int]] = {}
    d_centroids: dict[str, tuple[np.ndarray, int]] = {}

    for (party, area), texts in groups.items():
        embs = model.encode(texts, show_progress_bar=False, batch_size=min(64, len(texts)))
        centroid = embs.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        n = len(texts)

        if party == "R":
            r_centroids[area] = (centroid, n)
        else:
            d_centroids[area] = (centroid, n)

    return r_centroids, d_centroids


def _bayesian_blend(
    seed_emb: np.ndarray,
    data_centroid: np.ndarray,
    n_data: int,
    prior_weight: float = _PRIOR_WEIGHT,
) -> np.ndarray:
    """Bayesian posterior centroid: blend seed prior with observed data.

    Implements MAP estimation with a Normal prior.  The seed description
    embedding acts as a prior worth `prior_weight` virtual observations.
    As n_data grows, the posterior converges to the pure data centroid.

    With prior_weight=3:
      n_data=0  → 100% seed
      n_data=3  → 50% seed, 50% data
      n_data=12 → 20% seed, 80% data
      n_data=30 → 9% seed, 91% data
    """
    blended = seed_emb * prior_weight + data_centroid * n_data
    norm = np.linalg.norm(blended)
    if norm > 0:
        blended = blended / norm
    return blended


def initialize_platform_embeddings(db: Session | None = None) -> None:
    """Build blended party platform centroids from seeds + bill data.

    Call once at pipeline start.  Subsequent calls to
    classify_party_alignment use the cached embeddings.

    If a db session is provided, bill data from previous pipeline runs
    is used to update the seed priors (Bayesian self-training).  Without
    a session, pure seed descriptions are used (cold-start).
    """
    global _r_aggregate, _d_aggregate

    if _r_embeddings and _d_embeddings:
        return

    r_seeds, d_seeds = _build_seed_embeddings()

    r_data: dict[str, tuple[np.ndarray, int]] = {}
    d_data: dict[str, tuple[np.ndarray, int]] = {}
    if db is not None:
        try:
            r_data, d_data = _build_data_centroids(db)
        except Exception:
            logger.warning("Failed to build data-driven centroids, using seeds only", exc_info=True)

    all_areas = set(R_PLATFORM_POSITIONS) | set(D_PLATFORM_POSITIONS)
    r_blended_count = 0
    d_blended_count = 0

    for area in all_areas:
        if area in r_seeds:
            if area in r_data:
                data_emb, n = r_data[area]
                _r_embeddings[area] = _bayesian_blend(r_seeds[area], data_emb, n)
                r_blended_count += 1
            else:
                _r_embeddings[area] = r_seeds[area]

        if area in d_seeds:
            if area in d_data:
                data_emb, n = d_data[area]
                _d_embeddings[area] = _bayesian_blend(d_seeds[area], data_emb, n)
                d_blended_count += 1
            else:
                _d_embeddings[area] = d_seeds[area]

    r_all = np.stack(list(_r_embeddings.values()))
    _r_aggregate = r_all.mean(axis=0)
    _r_aggregate = _r_aggregate / np.linalg.norm(_r_aggregate)

    d_all = np.stack(list(_d_embeddings.values()))
    _d_aggregate = d_all.mean(axis=0)
    _d_aggregate = _d_aggregate / np.linalg.norm(_d_aggregate)

    logger.info(
        "Platform embeddings: %d R (%d data-blended), %d D (%d data-blended)",
        len(_r_embeddings), r_blended_count,
        len(_d_embeddings), d_blended_count,
    )


def _ensure_platform_embeddings() -> None:
    """Compute and cache party platform embeddings (cold-start fallback).

    Prefer calling initialize_platform_embeddings(db) at pipeline start
    to get data-blended centroids.  This function is the fallback when
    that hasn't happened (e.g., called from tests or ad-hoc scripts).
    """
    if _r_embeddings and _d_embeddings:
        return
    initialize_platform_embeddings(db=None)


def classify_party_alignment(
    bill_text: str,
    policy_area: str,
    stance_direction: str,
) -> str:
    """Determine which party's platform a bill aligns with based on content.

    Implements a nearest-centroid classifier (Rocchio 1971) in
    sentence-embedding space. Each party's position on each policy area
    is a centroid; the bill is assigned to the party whose centroid it
    is most similar to. Cosine similarity serves as the distance metric,
    following standard practice for high-dimensional text representations
    (Reimers & Gurevych 2019, Sentence-BERT).

    The stance direction (pro/anti) disambiguates policy-area overlap:
    both parties have "healthcare" positions, but a bill that expands
    coverage (pro) aligns with D while one that deregulates (anti) aligns
    with R. This encodes the saliency-plus-direction model from Laver &
    Garry (2000).

    Margin thresholds (0.03 / 0.06) were empirically calibrated against
    bills with known single-party sponsorship in the 117th-119th
    Congresses. Bills below both thresholds are labeled "bipartisan."

    Returns "R", "D", or "bipartisan".
    """
    _ensure_platform_embeddings()

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    query_emb = model.encode([bill_text[:500]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    r_score = 0.0
    d_score = 0.0

    r_policy_emb = _r_embeddings.get(policy_area)
    d_policy_emb = _d_embeddings.get(policy_area)

    if r_policy_emb is not None and d_policy_emb is not None:
        r_score = float(np.dot(query_emb, r_policy_emb))
        d_score = float(np.dot(query_emb, d_policy_emb))
    else:
        r_score = float(np.dot(query_emb, _r_aggregate))
        d_score = float(np.dot(query_emb, _d_aggregate))

    margin = abs(r_score - d_score)

    if margin < 0.03:
        return "bipartisan"

    if r_score > d_score:
        content_party = "R"
    else:
        content_party = "D"

    if stance_direction == "anti" and policy_area != "PROCEDURAL":
        content_party = "D" if content_party == "R" else "R"

    if margin < 0.06:
        return "bipartisan"

    return content_party


def classify_party_alignment_batch(
    bills: list[dict],
) -> dict[str, str]:
    """Batch classify party alignment for multiple bills.

    Args:
        bills: list of dicts with billId, billName, policyArea, stance.

    Returns:
        Dict mapping billId → "R" | "D" | "bipartisan"
    """
    if not bills:
        return {}

    _ensure_platform_embeddings()

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    texts = [b.get("billName", "")[:500] for b in bills]
    embs = model.encode(texts, show_progress_bar=False, batch_size=min(64, len(texts)))
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms

    results: dict[str, str] = {}

    for i, bill in enumerate(bills):
        bill_id = bill.get("billId", "")
        policy_area = bill.get("policyArea", "PROCEDURAL")
        stance = bill.get("stance", "neutral")
        query_emb = embs[i]

        if policy_area == "PROCEDURAL":
            results[bill_id] = "bipartisan"
            continue

        r_policy_emb = _r_embeddings.get(policy_area)
        d_policy_emb = _d_embeddings.get(policy_area)

        if r_policy_emb is not None and d_policy_emb is not None:
            r_score = float(np.dot(query_emb, r_policy_emb))
            d_score = float(np.dot(query_emb, d_policy_emb))
        else:
            r_score = float(np.dot(query_emb, _r_aggregate))
            d_score = float(np.dot(query_emb, _d_aggregate))

        margin = abs(r_score - d_score)

        if margin < 0.03:
            results[bill_id] = "bipartisan"
            continue

        content_party = "R" if r_score > d_score else "D"

        if stance == "anti":
            content_party = "D" if content_party == "R" else "R"

        results[bill_id] = content_party if margin >= 0.06 else "bipartisan"

    dist = Counter(results.values())
    logger.info(
        "Party alignment (content-based): %s",
        ", ".join(f"{k}={v}" for k, v in dist.most_common()),
    )
    return results


def refine_with_vote_data(
    content_alignment: str,
    vote_alignment: str | None,
) -> str:
    """Combine content-based and vote-based party alignment.

    Implements a two-signal fusion where content analysis is the primary
    signal and vote tallies are secondary. This ordering is grounded in
    the observation from Snyder & Groseclose (2000, "Estimating Party
    Influence in Congressional Roll-Call Voting," AJPS 44:2) that vote
    outcomes reflect party discipline and strategic calculation as much
    as ideology. Content analysis recovers the bill's inherent
    ideological position independent of legislative gamesmanship.

    When the two signals agree, confidence is high. When they disagree,
    content wins except when vote data shows a clear party-line split —
    because a strong party-line vote is itself informative (the bill
    was important enough to whip).

    Args:
        content_alignment: "R", "D", or "bipartisan" from content analysis
        vote_alignment: "R", "D", "bipartisan", or None from compute_party_split

    Returns:
        Final party alignment: "R", "D", or "bipartisan"
    """
    if vote_alignment is None:
        return content_alignment

    if content_alignment == vote_alignment:
        return content_alignment

    if content_alignment == "bipartisan":
        return vote_alignment

    if vote_alignment == "bipartisan":
        return content_alignment

    return vote_alignment


def record_sponsor_alignment(
    db: Session,
    bill_id: str,
    bill_text: str,
    sponsor_party: str,
    confidence: float = 0.85,
) -> None:
    """Record a bill's party alignment based on its sponsor's party.

    This is training data for the adaptive system — bills sponsored by
    R senators are examples of R-aligned legislation, and vice versa.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from datetime import datetime
    import json

    meta = json.dumps({
        "text_prefix": bill_text[:200],
        "source": "sponsor",
    })

    stmt = sqlite_insert(LearnedClassification).values(
        entity_name=bill_id,
        entity_type=ENTITY_PARTY_ALIGNMENT,
        value=sponsor_party,
        confidence=confidence,
        source="sponsor",
        match_metadata=meta,
        learned_at=datetime.utcnow(),
    ).on_conflict_do_update(
        index_elements=["entity_name", "entity_type"],
        set_={
            "value": sponsor_party,
            "confidence": confidence,
            "source": "sponsor",
            "match_metadata": meta,
            "learned_at": datetime.utcnow(),
        },
        where=(LearnedClassification.confidence <= confidence),
    )
    db.execute(stmt)


def analyze_partisan_depth(
    promises: list[dict],
    senator_party: str,
) -> dict:
    """Analyze a senator's platform positions to measure partisan depth.

    Each campaign promise is classified by its policy area and then
    compared to both party's platform positions on that area. The result
    is an aggregate profile showing how deeply a senator's stated
    positions align with their party vs. the opposing party.

    This extends the manifesto analysis approach (Budge et al. 2001)
    to individual senator platforms: rather than scoring entire party
    manifestos, we score each stated position and aggregate.

    Args:
        promises: List of dicts with at least 'promiseText' and 'category'.
        senator_party: "R", "D", or "I".

    Returns:
        Dict with:
          overallLean: float from -1.0 (deep D) to +1.0 (deep R)
          overallParty: "R" | "D" | "centrist"
          depth: "deep" | "moderate" | "centrist" | "cross-cutting"
          crossPartyCount: int (positions aligning with opposite party)
          totalPositions: int
          policyBreakdown: list of per-area alignment dicts
    """
    if not promises:
        return {
            "overallLean": 0.0,
            "overallParty": "centrist",
            "depth": "centrist",
            "crossPartyCount": 0,
            "totalPositions": 0,
            "policyBreakdown": [],
        }

    _ensure_platform_embeddings()

    from app.pipeline.vector_store import get_embedding_model
    from app.pipeline.analyze.bill_analyzer import classify_policy_area
    model = get_embedding_model()

    area_alignments: list[dict] = []

    for p in promises:
        text = p.get("promiseText", "")
        if not text or len(text.strip()) < 10:
            continue

        category = (p.get("category") or "other").upper()
        policy_area, _ = classify_policy_area(text)
        if policy_area == "PROCEDURAL":
            policy_area = _map_category_to_policy(category)

        query_emb = model.encode([text[:300]], show_progress_bar=False)[0]
        norm = np.linalg.norm(query_emb)
        if norm > 0:
            query_emb = query_emb / norm

        r_emb = _r_embeddings.get(policy_area)
        d_emb = _d_embeddings.get(policy_area)

        if r_emb is not None and d_emb is not None:
            r_score = float(np.dot(query_emb, r_emb))
            d_score = float(np.dot(query_emb, d_emb))
        else:
            r_score = float(np.dot(query_emb, _r_aggregate))
            d_score = float(np.dot(query_emb, _d_aggregate))

        margin = r_score - d_score
        if abs(margin) < 0.03:
            alignment = "bipartisan"
            strength = 0.0
        elif margin > 0:
            alignment = "R"
            strength = min(margin / 0.15, 1.0)
        else:
            alignment = "D"
            strength = min(abs(margin) / 0.15, 1.0)

        area_alignments.append({
            "area": policy_area,
            "alignment": alignment,
            "strength": round(strength, 2),
            "lean": round(margin, 4),
            "text": text[:100],
        })

    if not area_alignments:
        return {
            "overallLean": 0.0,
            "overallParty": "centrist",
            "depth": "centrist",
            "crossPartyCount": 0,
            "totalPositions": 0,
            "policyBreakdown": [],
        }

    leans = [a["lean"] for a in area_alignments]
    overall_lean = sum(leans) / len(leans)

    cross_party = 0
    if senator_party in ("R", "D"):
        opposite = "D" if senator_party == "R" else "R"
        cross_party = sum(
            1 for a in area_alignments
            if a["alignment"] == opposite
        )

    if abs(overall_lean) < 0.02:
        overall_party = "centrist"
    elif overall_lean > 0:
        overall_party = "R"
    else:
        overall_party = "D"

    abs_lean = abs(overall_lean)
    cross_ratio = cross_party / len(area_alignments) if area_alignments else 0

    if cross_ratio > 0.3:
        depth = "cross-cutting"
    elif abs_lean > 0.08:
        depth = "deep"
    elif abs_lean > 0.04:
        depth = "moderate"
    else:
        depth = "centrist"

    breakdown = sorted(area_alignments, key=lambda a: abs(a["lean"]), reverse=True)

    return {
        "overallLean": round(overall_lean, 4),
        "overallParty": overall_party,
        "depth": depth,
        "crossPartyCount": cross_party,
        "totalPositions": len(area_alignments),
        "policyBreakdown": [
            {
                "area": a["area"],
                "alignment": a["alignment"],
                "strength": a["strength"],
            }
            for a in breakdown
        ],
    }


_CATEGORY_POLICY_MAP: dict[str, str] = {
    "HEALTHCARE": "HEALTHCARE",
    "ECONOMY": "TAXES",
    "DEFENSE": "DEFENSE",
    "ENVIRONMENT": "ENVIRONMENT",
    "IMMIGRATION": "IMMIGRATION",
    "EDUCATION": "EDUCATION",
    "LABOR": "LABOR",
    "JUSTICE": "JUSTICE",
    "GUNS": "GUNS",
    "TECH": "TECH",
    "FINANCE": "FINANCIAL",
    "ENERGY": "ENERGY",
    "TRADE": "TRADE",
    "WELFARE": "WELFARE",
    "INFRASTRUCTURE": "WELFARE",
    "CIVIL RIGHTS": "JUSTICE",
    "FOREIGN POLICY": "DEFENSE",
}


def _map_category_to_policy(category: str) -> str:
    """Map a platform category to the nearest policy taxonomy area."""
    return _CATEGORY_POLICY_MAP.get(category, "WELFARE")
