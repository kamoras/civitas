"""Hybrid industry classifier using a tiered strategy.

Classification tiers (in priority order):
1. Learning store lookup (instant, highest confidence)
2. Sentence-transformer embedding cosine similarity against industry
   descriptions (fast, no LLM, generalizes to unseen entities)
3. Returns "OTHER" — the kNN reclassifier handles unknowns in batch
   and feeds results back into the learning store.

Academic rationale
------------------
Industry classification of campaign donors is a text classification
task where the "document" is an entity name (e.g. "Lockheed Martin
Employees PAC") and the classes are industry sectors. Cosine similarity
in dense embedding space (Reimers & Gurevych 2019, Sentence-BERT) is
the standard approach for short-text classification where labeled
training data per class is limited (Minaee et al. 2021, "Deep Learning-
Based Text Classification: A Comprehensive Review," ACM Computing
Surveys 54:3).

The industry descriptions serve as class prototypes in a zero-shot
classification setup (Yin, Hay & Roth 2019, "Benchmarking Zero-shot
Text Classification," EMNLP). Each description is a natural-language
definition of the industry enriched with exemplar company names,
providing both semantic coverage and entity-level anchoring.

The tiered strategy follows the computational parsimony principle from
Jurafsky & Martin (2023, "Speech and Language Processing," 3rd ed.):
use the simplest sufficient model at each stage, reserving expensive
methods for the residual.

References
----------
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT. EMNLP 2019.
- Minaee, S. et al. (2021). ACM Computing Surveys, 54(3), 1-40.
- Yin, W., Hay, J. & Roth, D. (2019). Benchmarking Zero-shot Text
  Classification. EMNLP 2019, 3914-3923.
- Jurafsky, D. & Martin, J. H. (2023). Speech and Language
  Processing. 3rd ed. Stanford University.
"""

import logging
import numpy as np
from sqlalchemy.orm import Session

from app.models import LearnedClassification

logger = logging.getLogger(__name__)

INDUSTRY_DESCRIPTIONS: dict[str, str] = {
    "PHARMA": (
        "pharmaceutical drugs biotech medicine vaccines clinical trials drug manufacturing. "
        "Pfizer Merck AbbVie Eli Lilly Johnson & Johnson Novartis Sanofi AstraZeneca Amgen "
        "Gilead Moderna Regeneron biopharmaceutical prescription drugs"
    ),
    "INSURANCE": (
        "insurance underwriting coverage premiums actuarial health insurance property casualty. "
        "Anthem Cigna Humana UnitedHealth Aetna Blue Cross Blue Shield BCBS MetLife Aflac Progressive "
        "Allstate State Farm Geico mutual life benefit plan underwriter indemnity "
        "Hartford Nationwide Liberty Mutual Travelers USAA"
    ),
    "OIL_GAS": (
        "oil gas petroleum drilling fracking pipeline fossil fuel refinery crude natural gas. "
        "Exxon Chevron ConocoPhillips BP Shell Halliburton Koch Marathon Valero exploration "
        "upstream downstream"
    ),
    "DEFENSE": (
        "defense military weapons aerospace missiles contractors armed forces. "
        "Lockheed Raytheon Boeing Northrop Grumman General Dynamics BAE L3Harris Leidos "
        "naval army air force Pentagon"
    ),
    "FINANCE": (
        "banking investment securities hedge private equity venture capital. "
        "Wall Street asset management brokerage wealth management credit lending mortgage. "
        "Credit union savings bank federal credit union mutual savings. "
        "Goldman Sachs Morgan Stanley JPMorgan Citigroup Bank of America Wells Fargo BlackRock "
        "Fidelity Vanguard Raymond James BB&T Truist PNC Capital One TD Bank "
        "financial services fiduciary trading derivatives bonds equities"
    ),
    "REAL_ESTATE": (
        "real estate property housing mortgage realty homebuilder REIT commercial property development. "
        "National Association of Realtors"
    ),
    "TECH": (
        "technology software internet cloud computing artificial intelligence data silicon valley. "
        "Google Alphabet Meta Facebook Apple Microsoft Amazon Oracle Salesforce Intel Nvidia "
        "digital platform cybersecurity"
    ),
    "TELECOM": (
        "telecommunications wireless broadband cable internet service provider cellular network satellite. "
        "AT&T Verizon T-Mobile Comcast Charter Sprint CenturyLink Windstream"
    ),
    "AGRIBUSINESS": (
        "agriculture farming crop livestock dairy grain agribusiness ranching fertilizer seed. "
        "Monsanto Cargill Archer Daniels Deere John Deere Bayer sugar cotton"
    ),
    "ENERGY": (
        "energy utility electric power renewable solar wind nuclear coal generation transmission. "
        "Electric cooperative power company electric co-op. "
        "Duke Energy Dominion Exelon NextEra Southern Company"
    ),
    "CONSTRUCTION": "construction building contractor engineering infrastructure cement architecture development",
    "TRANSPORT": (
        "transportation airline aviation railroad shipping trucking logistics freight maritime. "
        "FedEx UPS Delta United Airlines American Airlines Southwest"
    ),
    "LAWYERS": (
        "law firm attorney legal litigation counsel solicitor legal services LLP PLLC. "
        "Skadden Jones Day Kirkland Latham Sidley Covington Greenberg law office attorneys at law. "
        "Brownstein Hyatt Simpson Thacher DLA Piper Baker McKenzie Hogan Lovells "
        "White & Case Gibson Dunn Sullivan Cromwell Cleary Gottlieb Blank Rome Howrey"
    ),
    "LOBBYISTS": (
        "lobbying government relations public affairs advocacy influence. "
        "Akin Gump Squire Patton Boggs BGR Group Invariant LLC Holland Knight lobbying firm"
    ),
    "GAMBLING": "casino gambling gaming sports betting lottery wagering Las Vegas Sands MGM Wynn Caesars",
    "GUNS": (
        "firearm gun rifle ammunition weapons manufacturer second amendment. "
        "NRA National Rifle Association Gun Owners of America Smith & Wesson Remington"
    ),
    "TOBACCO": "tobacco cigarette vaping e-cigarette nicotine smoking Altria Philip Morris Reynolds JUUL",
    "CRYPTO": "cryptocurrency bitcoin blockchain digital currency decentralized finance web3 Coinbase Binance",
    "PRIVATE_PRISON": "prison corrections incarceration detention correctional facility CoreCivic GEO Group",
    "LABOR_UNIONS": (
        "union labor workers organized labor collective bargaining. "
        "AFL-CIO SEIU Teamsters AFSCME United Auto Workers UAW carpenters electricians plumbers "
        "steelworkers machinists firefighters laborers IBEW teachers union brotherhood"
    ),
    "EDUCATION": "university college school education academic teaching research higher education",
    "MEDIA": "media broadcast television news publishing journalism entertainment studio film",
    "RETAIL": "retail store merchandise consumer goods shopping wholesale distribution",
    "MANUFACTURING": "manufacturing factory production industrial assembly plant fabrication",
    "HEALTHCARE": (
        "hospital health clinic medical healthcare nursing patient care physician. "
        "Medical center health system medical association dental nurses optometric chiropractic podiatry. "
        "Surgical orthopedic anesthesiology radiology dermatology ophthalmology pediatric obstetrics. "
        "AMA HCA Kaiser Permanente Mayo Clinic"
    ),
    "POLITICAL": (
        "political party campaign committee victory fund leadership fund election. "
        "National committee DSCC NRSC Democratic Republican senatorial congressional. "
        "Emily's List Club for Growth campaign services digital strategy voter contact. "
        "action fund victory committee joint fundraising state victory friends of "
        "for senate for congress reelect senate majority PAC super PAC "
        "political action committee grassroots advocacy votes voters ballot"
    ),
}


_PAC_NAMING_PROTOTYPE = (
    "political action committee PAC employees PAC good government fund "
    "political fund voluntary contributions committee"
)

_embeddings_cache: dict[str, np.ndarray] = {}
_pac_naming_emb: np.ndarray | None = None
SIMILARITY_THRESHOLD = 0.30
POLITICAL_MARGIN = 0.06


def clear_industry_embedding_cache() -> None:
    """Clear cached industry embeddings (call between pipeline runs)."""
    _embeddings_cache.clear()


def _get_industry_embeddings() -> dict[str, np.ndarray]:
    """Pre-compute and cache industry description embeddings."""
    if _embeddings_cache:
        return _embeddings_cache

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    for industry, description in INDUSTRY_DESCRIPTIONS.items():
        embedding = model.encode([description], show_progress_bar=False)[0]
        _embeddings_cache[industry] = embedding / np.linalg.norm(embedding)

    logger.info("Computed embeddings for %d industry descriptions", len(_embeddings_cache))
    return _embeddings_cache


def _get_pac_naming_embedding() -> np.ndarray:
    """Cache and return the PAC naming context prototype embedding."""
    global _pac_naming_emb
    if _pac_naming_emb is not None:
        return _pac_naming_emb
    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    emb = model.encode([_PAC_NAMING_PROTOTYPE], show_progress_bar=False)[0]
    _pac_naming_emb = emb / np.linalg.norm(emb)
    return _pac_naming_emb


def _decontextualize_political(
    query_emb: np.ndarray,
    scored: list[tuple[str, float]],
) -> tuple[str, float]:
    """If POLITICAL wins, check whether the entity is an industry-PAC.

    Many entities are named "[Industry] PAC" — the word PAC pulls them
    toward POLITICAL in embedding space. If the entity has high cosine
    similarity to the PAC naming context prototype AND a non-POLITICAL
    runner-up is above threshold and within margin, prefer the runner-up.

    This is a mathematical decontextualization approach: instead of
    regex-stripping the PAC suffix, we detect the PAC naming pattern
    semantically and adjust the ranking accordingly.
    """
    if not scored or scored[0][0] != "POLITICAL":
        return scored[0] if scored else ("OTHER", 0.0)

    political_score = scored[0][1]

    pac_emb = _get_pac_naming_embedding()
    pac_context_score = float(np.dot(query_emb, pac_emb))

    if pac_context_score < 0.35:
        return scored[0]

    for industry, score in scored[1:]:
        if score >= SIMILARITY_THRESHOLD and political_score - score < POLITICAL_MARGIN:
            logger.debug(
                "PAC decontextualization: POLITICAL(%.3f) -> %s(%.3f), pac_ctx=%.3f",
                political_score, industry, score, pac_context_score,
            )
            return industry, score

    return scored[0]


def classify_industry(org_name: str | None) -> str:
    """Classify a single org name using embedding cosine similarity.

    Returns:
        Industry code string, or "OTHER" if below similarity threshold.
    """
    result, _ = classify_industry_with_provenance(org_name)
    return result


def classify_industry_with_provenance(org_name: str | None) -> tuple[str, dict]:
    """Classify and return provenance metadata (top scores, matched anchor).

    Uses embedding cosine similarity against industry description prototypes.
    When POLITICAL wins, applies mathematical decontextualization to detect
    "[Industry] PAC" naming patterns and prefer the true industry.

    Returns:
        (industry_code, metadata_dict) where metadata_dict contains
        top_match, top_score, runner_up, runner_up_score.
    """
    if not org_name or len(org_name.strip()) < 2:
        return "OTHER", {}

    from app.pipeline.vector_store import get_embedding_model

    industry_embs = _get_industry_embeddings()
    model = get_embedding_model()

    query_emb = model.encode([org_name], show_progress_bar=False)[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    scored: list[tuple[str, float]] = []
    for industry, ind_emb in industry_embs.items():
        score = float(np.dot(query_emb, ind_emb))
        scored.append((industry, score))
    scored.sort(key=lambda x: x[1], reverse=True)

    best_industry, best_score = _decontextualize_political(query_emb, scored)
    if best_score < SIMILARITY_THRESHOLD:
        best_industry = "OTHER"

    meta: dict = {"top_match": scored[0][0], "top_score": round(scored[0][1], 4)} if scored else {}
    if len(scored) > 1:
        meta["runner_up"] = scored[1][0]
        meta["runner_up_score"] = round(scored[1][1], 4)

    return best_industry, meta


def classify_industries_batch_scored(org_names: list[str]) -> dict[str, tuple[str, float]]:
    """Batch-classify org names, returning (industry, confidence) pairs.

    Core embedding classifier — all names are encoded in batch against
    industry description embeddings.  Only entries above
    SIMILARITY_THRESHOLD are included in the result; absent names should
    be treated as OTHER.

    Returns:
        Dict mapping original name -> (industry_code, cosine_score).
    """
    if not org_names:
        return {}

    results: dict[str, tuple[str, float]] = {}

    needs_embedding: list[tuple[int, str]] = []
    for i, name in enumerate(org_names):
        if not name or len(name.strip()) < 2:
            continue
        needs_embedding.append((i, name))

    if not needs_embedding:
        return results

    from app.pipeline.vector_store import get_embedding_model

    industry_embs = _get_industry_embeddings()
    ind_keys = list(industry_embs.keys())
    ind_matrix = np.stack([industry_embs[k] for k in ind_keys])
    pac_emb = _get_pac_naming_embedding()

    model = get_embedding_model()
    raw_names = [name for _, name in needs_embedding]

    _ENCODE_BATCH = 256
    all_embeddings = []
    for start in range(0, len(raw_names), _ENCODE_BATCH):
        batch = raw_names[start : start + _ENCODE_BATCH]
        embs = model.encode(batch, show_progress_bar=False, batch_size=min(64, len(batch)))
        all_embeddings.append(embs)
    query_embs = np.vstack(all_embeddings)
    norms = np.linalg.norm(query_embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    query_embs = query_embs / norms

    scores = query_embs @ ind_matrix.T
    pac_ctx_scores = query_embs @ pac_emb

    political_idx = ind_keys.index("POLITICAL") if "POLITICAL" in ind_keys else -1

    for j, (_, name) in enumerate(needs_embedding):
        row_scores = scores[j]
        best_idx = int(np.argmax(row_scores))
        best_score = float(row_scores[best_idx])
        best_industry = ind_keys[best_idx]

        if best_industry == "POLITICAL" and political_idx >= 0 and pac_ctx_scores[j] >= 0.35:
            sorted_idx = np.argsort(row_scores)[::-1]
            for k in sorted_idx[1:]:
                runner_score = float(row_scores[k])
                if runner_score >= SIMILARITY_THRESHOLD and best_score - runner_score < POLITICAL_MARGIN:
                    best_industry = ind_keys[k]
                    best_score = runner_score
                    break

        if best_score > SIMILARITY_THRESHOLD:
            results[name] = (best_industry, best_score)

    logger.info(
        "Batch classified %d org names (%d matched via embedding)",
        len(org_names),
        sum(1 for _ in results),
    )
    return results


def classify_industries_batch(org_names: list[str]) -> dict[str, str]:
    """Batch-classify org names using embedding cosine similarity.

    Convenience wrapper around classify_industries_batch_scored that
    returns plain industry codes (scores discarded).
    """
    scored = classify_industries_batch_scored(org_names)
    results: dict[str, str] = {name: "OTHER" for name in org_names}
    for name, (industry, _score) in scored.items():
        results[name] = industry
    return results


def classify_with_learning(
    org_name: str,
    db_session: Session | None = None,
) -> tuple[str, str]:
    """Classify using the full tiered strategy.

    Returns:
        (industry_code, source) where source is "learned", "embedding", or "unknown"
    """
    if not org_name or len(org_name.strip()) < 2:
        return "OTHER", "unknown"

    normalized = org_name.upper().strip()

    if db_session is not None:
        learned = (
            db_session.query(LearnedClassification)
            .filter(
                LearnedClassification.entity_name == normalized,
                LearnedClassification.entity_type == "industry",
            )
            .first()
        )
        if learned:
            return learned.value, "learned"

    industry, meta = classify_industry_with_provenance(org_name)
    if industry != "OTHER":
        if db_session is not None:
            _store_classification(
                db_session, normalized, "industry", industry, 0.9, "embedding",
                match_metadata=meta,
            )
        return industry, "embedding"

    return "OTHER", "unknown"


def classify_batch_with_learning(
    org_names: list[str],
    db_session: Session | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Batch classify with learning store.

    Returns:
        (results_dict, unknowns_list) where unknowns need LLM classification.
    """
    results: dict[str, str] = {}
    unknowns: list[str] = []

    known_from_db: dict[str, str] = {}
    if db_session is not None:
        unique_names = list({n.upper().strip() for n in org_names if n})
        if unique_names:
            rows = (
                db_session.query(LearnedClassification)
                .filter(
                    LearnedClassification.entity_name.in_(unique_names),
                    LearnedClassification.entity_type == "industry",
                )
                .all()
            )
            known_from_db = {r.entity_name: r.value for r in rows}

    for name in org_names:
        if not name:
            results[name] = "OTHER"
            continue

        normalized = name.upper().strip()

        if normalized in known_from_db:
            results[name] = known_from_db[normalized]
            continue

        industry = classify_industry(name)
        results[name] = industry

        if industry != "OTHER":
            if db_session is not None:
                _store_classification(db_session, normalized, "industry", industry, 0.9, "embedding")
        else:
            unknowns.append(name)

    return results, unknowns


def store_llm_classifications(
    classifications: dict[str, str],
    db_session: Session,
) -> None:
    """Store LLM-derived classifications in the learning store.

    Called after the LLM reclassifier processes unknowns.
    Next pipeline run will find these via the learning store (tier 1).
    """
    for name, industry in classifications.items():
        normalized = name.upper().strip()
        _store_classification(db_session, normalized, "industry", industry, 0.7, "llm")
    db_session.flush()


def _store_classification(
    db_session: Session,
    entity_name: str,
    entity_type: str,
    value: str,
    confidence: float,
    source: str,
    match_metadata: dict | None = None,
) -> None:
    """Upsert a classification into the learning store with provenance."""
    import json
    from datetime import datetime
    from app.pipeline.vector_store import get_model_version

    meta_json = json.dumps(match_metadata) if match_metadata else None

    existing = (
        db_session.query(LearnedClassification)
        .filter(
            LearnedClassification.entity_name == entity_name,
            LearnedClassification.entity_type == entity_type,
        )
        .first()
    )
    if existing:
        if confidence >= existing.confidence:
            existing.value = value
            existing.confidence = confidence
            existing.source = source
            existing.model_version = get_model_version() if source in ("embedding", "nn") else None
            existing.match_metadata = meta_json
            existing.learned_at = datetime.utcnow()
    else:
        db_session.add(LearnedClassification(
            entity_name=entity_name,
            entity_type=entity_type,
            value=value,
            confidence=confidence,
            source=source,
            model_version=get_model_version() if source in ("embedding", "nn") else None,
            match_metadata=meta_json,
        ))
    db_session.commit()
