"""Hybrid industry classifier using a tiered strategy.

Classification tiers (in priority order):
1. Learning store lookup (instant, highest confidence)
2. Sentence-transformer embedding cosine similarity against industry
   descriptions (fast, no LLM, generalizes to unseen entities)
3. Returns "OTHER" — the LLM reclassifier in the pipeline handles
   unknowns in batch and feeds results back into the learning store.

The embedding approach is academically grounded: cosine similarity
in a dense embedding space is a standard text classification technique
(cf. sentence-BERT, Reimers & Gurevych 2019). It generalizes far
better than keyword lists because it captures semantic meaning.
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
        "Anthem Cigna Humana UnitedHealth Aetna Blue Cross Blue Shield MetLife Aflac Progressive "
        "Allstate State Farm Geico mutual life benefit plan underwriter indemnity"
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
        "banking investment securities hedge fund private equity venture capital financial services. "
        "Wall Street asset management brokerage wealth management credit lending. "
        "Credit union savings bank savings & loan community bank federal credit union mutual savings. "
        "Goldman Sachs Morgan Stanley JPMorgan Citigroup Bank of America Wells Fargo BlackRock "
        "Fidelity Vanguard Raymond James Chain Bridge"
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
        "AT&T Verizon T-Mobile Comcast Charter"
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
        "Skadden Jones Day Kirkland Latham Sidley Covington Greenberg law office attorneys at law"
    ),
    "LOBBYISTS": "lobbying government relations public affairs advocacy political consulting Akin Gump Brownstein",
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
        "Emily's List Club for Growth campaign services digital strategy voter contact"
    ),
}


def _strip_pac_suffix(name: str) -> str:
    """Remove 'PAC' suffix from org names to improve embedding classification.

    Entity names like 'XYZ CREDIT UNION PAC' get misclassified as POLITICAL
    because the word 'PAC' dominates the embedding. Stripping it lets the
    semantic content ('credit union') drive classification.
    """
    import re
    return re.sub(
        r"\s+(?:PAC|POLITICAL ACTION COMMITTEE|POLITICAL ACTION)\s*$",
        "",
        name.strip(),
        flags=re.IGNORECASE,
    ).strip()


_embeddings_cache: dict[str, np.ndarray] = {}
SIMILARITY_THRESHOLD = 0.30


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


def classify_industry(org_name: str | None) -> str:
    """Classify a single org name using embedding cosine similarity.

    Returns:
        Industry code string, or "OTHER" if below similarity threshold.
    """
    result, _ = classify_industry_with_provenance(org_name)
    return result


def classify_industry_with_provenance(org_name: str | None) -> tuple[str, dict]:
    """Classify and return provenance metadata (top scores, matched anchor).

    Returns:
        (industry_code, metadata_dict) where metadata_dict contains
        top_match, top_score, runner_up, runner_up_score.
    """
    if not org_name or len(org_name.strip()) < 2:
        return "OTHER", {}

    clean_name = _strip_pac_suffix(org_name)
    if len(clean_name.strip()) < 2:
        clean_name = org_name

    from app.pipeline.vector_store import get_embedding_model

    industry_embs = _get_industry_embeddings()
    model = get_embedding_model()

    query_emb = model.encode([clean_name], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    scored: list[tuple[str, float]] = []
    for industry, ind_emb in industry_embs.items():
        score = float(np.dot(query_emb, ind_emb))
        scored.append((industry, score))
    scored.sort(key=lambda x: x[1], reverse=True)

    best_industry, best_score = scored[0] if scored else ("OTHER", 0.0)
    if best_score < SIMILARITY_THRESHOLD:
        best_industry = "OTHER"

    meta: dict = {"top_match": scored[0][0], "top_score": round(scored[0][1], 4)} if scored else {}
    if len(scored) > 1:
        meta["runner_up"] = scored[1][0]
        meta["runner_up_score"] = round(scored[1][1], 4)

    return best_industry, meta


def classify_industries_batch(org_names: list[str]) -> dict[str, str]:
    """Batch-classify org names using embedding cosine similarity.

    Strips 'PAC' suffixes before embedding to improve accuracy.
    Much more efficient than calling classify_industry() per name.
    """
    if not org_names:
        return {}

    results: dict[str, str] = {name: "OTHER" for name in org_names}

    needs_embedding: list[tuple[int, str, str]] = []
    for i, name in enumerate(org_names):
        if not name or len(name.strip()) < 2:
            continue
        clean = _strip_pac_suffix(name)
        if len(clean.strip()) < 2:
            clean = name
        needs_embedding.append((i, name, clean))

    if not needs_embedding:
        return results

    from app.pipeline.vector_store import get_embedding_model

    industry_embs = _get_industry_embeddings()
    ind_keys = list(industry_embs.keys())
    ind_matrix = np.stack([industry_embs[k] for k in ind_keys])

    model = get_embedding_model()
    clean_names = [clean for _, _, clean in needs_embedding]

    _ENCODE_BATCH = 256
    all_embeddings = []
    for start in range(0, len(clean_names), _ENCODE_BATCH):
        batch = clean_names[start : start + _ENCODE_BATCH]
        embs = model.encode(batch, show_progress_bar=False, batch_size=min(64, len(batch)))
        all_embeddings.append(embs)
    query_embs = np.vstack(all_embeddings)
    norms = np.linalg.norm(query_embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    query_embs = query_embs / norms

    scores = query_embs @ ind_matrix.T
    best_indices = np.argmax(scores, axis=1)
    best_scores = scores[np.arange(len(scores)), best_indices]

    for j, (_, name, _) in enumerate(needs_embedding):
        if best_scores[j] > SIMILARITY_THRESHOLD:
            results[name] = ind_keys[best_indices[j]]

    logger.info(
        "Batch classified %d org names (%d matched via embedding)",
        len(org_names),
        int(np.sum(best_scores > SIMILARITY_THRESHOLD)),
    )
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
