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
        "preserve low tax rates, supply-side economics, reduce deficit through cuts"
    ),
    "HEALTHCARE": (
        "market-based healthcare reform, health savings accounts, repeal ACA mandates, "
        "reduce healthcare regulation, competition across state lines, "
        "voluntary private insurance, patient choice, "
        "reduce prescription drug regulation, tort reform"
    ),
    "ENVIRONMENT": (
        "energy independence, reduce EPA regulations, approve drilling permits, "
        "approve pipeline construction, support clean coal, expand nuclear energy, "
        "market-based environmental solutions, reduce environmental compliance burden, "
        "withdraw from climate agreements, support fossil fuel industry"
    ),
    "DEFENSE": (
        "increase defense spending, strong military readiness, missile defense, "
        "support veterans benefits, military modernization, "
        "maintain defense budget, strengthen national security, "
        "counter China and Russia, expand military capability, "
        "support arms sales to allies, streamline defense exports"
    ),
    "GUNS": (
        "protect second amendment rights, defend gun ownership and access to firearms, "
        "support concealed carry reciprocity, preserve access to ammunition and magazines, "
        "support gun manufacturer liability protection, "
        "arm teachers for self-defense, expand firearm freedoms, "
        "castle doctrine, stand your ground laws, deregulate firearms"
    ),
    "IMMIGRATION": (
        "secure the border, build border wall, reduce illegal immigration, "
        "merit-based legal immigration, enforcement-first approach, "
        "increase border patrol funding, end sanctuary cities, "
        "end DACA, mandatory E-Verify"
    ),
    "EDUCATION": (
        "school choice, charter schools, voucher programs, "
        "reduce federal education mandates, local curriculum control, "
        "parental rights in curriculum, reduce Department of Education, "
        "personal responsibility for student debt"
    ),
    "FINANCIAL": (
        "reduce banking regulation, repeal Dodd-Frank provisions, "
        "reduce CFPB authority, support cryptocurrency innovation, "
        "free market financial activity, reduce compliance burden"
    ),
    "ENERGY": (
        "energy independence, expand domestic production, reduce energy regulation, "
        "support nuclear power, market-driven energy mix, "
        "reduce utility regulation, fossil fuel production"
    ),
    "JUSTICE": (
        "tough on crime, support law enforcement funding, back the blue, "
        "mandatory minimum sentences, support death penalty, "
        "keep cash bail, expand executive authority"
    ),
    "TRADE": (
        "fair trade enforcement, tariffs on China, renegotiate trade deals, "
        "protect domestic manufacturing, trade reciprocity, "
        "bilateral trade agreements, support defense exports to allies"
    ),
    "WELFARE": (
        "work requirements for benefits, reduce welfare spending, "
        "reform Social Security, reduce entitlement growth, "
        "block-grant federal programs to states, self-sufficiency over dependency"
    ),
    "LABOR": (
        "right-to-work legislation, reduce union power, "
        "market-determined wages, reduce workplace regulation, "
        "voluntary employer benefits, support gig economy flexibility"
    ),
    "TECH": (
        "reduce tech regulation, maintain platform liability protections, "
        "support innovation, limit government digital overreach, "
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
        "military sexual assault reform, "
        "regulate arms sales and exports, arms trade oversight, "
        "human rights conditions on military transfers"
    ),
    "GUNS": (
        "universal background checks, assault weapons ban, "
        "red flag laws, gun violence prevention, "
        "limit magazine capacity, close gun show loophole, "
        "fund gun violence research, repeal gun manufacturer liability protections, "
        "regulate ammunition sales and transfers, restrict access to firearms, "
        "mandatory waiting periods, safe storage requirements"
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
        "multilateral trade agreements, "
        "regulate arms exports, human rights conditions on foreign sales"
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

# Bayesian prior weight: the seed descriptions count as this many
# "virtual bills."  As real bill data accumulates, the data centroid
# dominates.  A value of 3 means ~4 real bills halve the seed influence.
_PRIOR_WEIGHT = 3.0

# Below this R/D score margin, a bill is classified "bipartisan" rather
# than assigned to either party — empirically calibrated (see
# classify_party_alignment's docstring) against bills with known
# single-party sponsorship in the 117th-119th Congresses.
_BIPARTISAN_MARGIN_THRESHOLD = 0.06


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


def _populate_party_embeddings(
    areas: set[str],
    seeds: dict[str, np.ndarray],
    data: dict[str, tuple[np.ndarray, int]],
    embeddings: dict[str, np.ndarray],
) -> int:
    """Populate `embeddings` in place from seeds, Bayesian-blended with
    data centroids where available. Returns the count of areas blended
    with data (vs. pure seed). Shared by the R and D passes in
    _PlatformEmbeddingCache.initialize(), which are otherwise identical
    except for which party's seeds/data/embeddings dict they operate on.
    """
    blended_count = 0
    for area in areas:
        if area not in seeds:
            continue
        if area in data:
            data_emb, n = data[area]
            embeddings[area] = _bayesian_blend(seeds[area], data_emb, n)
            blended_count += 1
        else:
            embeddings[area] = seeds[area]
    return blended_count


def _normalized_mean(embeddings: dict[str, np.ndarray]) -> np.ndarray:
    """Unit-normalized centroid of all embeddings in the dict."""
    stacked = np.stack(list(embeddings.values()))
    mean = stacked.mean(axis=0)
    return mean / np.linalg.norm(mean)


class _PlatformEmbeddingCache:
    """In-memory cache of blended R/D party-platform centroid embeddings.

    Built once per pipeline run and cleared between runs. The two
    parties' embedding dicts and aggregate vectors are always populated
    together in initialize() and cleared together in clear() — a class
    makes that invariant explicit instead of implicit across two
    separate `global` statements.
    """

    def __init__(self) -> None:
        self.r_embeddings: dict[str, np.ndarray] = {}
        self.d_embeddings: dict[str, np.ndarray] = {}
        self.r_aggregate: np.ndarray | None = None
        self.d_aggregate: np.ndarray | None = None

    @property
    def is_loaded(self) -> bool:
        return bool(self.r_embeddings and self.d_embeddings)

    def clear(self) -> None:
        self.r_embeddings.clear()
        self.d_embeddings.clear()
        self.r_aggregate = None
        self.d_aggregate = None

    def initialize(self, db: Session | None = None) -> None:
        """Build blended party platform centroids from seeds + bill data.

        If a db session is provided, bill data from previous pipeline
        runs is used to update the seed priors (Bayesian self-training).
        Without a session, pure seed descriptions are used (cold-start).
        """
        if self.is_loaded:
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
        r_blended_count = _populate_party_embeddings(all_areas, r_seeds, r_data, self.r_embeddings)
        d_blended_count = _populate_party_embeddings(all_areas, d_seeds, d_data, self.d_embeddings)

        self.r_aggregate = _normalized_mean(self.r_embeddings)
        self.d_aggregate = _normalized_mean(self.d_embeddings)

        logger.info(
            "Platform embeddings: %d R (%d data-blended), %d D (%d data-blended)",
            len(self.r_embeddings), r_blended_count,
            len(self.d_embeddings), d_blended_count,
        )

    def ensure(self) -> None:
        """Cold-start fallback: initialize with seeds only if not already loaded."""
        if not self.is_loaded:
            self.initialize(db=None)


_platform_cache = _PlatformEmbeddingCache()


def clear_platform_cache() -> None:
    """Clear cached party platform embeddings between pipeline runs."""
    _platform_cache.clear()


def initialize_platform_embeddings(db: Session | None = None) -> None:
    """Build blended party platform centroids from seeds + bill data.

    Call once at pipeline start.  Subsequent calls to
    classify_party_alignment use the cached embeddings.

    If a db session is provided, bill data from previous pipeline runs
    is used to update the seed priors (Bayesian self-training).  Without
    a session, pure seed descriptions are used (cold-start).
    """
    _platform_cache.initialize(db)


def _ensure_platform_embeddings() -> None:
    """Compute and cache party platform embeddings (cold-start fallback).

    Prefer calling initialize_platform_embeddings(db) at pipeline start
    to get data-blended centroids.  This function is the fallback when
    that hasn't happened (e.g., called from tests or ad-hoc scripts).
    """
    _platform_cache.ensure()


def _stance_conditioned_query(bill_text: str, stance_direction: str) -> str:
    """Construct a stance-conditioned query for embedding classification.

    Sentence-transformer models struggle with antonymy and negation
    (Ettinger 2020, "What BERT Is Not: Lessons from a New Suite of
    Psycholinguistic Diagnostics for Language Models").  A bill that
    "restricts ammunition" and a platform that "defends access to
    ammunition" share most semantic content, making cosine similarity
    unreliable for distinguishing pro from anti stances.

    By prepending a directional prefix, we shift the query embedding
    toward the region of semantic space that matches the bill's
    legislative intent, allowing the nearest-centroid classifier to
    assign the correct party alignment.
    """
    if stance_direction == "pro":
        return f"legislation to support and strengthen: {bill_text[:480]}"
    if stance_direction == "anti":
        return f"legislation to restrict and limit: {bill_text[:480]}"
    return bill_text[:500]


def classify_party_alignment(
    bill_text: str,
    policy_area: str,
    stance_direction: str,
) -> str:
    """Determine which party's platform a bill aligns with based on content.

    Implements a nearest-centroid classifier (Rocchio 1971) in
    sentence-embedding space with stance-conditioned query construction.
    Each party's position on each policy area is a centroid; the bill is
    assigned to the party whose centroid it is most similar to.

    Stance conditioning (Reimers & Gurevych 2019, Sentence-BERT)
    ---------------------------------------------------------------
    The stance direction (pro/anti/neutral) is used to construct a
    directionally explicit query embedding.  Sentence-transformer models
    are weak at capturing negation/antonymy — "oppose gun control" and
    "gun control" embed nearly identically (Ettinger 2020, "What BERT Is
    Not").  Rather than relying on the embedding model to distinguish
    pro-X from anti-X, we prepend a stance-appropriate prefix that makes
    the bill's legislative direction explicit:

      pro  → "legislation to support and strengthen: ..."
      anti → "legislation to restrict and limit: ..."

    This shifts the query embedding toward the correct party's centroid
    without hardcoded party-stance mappings.  The platform descriptions
    are written in directionally positive language (what each party WANTS),
    so a "restrict" prefix naturally aligns with the party that wants
    restrictions in that policy area.

    Margin thresholds were empirically calibrated against bills with known
    single-party sponsorship in the 117th-119th Congresses.

    Returns "R", "D", or "bipartisan".
    """
    _ensure_platform_embeddings()

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    query_text = _stance_conditioned_query(bill_text, stance_direction)
    query_emb = model.encode([query_text], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    r_score = 0.0
    d_score = 0.0

    r_policy_emb = _platform_cache.r_embeddings.get(policy_area)
    d_policy_emb = _platform_cache.d_embeddings.get(policy_area)

    if r_policy_emb is not None and d_policy_emb is not None:
        r_score = float(np.dot(query_emb, r_policy_emb))
        d_score = float(np.dot(query_emb, d_policy_emb))
    else:
        r_score = float(np.dot(query_emb, _platform_cache.r_aggregate))
        d_score = float(np.dot(query_emb, _platform_cache.d_aggregate))

    margin = abs(r_score - d_score)

    if margin < _BIPARTISAN_MARGIN_THRESHOLD:
        return "bipartisan"

    return "R" if r_score > d_score else "D"


def classify_party_alignment_multi(
    bill_text: str,
    policy_areas: list[dict],
    stance_direction: str,
) -> dict:
    """Determine per-area party alignment and weighted aggregate for multi-area bills.

    Real legislation spans multiple policy domains (Adler & Wilkerson 2012).
    A bill touching HEALTHCARE and TAXES may align with D on healthcare
    (expanding coverage) but R on taxes (certain deductions). A senator's
    vote on such a bill represents a nuanced position, not a binary party
    choice.

    Per-area alignment uses the same nearest-centroid classifier as
    classify_party_alignment, with stance-conditioned query construction
    and directional platform descriptions.  The aggregate uses
    confidence-weighted voting: each area's alignment vote is weighted
    by its embedding confidence, following the weighted-expert framework
    in Clemen (1989, "Combining Forecasts: A Review and Annotated
    Bibliography," Intl J Forecasting 5:4).

    Returns:
        {
            "overall": "R" | "D" | "bipartisan",
            "weight": float 0-1 (how strongly the bill leans toward the overall party),
            "areas": [{"area": str, "party": str, "confidence": float}, ...]
        }
    """
    _ensure_platform_embeddings()

    if not policy_areas or all(a["area"] == "PROCEDURAL" for a in policy_areas):
        return {
            "overall": "bipartisan",
            "weight": 0.0,
            "areas": [],
        }

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    query_text = _stance_conditioned_query(bill_text, stance_direction)
    query_emb = model.encode([query_text], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    area_results: list[dict] = []
    r_weight = 0.0
    d_weight = 0.0

    for pa in policy_areas:
        area = pa["area"]
        area_conf = pa.get("confidence", 0.5)

        if area == "PROCEDURAL":
            continue

        r_policy_emb = _platform_cache.r_embeddings.get(area)
        d_policy_emb = _platform_cache.d_embeddings.get(area)

        if r_policy_emb is not None and d_policy_emb is not None:
            r_score = float(np.dot(query_emb, r_policy_emb))
            d_score = float(np.dot(query_emb, d_policy_emb))
        else:
            r_score = float(np.dot(query_emb, _platform_cache.r_aggregate))
            d_score = float(np.dot(query_emb, _platform_cache.d_aggregate))

        margin = abs(r_score - d_score)

        if margin < _BIPARTISAN_MARGIN_THRESHOLD:
            party = "bipartisan"
        else:
            party = "R" if r_score > d_score else "D"

        area_results.append({
            "area": area,
            "party": party,
            "confidence": round(area_conf, 4),
        })

        if party == "R":
            r_weight += area_conf
        elif party == "D":
            d_weight += area_conf

    if not area_results:
        return {"overall": "bipartisan", "weight": 0.0, "areas": []}

    total_partisan_weight = r_weight + d_weight
    if total_partisan_weight < 0.01:
        return {"overall": "bipartisan", "weight": 0.0, "areas": area_results}

    if r_weight > d_weight:
        overall = "R"
        weight = r_weight / total_partisan_weight
    elif d_weight > r_weight:
        overall = "D"
        weight = d_weight / total_partisan_weight
    else:
        overall = "bipartisan"
        weight = 0.5

    return {
        "overall": overall,
        "weight": round(weight, 4),
        "areas": area_results,
    }


def refine_with_vote_data(
    content_alignment: str,
    vote_alignment: str | None,
) -> str:
    """Combine content-based and vote-based party alignment.

    The actual roll-call split wins whenever it exists. partyLeaning's
    downstream consumer is the voted-with-party computation, and "did
    this member break with their party" is defined by how the parties
    actually voted, not by the bill's inherent ideology. In particular,
    a bill whose content reads partisan but which passed with both party
    majorities ("bipartisan" split) must be excluded from party-loyalty
    counting — the 2026-06 audit found the previous rule (content wins
    over a bipartisan split) marked members of one party as voting
    "against party" on bills nearly everyone supported, which pinned
    House Independent Voting scores at ≈87-89 for all 431 reps.

    Content analysis is used only when no roll-call member data exists.

    Args:
        content_alignment: "R", "D", or "bipartisan" from content analysis
        vote_alignment: "R", "D", "bipartisan", or None from compute_party_split

    Returns:
        Final party alignment: "R", "D", or "bipartisan"
    """
    if vote_alignment is None:
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
    )
    db.execute(stmt)


def analyze_partisan_depth(
    promises: list[dict],
    senator_party: str,
    voting_record: dict | None = None,
    ideology_score: float | None = None,
) -> dict:
    """Analyze a senator's partisan depth from voting record + platform positions.

    Primary signal: the senator's actual votes on bills with known party
    alignment (party_leaning).  Each vote on a D-leaning or R-leaning bill
    is a direct behavioral observation of partisan positioning — far more
    reliable than platform text analysis.

    Secondary signal: campaign promises, analyzed via embedding similarity
    to party platform positions (manifesto analysis approach, Budge et al.
    2001).  These enrich the profile but cannot override the vote signal.

    Tertiary signal: SVD-derived ideology score from cosponsorship patterns
    (Tauberer 2012, adapted from Poole & Rosenthal 1985).  This serves as
    a Bayesian prior — with sparse vote data it has more influence, with
    rich vote data the observations dominate.

    Args:
        promises: List of dicts with at least 'promiseText' and 'category'.
        senator_party: "R", "D", or "I".
        voting_record: Dict with 'keyVotes' and/or 'recentVotes' lists.
        ideology_score: 0.0 (far left) to 1.0 (far right) from SVD on
            cosponsorship matrix, or None if unavailable.

    Returns:
        Dict with:
          overallLean: float from -1.0 (deep D) to +1.0 (deep R)
          overallParty: "R" | "D" | "centrist"
          depth: "deep" | "moderate" | "centrist" | "cross-cutting"
          crossPartyCount: int (positions aligning with opposite party)
          totalPositions: int
          policyBreakdown: list of per-area alignment dicts
    """
    area_alignments = _alignments_from_votes(voting_record or {})

    promise_alignments = _alignments_from_promises(promises or [])
    if promise_alignments:
        area_alignments.extend(promise_alignments)

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
    vote_lean = sum(leans) / len(leans)

    # Bayesian blend: SVD cosponsorship ideology as prior, vote/promise
    # observations as likelihood.  Prior weight decreases as data grows,
    # following the shrinkage pattern used elsewhere in the scoring
    # pipeline (count confidence = min(n/threshold, 1.0)).
    # We count partisan votes (not policy areas) to measure data richness,
    # because 10 votes in one area is strong evidence.
    if ideology_score is not None:
        ideology_lean = (ideology_score - 0.5) * 2.0  # map [0,1] → [-1,+1]
        vr = voting_record or {}
        all_v = (vr.get("keyVotes") or []) + (vr.get("recentVotes") or [])
        partisan_vote_count = sum(
            1 for v in all_v
            if isinstance(v, dict) and v.get("vote") in ("Yea", "Nay")
        )
        data_confidence = min(partisan_vote_count / 15.0, 1.0)
        prior_weight = 1.0 - data_confidence
        overall_lean = data_confidence * vote_lean + prior_weight * ideology_lean
    else:
        overall_lean = vote_lean

    eval_party = senator_party
    if senator_party == "I":
        d_count = sum(1 for a in area_alignments if a["alignment"] == "D")
        r_count = sum(1 for a in area_alignments if a["alignment"] == "R")
        if d_count > r_count:
            eval_party = "D"
        elif r_count > d_count:
            eval_party = "R"

    cross_party = 0
    if eval_party in ("R", "D"):
        opposite = "D" if eval_party == "R" else "R"
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

    # Strength-weighted cross-party ratio: a weak opposite-party signal
    # (e.g. strength 0.19) contributes less than a strong one (0.8+).
    # This prevents reliably partisan senators from being labeled
    # "cross-cutting" due to several barely-opposite positions.
    if area_alignments and eval_party in ("R", "D"):
        opposite = "D" if eval_party == "R" else "R"
        cross_weight = sum(
            a["strength"] for a in area_alignments if a["alignment"] == opposite
        )
        total_weight = sum(a["strength"] for a in area_alignments)
        cross_ratio = cross_weight / total_weight if total_weight > 0 else 0.0
    else:
        cross_ratio = 0.0

    # Depth thresholds calibrated against the observed lean distribution
    # (D: -0.30 to -0.05, R: +0.10 to +0.43) so that known moderates
    # (Collins ~0.10, Murkowski ~0.14) land in "moderate" while clearly
    # partisan senators (0.20+) land in "deep."
    if cross_ratio > 0.3:
        depth = "cross-cutting"
    elif abs_lean > 0.20:
        depth = "deep"
    elif abs_lean > 0.10:
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


def _alignments_from_votes(voting_record: dict) -> list[dict]:
    """Derive per-policy-area partisan alignments from actual votes.

    Uses multi-area bill data when available: each bill may span multiple
    policy areas (e.g. a bill touching HEALTHCARE and TAXES), each with
    its own per-area party alignment.  A senator's Yea/Nay on the bill
    registers as a signal in each area separately, weighted by the area's
    confidence.  This follows Adler & Wilkerson (2012) in treating
    legislation as multi-dimensional.

    Fallback: when `policyAreas` is absent, uses the single `policyArea`
    with the bill's overall `partyLeaning`.

    Votes are weighted: voting FOR a party-leaning bill = +weight for
    that party, voting AGAINST = +weight for the opposing party.
    """
    from collections import defaultdict

    all_votes = (voting_record.get("keyVotes") or []) + (
        voting_record.get("recentVotes") or []
    )
    if not all_votes:
        return []

    area_counts: dict[str, dict[str, float]] = defaultdict(
        lambda: {"d": 0.0, "r": 0.0}
    )

    for v in all_votes:
        if not isinstance(v, dict):
            continue
        vote = v.get("vote", "")
        if vote not in ("Yea", "Nay"):
            continue

        multi_areas = v.get("policyAreas") or []
        if multi_areas and isinstance(multi_areas, list):
            for pa in multi_areas:
                if not isinstance(pa, dict):
                    continue
                area = pa.get("area", "")
                if not area or area == "PROCEDURAL":
                    continue
                area_party = pa.get("party", "")
                if area_party not in ("D", "R"):
                    continue
                conf = pa.get("confidence", 0.5)

                if vote == "Yea":
                    area_counts[area][area_party.lower()] += conf
                else:
                    opposite = "r" if area_party == "D" else "d"
                    area_counts[area][opposite] += conf
        else:
            party_leaning = v.get("partyLeaning") or v.get("party_leaning", "")
            if party_leaning not in ("D", "R"):
                continue
            area = v.get("policyArea") or v.get("policy_area", "")
            if not area or area == "PROCEDURAL":
                continue

            if vote == "Yea":
                area_counts[area][party_leaning.lower()] += 1.0
            else:
                opposite = "r" if party_leaning == "D" else "d"
                area_counts[area][opposite] += 1.0

    alignments: list[dict] = []
    for area, counts in area_counts.items():
        d_ct = counts["d"]
        r_ct = counts["r"]
        total = d_ct + r_ct
        if total < 2:
            continue

        lean = (r_ct - d_ct) / total
        if abs(lean) < 0.10:
            alignment = "bipartisan"
            strength = 0.0
        elif lean > 0:
            alignment = "R"
            strength = min(abs(lean), 1.0)
        else:
            alignment = "D"
            strength = min(abs(lean), 1.0)

        alignments.append({
            "area": area,
            "alignment": alignment,
            "strength": round(strength, 2),
            "lean": round(lean, 4),
        })

    return alignments


def _alignments_from_promises(promises: list[dict]) -> list[dict]:
    """Derive partisan alignments from campaign promise text via embedding similarity."""
    if not promises:
        return []

    _ensure_platform_embeddings()

    from app.pipeline.vector_store import get_embedding_model
    from app.pipeline.analyze.bill_analyzer import classify_policy_area
    model = get_embedding_model()

    alignments: list[dict] = []

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

        r_emb = _platform_cache.r_embeddings.get(policy_area)
        d_emb = _platform_cache.d_embeddings.get(policy_area)

        if r_emb is not None and d_emb is not None:
            r_score = float(np.dot(query_emb, r_emb))
            d_score = float(np.dot(query_emb, d_emb))
        else:
            r_score = float(np.dot(query_emb, _platform_cache.r_aggregate))
            d_score = float(np.dot(query_emb, _platform_cache.d_aggregate))

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

        alignments.append({
            "area": policy_area,
            "alignment": alignment,
            "strength": round(strength, 2),
            "lean": round(margin, 4),
        })

    return alignments


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
